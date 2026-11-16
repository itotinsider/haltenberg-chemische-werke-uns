"""OPC UA Alarms & Conditions (Part 9) and events of the DCS: read-only.

What a client gets, on every site, in the site's own names (native.table() lists the conditions):

    Conditions   one per alarm source, as a DCS gateway presents ISA-18.2 alarms:
                   a tag's H/HH/L/LL limits     ExclusiveLevelAlarmType "LevelAlarm", LimitState = the worst
                                                active limit, High/HighHigh/Low/LowLowLimit in native units
                   an interlock trip            TripAlarmType "TRIP" on the interlock object
                   KS-201 alarms                OffNormalAlarmType "SWITCH_DELAYED", "REGEN_INCOMPLETE"
                   field I/O failure            SystemOffNormalAlarmType "COMM" (SystemConditionClass)
                   standby pump auto-start      OffNormalAlarmType "AUTOSTART" (Port Arden)
                 ActiveState, AckedState (the console's acknowledgement), Retain = active or unacked,
                 Severity from the DCS priority (P1 900, P2 600, P3 300), Quality (BadNoCommunication while
                 the field I/O is down). An event on every state change; ConditionRefresh replays the
                 retained ones.
    Events       the rest of the console's event journal (operator actions, equipment, sequence steps,
                 trip handovers) as BaseEventType, from the object they concern.
    Notifiers    every object on the way from the unit down to an alarm source has EventNotifier
                 SubscribeToEvents|HistoryRead and HasNotifier/HasEventSource references below
                 Objects/Server: subscribe at the Server, the unit, a process block or a single tag.
    History      HistoryRead (ReadEventDetails) on any notifier, answered from the historian's event
                 journal, the same rows as /api/events. Alarm rows come back as their condition's events
                 (ActiveState, no AckedState: the journal records acknowledgements as operator events).

Read-only: Acknowledge, Confirm, Shelve, Enable/Disable are not offered. Alarms are acknowledged on the
operator console only (the "OPC UA writes" roadmap item would add them).

Conditions are not instantiated as browsable nodes (Part 9 allows that); their ConditionId is a NodeId
unique per site. asyncua 2.1 routes an event only to monitored items on its exact emitting node, so this
layer emits each event once per notifier on the source's path (and keeps its own retained set for
ConditionRefresh). It also evaluates OfType in event filters including subtypes, as Part 4 requires
(asyncua compares the exact type), so a client filtering on AlarmConditionType gets the level alarms.
"""

import asyncio
import copy
import uuid
from datetime import datetime, timedelta, timezone

from asyncua import ua
from asyncua.common import event_objects, events as ua_events
from asyncua.common.methods import uamethod
from asyncua.server import monitored_item_service as mis

import config as C
import control
import native
import opcua_server as O

SEVERITY = {1: 900, 2: 600, 3: 300}              # DCS priority -> OPC UA severity (1..1000)
JOURNAL_SEVERITY = {"trip": 700, "equipment": 300, "sequence": 200, "operator": 100}
LIMIT_STATE = {"HH": "HighHigh", "H": "High", "L": "Low", "LL": "LowLow"}
LIMIT_FIELD = {"HH": "HighHighLimit", "H": "HighLimit", "L": "LowLimit", "LL": "LowLowLimit"}
CLASS_NAME = {"ProcessConditionClassType": "Process", "SystemConditionClassType": "System"}
NOTIFIER = [ua.EventNotifier.SubscribeToEvents, ua.EventNotifier.HistoryRead]


# ---- OfType including subtypes (asyncua 2.1 compares the exact EventType) ----
_orig_eval_el = mis.WhereClauseEvaluator._eval_el


def _is_subtype(aspace, t, base):
    seen = set()
    while t is not None and t not in seen:
        if t == base:
            return True
        seen.add(t)
        node = aspace.get(t)
        t = next((r.NodeId for r in (node.references if node else [])
                  if not r.IsForward and r.ReferenceTypeId == ua.NodeId(ua.ObjectIds.HasSubtype)), None)
    return False


