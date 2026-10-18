"""OPC UA server of the HCW ASU1 DCS (asyncua).

Address space (namespace urn:haltenberg:hcw:asu1:dcs, string NodeIds "HCW.ASU1.<tag>.<attr>"):

    Objects/
      HCW/                                   enterprise (FolderType)
        Haltenberg/                          site
          ASU1/                              unit "Air Separation Unit 1"
            MainAirCompressor/ ... Energy/   one folder per process block (config.BLOCKS)
              FIC-101/                       one object per DCS tag, Description property
                PV   AnalogItemType, EngineeringUnits + EURange, Historizing
                SP, OP, Mode                 controllers only (SP/OP historized too)
                AlarmState                   tags with alarm limits: NORMAL / H / HH / L / LL
              KS-201/                        PPU switching sequence (in FrontEndPurification)
            Equipment/MAC, EXP, BAC, LOXP,   State, Running (one object per configured equipment)
                      GANC, LOXB, LINB
            Interlocks/I-100, I-300, ...     Tripped, FirstOut (one object per configured interlock)
            Diagnostics/                     FieldIOConnected, ActiveAlarms, UnackedAlarms, ...

Values carry the DCS scan time as SourceTimestamp and Bad_NoCommunication while the field I/O
link is down. HistoryRead (raw) is answered from the DCS's own historian (historian.py) through a
custom history storage — the OPC UA server does not keep a second copy of the history.
Read-only by design for this first version: nothing on the OPC UA side can change the process.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from asyncua import Server, ua
from asyncua.common.instantiate_util import instantiate
from asyncua.server.history import HistoryStorageInterface

import config as C

log = logging.getLogger("dcs.opcua")
logging.getLogger("asyncua").setLevel(logging.ERROR)

NAMESPACE = "urn:haltenberg:hcw:asu1:dcs"
PREFIX = f"{C.ENTERPRISE}.{C.UNIT}"
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

GOOD = ua.StatusCode(ua.StatusCodes.Good)
BAD_COMM = ua.StatusCode(ua.StatusCodes.BadNoCommunication)
# A calculated value that doesn't apply right now (I/O fine), e.g. KPI-701 while no GOX is produced.
BAD_OOS = ua.StatusCode(ua.StatusCodes.BadOutOfService)


def to_dt(ts):
    return EPOCH + timedelta(seconds=ts)


def from_dt(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if dt.year < 1971:          # OPC UA "unspecified" (DateTime.MinValue / 1601 epoch)
        return None
    return (dt - EPOCH).total_seconds()


class HistorianStorage(HistoryStorageInterface):
    """Serves OPC UA HistoryRead (ReadRaw) from the DCS historian's SQLite store."""

    def __init__(self, historian, nodeid_to_point):
        self.historian = historian
        self.map = nodeid_to_point

    async def init(self):
        pass

    async def new_historized_node(self, node_id, period, count=0):
        pass

    async def save_node_value(self, node_id, datavalue):
        pass   # the scan loop writes the historian directly

    async def read_node_history(self, node_id, start, end, nb_values):
        point = self.map.get(node_id)
        if point is None:
            return [], None
        t0, t1 = from_dt(start), from_dt(end)
        reverse = t0 is not None and t1 is not None and t0 > t1
        if reverse:
            t0, t1 = t1, t0
        t0 = 0.0 if t0 is None else t0
        t1 = datetime.now(timezone.utc).timestamp() + 1 if t1 is None else t1
        limit = nb_values or 0
        rows = await asyncio.get_running_loop().run_in_executor(
            None, self.historian.raw, point, t0, t1, (limit + 1) if limit else 0)
        cont = None
        if limit and len(rows) > limit:
            rows = rows[:limit]
            cont = to_dt(rows[-1][0] + 1e-6)
        if reverse:
            rows = rows[::-1]
        dvs = []
        for ts, value, q in rows:
            dt = to_dt(ts)
            dvs.append(ua.DataValue(
                Value=ua.Variant(value if value is not None else 0.0, ua.VariantType.Double),
                StatusCode={0: GOOD, 2: BAD_OOS}.get(q, BAD_COMM), SourceTimestamp=dt, ServerTimestamp=dt))
        return dvs, cont

    async def new_historized_event(self, source_id, evtypes, period, count=0):
        pass

    async def save_event(self, event):
        pass

    async def read_event_history(self, source_id, start, end, nb_values, evfilter):
        return [], None

    async def stop(self):
        pass