def _eval_el(self, index, event):
    el = self.elements[index]
    if el.FilterOperator == ua.FilterOperator.OfType:
        return _is_subtype(self._aspace, event.EventType, self._eval_op(el.FilterOperands[0], event))
    return _orig_eval_el(self, index, event)


mis.WhereClauseEvaluator._eval_el = _eval_el


def _set(ev, name, value):
    if name in ev.data_types:
        setattr(ev, name, value)


def _lt(text):
    return ua.LocalizedText(text)


class Conditions:
    """The A&C / event layer of one OPC UA server (OpcUaServer or NativeOpcUaServer)."""

    def __init__(self, srv):
        self.srv = srv
        self.server = srv.server
        self.tb = native.table()
        self.offset = native.META["ts_offset_s"]
        self.defs = {d[0]: dict(zip(("key", "tag", "limit", "setpoint", "priority", "message"), d))
                     for d in control.alarm_defs()}
        self.state = {}          # condition id -> last emitted state
        self.retained = {}       # condition id -> (event, chain), for ConditionRefresh

    def nid(self, ident):
        return ua.NodeId(ident, self.srv.idx)

    # ---------------------------------------------------------------- address space
    async def build(self):
        objs = {o["path"]: o for o in self.tb.objects}
        sources = list(self.tb.sources.values()) + [objs[c["source"]] for c in self.tb.conditions]
        notifiers = {}                      # path -> object, every object on a source's path
        for o in sources:
            for i in range(1, len(o["path"]) + 1):
                if o["path"][:i] in objs:
                    notifiers[o["path"][:i]] = objs[o["path"][:i]]
        self.notifiers = {p: self.nid(o["id"]) for p, o in notifiers.items()}
        server_node = ua.NodeId(ua.ObjectIds.Server)
        await self.server.get_node(server_node).set_event_notifier(NOTIFIER)
        for path in sorted(notifiers, key=len):
            node = self.server.get_node(self.notifiers[path])
            await node.set_event_notifier(NOTIFIER)
            parent = self.notifiers.get(path[:-1], server_node)
            has_children = any(len(p) > len(path) and p[:len(path)] == path for p in notifiers)
            ref = ua.ObjectIds.HasNotifier if has_children else ua.ObjectIds.HasEventSource
            await self.server.get_node(parent).add_reference(node.nodeid, ref, forward=True, bidirectional=True)
        self.all_notifiers = set(self.notifiers.values()) | {server_node}

        self.templates = {}
        for t in {c["type"] for c in self.tb.conditions} | {"BaseEventType", "RefreshStartEventType",
                                                            "RefreshEndEventType"}:
            ev = await ua_events.get_event_obj_from_type_node(
                self.server.get_node(ua.NodeId(getattr(ua.ObjectIds, t))))
            if isinstance(ev, event_objects.Condition):
                ev.add_property("NodeId", None, ua.VariantType.NodeId)          # the ConditionId
            if t == "ExclusiveLevelAlarmType":
                ev.add_property("LimitState/CurrentState", None, ua.VariantType.LocalizedText)
                ev.add_property("LimitState/CurrentState/Id", None, ua.VariantType.NodeId)
            self.templates[t] = ev

        pv_nodes = {v["src"]: v for v in self.tb.variables if v["src"]}
        self.conds, self.by_alarm = {}, {}
        for c in self.tb.conditions:
            src = objs[c["source"]]
            cond = dict(c, nid=self.nid(c["id"]), src_nid=self.nid(src["id"]), src_name=src["path"][-1],
                        chain=self._chain(c["source"]))
            if c["tag"]:
                pv = pv_nodes[f"{c['tag']}.PV"]
                cond["input"] = self.nid(pv["id"])
                cond["limits"] = {LIMIT_FIELD[d["limit"]]: native.encode(pv, d["setpoint"])
                                  for d in self.defs.values() if d["tag"] == c["tag"] and d["limit"] in LIMIT_FIELD}
            self.conds[c["id"]] = cond
            for key in c["alarms"]:
                self.by_alarm[key] = cond
        self.sources = {name: (self.nid(o["id"]), o["path"][-1], self._chain(o["path"]))
                        for name, o in self.tb.sources.items()}

        isession = self.server.iserver.isession
        for mid in (ua.ObjectIds.ConditionType_ConditionRefresh, ua.ObjectIds.ConditionType_ConditionRefresh2):
            isession.add_method_callback(ua.NodeId(mid), self._refresh_method())

    def _chain(self, path):
        return {ua.NodeId(ua.ObjectIds.Server)} | {nid for p, nid in self.notifiers.items()
                                                   if path[:len(p)] == p}

    # ---------------------------------------------------------------- events
    def _base(self, etype, ts):
        ev = copy.copy(self.templates[etype])
        ev.EventId = ua.Variant(uuid.uuid4().hex.encode(), ua.VariantType.ByteString)
        ev.Time = O.to_dt(ts + self.offset)
        ev.ReceiveTime = datetime.now(timezone.utc) + timedelta(seconds=self.offset)
        return ev

    def condition_event(self, cond, ts, active, acked, limit, quality, message, priority):
        ev = self._base(cond["type"], ts)
        ev.SourceNode, ev.SourceName = cond["src_nid"], cond["src_name"]
        ev.NodeId = cond["nid"]
        ev.ConditionName = cond["name"]
        ev.ConditionClassId = ua.NodeId(getattr(ua.ObjectIds, cond["cls"]))
        ev.ConditionClassName = _lt(CLASS_NAME[cond["cls"]])
        ev.Message = _lt(message)
        ev.Severity = SEVERITY.get(priority, 300)
        _set(ev, "LastSeverity", ev.Severity)
        _set(ev, "EnabledState", _lt("Enabled"))
        _set(ev, "EnabledState/Id", True)
        _set(ev, "ActiveState", _lt("Active" if active else "Inactive"))
        _set(ev, "ActiveState/Id", active)
        if acked is not None:
            _set(ev, "AckedState", _lt("Acknowledged" if acked else "Unacknowledged"))
            _set(ev, "AckedState/Id", acked)
            ev.Retain = active or not acked
        else:
            ev.Retain = False                # history: the journal doesn't say whether it was acknowledged
        _set(ev, "Quality", quality)
        _set(ev, "BranchId", ua.NodeId())
        if cond.get("input"):
            _set(ev, "InputNode", cond["input"])
            for field, value in cond["limits"].items():
                _set(ev, field, float(value))
            if active and limit in LIMIT_STATE:
                _set(ev, "LimitState/CurrentState", _lt(LIMIT_STATE[limit]))
                _set(ev, "LimitState/CurrentState/Id",
                     ua.NodeId(getattr(ua.ObjectIds, f"ExclusiveLimitStateMachineType_{LIMIT_STATE[limit]}")))
        return ev

    def journal_event(self, row):
        """A non-alarm journal row (operator, equipment, sequence, trip) as a BaseEventType event."""
        nid, name, chain = self.sources.get(row["source"]) or self.sources["*"]
        ev = self._base("BaseEventType", row["ts"])
        ev.SourceNode = nid
        ev.SourceName = name if row["source"] in self.sources else row["source"]
        ev.Message = _lt(row["message"])
        ev.Severity = JOURNAL_SEVERITY.get(row["kind"], 100)
        return ev, chain

    async def _emit(self, ev, chain, sub_id=None, mid=None):
        subs = self.server.iserver.subscription_service.subscriptions
        for sid, sub in list(subs.items()):
            if sub_id is not None and sid != sub_id:
                continue
            ms = sub.monitored_item_srv
            for node, mids in list(ms._monitored_events.items()):
                if node not in chain:
                    continue
                for m in list(mids):
                    if mid is None or m == mid:
                        e = copy.copy(ev)
                        e.emitting_node = node
                        await ms._trigger_event(e, m)

    # ---------------------------------------------------------------- per scan
    async def update(self, snap, ts, events=()):
        rows = {a["id"]: a for a in snap["alarms"]}
        io_ok = snap["io_ok"]
        for cid, cond in self.conds.items():
            mine = [rows[k] for k in cond["alarms"] if k in rows]
            active = [r for r in mine if r["active"]]
            acked = all(r["acked"] for r in mine)
            worst = min(active or mine, key=lambda r: (r["priority"], -(r["since"] or 0)), default=None)
            quality = O.BAD_COMM if cond.get("input") and not io_ok else O.GOOD
            st = (bool(active), acked, worst["id"] if worst else None, quality.value)
            prev = self.state.get(cid, (False, True, None, O.GOOD.value))
            if prev == st:
                continue
            self.state[cid] = st
            d = self.defs[worst["id"]] if worst else self.defs[cond["alarms"][0]]
            msg = d["message"] if active else f"RTN {d['message']}"
            # A new (or worse) alarm carries the time the DCS raised it; acks, RTNs, quality: this scan.
            new = active and (not prev[0] or prev[2] != st[2])
            when = worst["since"] if new and worst["since"] else ts
            ev = self.condition_event(cond, when, bool(active), acked, d["limit"], quality, msg, d["priority"])
            if ev.Retain:
                self.retained[cid] = (ev, cond["chain"])
            else:
                self.retained.pop(cid, None)
            await self._emit(ev, cond["chain"])
        for row in events:
            if row["kind"] != "alarm":
                await self._emit(*self.journal_event(row))

    # ---------------------------------------------------------------- ConditionRefresh
    def _refresh_method(self):
        @uamethod
        async def refresh(parent, subscription_id, mid=None):
            subs = self.server.iserver.subscription_service.subscriptions
            if subscription_id not in subs:
                return ua.StatusCode(ua.StatusCodes.BadSubscriptionIdInvalid)
            if mid is not None and mid not in subs[subscription_id].monitored_item_srv._monitored_items:
                return ua.StatusCode(ua.StatusCodes.BadMonitoredItemIdInvalid)
            now = datetime.now(timezone.utc).timestamp()
            for etype in ("RefreshStartEventType", None, "RefreshEndEventType"):
                if etype is None:
                    for ev, chain in list(self.retained.values()):
                        await self._emit(ev, chain, subscription_id, mid)
                    continue
                ev = self._base(etype, now)
                ev.SourceNode, ev.SourceName = ua.NodeId(ua.ObjectIds.Server), "Server"
                ev.Severity = 1
                ev.Message = _lt("ConditionRefresh " + ("start" if etype.startswith("RefreshStart") else "end"))
                await self._emit(ev, self.all_notifiers, subscription_id, mid)
            return None
        return refresh

    # ---------------------------------------------------------------- HistoryRead (events)
    def _alarm_key(self, row):
        """The alarm of a journal row written before rows carried their key: same source tag, and the longest
        alarm text the message contains ("… H" is inside "… HH", so the longest wins)."""
        hits = [d for d in self.defs.values() if d["tag"] == row["source"] and d["message"] in row["message"]]
        return max(hits, key=lambda d: len(d["message"]))["key"] if hits else None

    async def history(self, node_id, start, end, nb_values):
        if node_id not in self.all_notifiers:
            return [], None
        t0, t1 = O.from_dt(start), O.from_dt(end)
        reverse = t0 is not None and t1 is not None and t0 > t1
        if reverse:
            t0, t1 = t1, t0
        t0 = 0.0 if t0 is None else t0
        t1 = datetime.now(timezone.utc).timestamp() + 1 if t1 is None else t1
        rows = await asyncio.get_running_loop().run_in_executor(None, self.srv.historian.journal, t0, t1)
        out = []
        for row in rows:
            key = (row["alarm"] or self._alarm_key(row)) if row["kind"] == "alarm" else None
            cond = self.by_alarm.get(key)
            if cond:
                d = self.defs[key]
                active = row["message"].startswith("ALARM")
                ev = self.condition_event(cond, row["ts"], active, None, d["limit"], O.GOOD,
                                          d["message"] if active else f"RTN {d['message']}", d["priority"])
                chain = cond["chain"]
            else:
                ev, chain = self.journal_event(row)
            if node_id in chain:
                out.append(ev)
        cont = None
        if reverse:
            out = out[::-1]
        if nb_values and len(out) > nb_values:
            out, cont = out[:nb_values], None
            nxt = out[-1].Time - timedelta(seconds=self.offset)
            cont = nxt - timedelta(microseconds=1) if reverse else nxt + timedelta(microseconds=1)
        return out, cont