class OpcUaServer:
    def __init__(self, historian, port=4840, host="0.0.0.0"):
        self.historian = historian
        self.endpoint = f"opc.tcp://{host}:{port}/hcw/asu1/"
        self.server = Server()
        self.vars = {}          # key (e.g. "FIC-101.PV") -> (nodeid, VariantType)
        self.last = {}

    async def start(self):
        s = self.server
        await s.init()
        s.set_endpoint(self.endpoint)
        s.set_server_name("HCW ASU1 DCS")
        await s.set_application_uri("urn:haltenberg:hcw:asu1:dcs:server")
        s.set_security_policy([ua.SecurityPolicyType.NoSecurity])
        self.idx = await s.register_namespace(NAMESPACE)
        await self._build()
        point_map = {nid: key for key, (nid, vt) in self.vars.items()
                     if key in self.historian.ids and vt == ua.VariantType.Double}
        s.iserver.history_manager.set_storage(HistorianStorage(self.historian, point_map))
        await s.start()
        log.info("OPC UA server on %s (%d variables, %d historized)", self.endpoint, len(self.vars), len(point_map))

    async def stop(self):
        await self.server.stop()

    # ---- address space ----
    def _nid(self, path):
        return ua.NodeId(f"{PREFIX}.{path}", self.idx)

    def _qn(self, name):
        return ua.QualifiedName(name, self.idx)

    async def _folder(self, parent, path, name, desc=None):
        node = await parent.add_folder(self._nid(path) if path else ua.NodeId(name, self.idx), self._qn(name))
        if desc:
            await node.write_attribute(ua.AttributeIds.Description,
                                       ua.DataValue(ua.Variant(ua.LocalizedText(desc), ua.VariantType.LocalizedText)))
        return node

    async def _object(self, parent, path, name, desc):
        node = await parent.add_object(self._nid(path), self._qn(name))
        await node.write_attribute(ua.AttributeIds.Description,
                                   ua.DataValue(ua.Variant(ua.LocalizedText(desc), ua.VariantType.LocalizedText)))
        await node.add_property(self._nid(f"{path}.Description"), self._qn("Description"), desc)
        return node

    async def _var(self, parent, key, name, value, vtype, historize=False):
        node = await parent.add_variable(self._nid(key), self._qn(name), ua.Variant(value, vtype))
        if historize:
            await self._historizing(node)
        self.vars[key] = (node.nodeid, vtype)
        return node

    async def _analog(self, parent, key, name, unit, lo, hi, historize=True):
        nodes = await instantiate(parent, self.server.get_node(ua.ObjectIds.AnalogItemType),
                                  nodeid=self._nid(key), bname=self._qn(name),
                                  dname=ua.LocalizedText(name), instantiate_optional=False)
        node = nodes[0]
        await node.write_attribute(ua.AttributeIds.DataType,
                                   ua.DataValue(ua.Variant(ua.NodeId(ua.ObjectIds.Double), ua.VariantType.NodeId)))
        await node.write_attribute(ua.AttributeIds.ValueRank, ua.DataValue(ua.Variant(-1, ua.VariantType.Int32)))
        await self.server.write_attribute_value(node.nodeid, ua.DataValue(ua.Variant(0.0, ua.VariantType.Double)))
        for child in await node.get_children():
            if (await child.read_browse_name()).Name == "EURange":
                await child.write_value(ua.Variant(ua.Range(Low=float(lo), High=float(hi)), ua.VariantType.ExtensionObject))
        await node.add_property(self._nid(f"{key}.EngineeringUnits"), ua.QualifiedName("EngineeringUnits", 0),
                                ua.Variant(ua.EUInformation(
                                    NamespaceUri="http://www.opcfoundation.org/UA/units/un/cefact", UnitId=-1,
                                    DisplayName=ua.LocalizedText(unit), Description=ua.LocalizedText(unit)),
                                    ua.VariantType.ExtensionObject),
                                datatype=ua.NodeId(ua.ObjectIds.EUInformation))
        self.vars[key] = (node.nodeid, ua.VariantType.Double)
        if historize:
            await self._historizing(node)
        else:
            await self._access(node, ua.AccessLevel.CurrentRead)
        return node

    async def _access(self, node, *levels):
        mask = 0
        for lvl in levels:
            mask |= 1 << lvl.value
        dv = ua.DataValue(ua.Variant(mask, ua.VariantType.Byte))
        await node.write_attribute(ua.AttributeIds.AccessLevel, dv)
        await node.write_attribute(ua.AttributeIds.UserAccessLevel, dv)

    async def _historizing(self, node):
        await self._access(node, ua.AccessLevel.CurrentRead, ua.AccessLevel.HistoryRead)
        await node.write_attribute(ua.AttributeIds.Historizing, ua.DataValue(ua.Variant(True, ua.VariantType.Boolean)))

    async def _build(self):
        objects = self.server.nodes.objects
        ent = await objects.add_folder(ua.NodeId(C.ENTERPRISE, self.idx), self._qn(C.ENTERPRISE))
        await ent.write_attribute(ua.AttributeIds.Description, ua.DataValue(ua.Variant(
            ua.LocalizedText("Haltenberg Chemische Werke"), ua.VariantType.LocalizedText)))
        site = await ent.add_folder(ua.NodeId(f"{C.ENTERPRISE}.{C.SITE}", self.idx), self._qn(C.SITE))
        unit = await site.add_object(ua.NodeId(PREFIX, self.idx), self._qn(C.UNIT))
        await unit.write_attribute(ua.AttributeIds.Description, ua.DataValue(ua.Variant(
            ua.LocalizedText(C.UNIT_LABEL), ua.VariantType.LocalizedText)))

        blocks = {}
        for key, label in C.BLOCKS:
            blocks[key] = await self._folder(unit, key, key, label)
        for t in C.TAGS:
            tag = t["tag"]
            obj = await self._object(blocks[t["block"]], tag, tag, t["desc"])
            await self._analog(obj, f"{tag}.PV", "PV", t["unit"], t["lo"], t["hi"])
            if t["kind"] == "PID":
                await self._analog(obj, f"{tag}.SP", "SP", t["unit"], t["lo"], t["hi"])
                await self._analog(obj, f"{tag}.OP", "OP", "%", 0, 100)
                await self._var(obj, f"{tag}.Mode", "Mode", "AUTO", ua.VariantType.String)
            if t["alarms"]:
                await self._var(obj, f"{tag}.AlarmState", "AlarmState", "NORMAL", ua.VariantType.String)

        ks = await self._object(blocks["FrontEndPurification"], "KS-201", "KS-201", "PPU bed switching sequence")
        for name, val, vt in [("State", "", ua.VariantType.String), ("ActiveBed", "A", ua.VariantType.String),
                              ("Step", "", ua.VariantType.String), ("StepTime", 0.0, ua.VariantType.Double),
                              ("OnlineTime", 0.0, ua.VariantType.Double), ("CycleTime", 0.0, ua.VariantType.Double),
                              ("RegenIncomplete", False, ua.VariantType.Boolean)]:
            await self._var(ks, f"KS-201.{name}", name, val, vt)

        eq_folder = await self._folder(unit, "Equipment", "Equipment", "Rotating equipment run states")
        for key, cfg in C.EQUIPMENT.items():
            obj = await self._object(eq_folder, f"Equipment.{key}", key, cfg["label"])
            await self._var(obj, f"Equipment.{key}.State", "State", "", ua.VariantType.String)
            await self._var(obj, f"Equipment.{key}.Running", "Running", False, ua.VariantType.Boolean)

        il_folder = await self._folder(unit, "Interlocks", "Interlocks", "Safety interlocks / trips")
        for key, cfg in C.INTERLOCKS.items():
            obj = await self._object(il_folder, f"Interlocks.{key}", key, cfg["label"])
            await self._var(obj, f"Interlocks.{key}.Tripped", "Tripped", False, ua.VariantType.Boolean)
            await self._var(obj, f"Interlocks.{key}.FirstOut", "FirstOut", "", ua.VariantType.String)

        diag = await self._folder(unit, "Diagnostics", "Diagnostics", "DCS diagnostics")
        for name, val, vt in [("FieldIOConnected", False, ua.VariantType.Boolean),
                              ("ScanCount", 0, ua.VariantType.UInt32),
                              ("ActiveAlarms", 0, ua.VariantType.Int32),
                              ("UnackedAlarms", 0, ua.VariantType.Int32),
                              ("HistorianSamples", 0, ua.VariantType.UInt64)]:
            await self._var(diag, f"Diagnostics.{name}", name, val, vt)

    # ---- per-scan update ----
    async def update(self, snap, ts, scan_count, historian_samples):
        dt = to_dt(ts)
        io_ok = snap["io_ok"]
        alarm_state = {}
        for a in snap["alarms"]:
            if a["active"] and a["limit"] != "DISC":
                cur = alarm_state.get(a["tag"])
                if cur is None or C.ALARM_PRIORITY[a["limit"]] < C.ALARM_PRIORITY[cur]:
                    alarm_state[a["tag"]] = a["limit"]
        values = {}
        for t in C.TAGS:
            row = snap["tags"][t["tag"]]
            values[f"{t['tag']}.PV"] = row["pv"]
            if t["kind"] == "PID":
                values[f"{t['tag']}.SP"] = row["sp"]
                values[f"{t['tag']}.OP"] = row["op"]
                values[f"{t['tag']}.Mode"] = row["mode"]
            if t["alarms"]:
                values[f"{t['tag']}.AlarmState"] = alarm_state.get(t["tag"], "NORMAL")
        q = snap["sequences"]["KS-201"]
        values.update({"KS-201.State": q["display"], "KS-201.ActiveBed": q["online"], "KS-201.Step": q["step"],
                       "KS-201.StepTime": float(q["step_time"]), "KS-201.OnlineTime": float(q["online_time"]),
                       "KS-201.CycleTime": float(q["cycle"]), "KS-201.RegenIncomplete": q["regen_incomplete"]})
        for key, e in snap["equipment"].items():
            values[f"Equipment.{key}.State"] = e["state"]
            values[f"Equipment.{key}.Running"] = e["state"] == "RUNNING"
        for key, il in snap["interlocks"].items():
            values[f"Interlocks.{key}.Tripped"] = il["latched"]
            values[f"Interlocks.{key}.FirstOut"] = il["first_out"] or ""
        values.update({"Diagnostics.FieldIOConnected": io_ok, "Diagnostics.ScanCount": scan_count,
                       "Diagnostics.ActiveAlarms": sum(1 for a in snap["alarms"] if a["active"]),
                       "Diagnostics.UnackedAlarms": sum(1 for a in snap["alarms"] if not a["acked"]),
                       "Diagnostics.HistorianSamples": historian_samples})

        process_keys = {f"{t['tag']}.{a}" for t in C.TAGS for a in ("PV", "SP", "OP")}
        for key, value in values.items():
            nid, vtype = self.vars[key]
            # No I/O: BadNoCommunication. I/O fine but no value: a calculation that doesn't apply now.
            status = BAD_COMM if key in process_keys and not io_ok else BAD_OOS if value is None else GOOD
            bad = status != GOOD
            if value is None:
                value = self.last.get(key, 0.0 if vtype == ua.VariantType.Double else value)
                if value is None:
                    continue
            if vtype == ua.VariantType.Double:
                value = float(value)
            prev = self.last.get(key)
            if prev == value and not bad and key not in process_keys:
                continue      # discrete/state values: only on change
            self.last[key] = value
            await self.server.write_attribute_value(nid, ua.DataValue(
                Value=ua.Variant(value, vtype), StatusCode=status,
                SourceTimestamp=dt, ServerTimestamp=datetime.now(timezone.utc)))
