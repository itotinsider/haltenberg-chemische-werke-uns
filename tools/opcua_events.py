"""A minimal OPC UA alarm & event viewer: the no-install alternative to UaExpert's Event View.

Subscribes to the events of one notifier (default: the Server object, which sees everything), asks for
a ConditionRefresh so the alarms active right now come first, and prints every event. With --history it
reads the event history of the last N minutes instead (HistoryRead, ReadEventDetails).

Runs anywhere asyncua is installed, e.g. in the DCS image on the compose network:

    docker run --rm -it --network <project>_default -v "$PWD/tools":/t --entrypoint python \\
        hcw/asu1-dcs:latest /t/opcua_events.py opc.tcp://dcs:4840/hcw/asu1/
    ... /t/opcua_events.py opc.tcp://dcs:4840/hcw/asu1/ --history 60
    ... /t/opcua_events.py opc.tcp://dcs-us:4840/PortArden/ASU7100 --node "ns=2;s=PTA_ASU7100"

Read-only: it never acknowledges anything.
"""

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

from asyncua import Client, ua

FIELDS = ["Time", "Severity", "SourceName", "ConditionName", "Message", "ActiveState/Id", "AckedState/Id",
          "LimitState/CurrentState", "Retain", "EventType"]


def line(fields):
    f = dict(zip(FIELDS, fields))
    t = f["Time"].strftime("%H:%M:%S") if f["Time"] else "--:--:--"
    etype = ua.ObjectIdNames.get(f["EventType"].Identifier, str(f["EventType"])) if f["EventType"] else "?"
    state = ""
    if f["ActiveState/Id"] is not None:
        state = ("ACTIVE " if f["ActiveState/Id"] else "inactive ")
        state += "" if f["AckedState/Id"] is None else ("acked" if f["AckedState/Id"] else "UNACKED")
    limit = f["LimitState/CurrentState"].Text if f["LimitState/CurrentState"] else ""
    msg = f["Message"].Text if f["Message"] else ""
    return (f"{t} {f['Severity'] or 0:>4} {etype:<26} {f['SourceName'] or '':<16} "
            f"{f['ConditionName'] or '':<16} {state:<17} {limit:<9} {msg}")


# The event type each field is declared on (Part 9), as a client's select clause names it.
FIELD_TYPE = {"ConditionName": "ConditionType", "Retain": "ConditionType", "AckedState/Id": "AcknowledgeableConditionType",
              "ActiveState/Id": "AlarmConditionType", "LimitState/CurrentState": "ExclusiveLimitAlarmType"}


def select_clauses():
    out = []
    for name in FIELDS:
        op = ua.SimpleAttributeOperand()
        op.TypeDefinitionId = ua.NodeId(getattr(ua.ObjectIds, FIELD_TYPE.get(name, "BaseEventType")))
        op.BrowsePath = [ua.QualifiedName(p, 0) for p in name.split("/")]
        op.AttributeId = ua.AttributeIds.Value
        out.append(op)
    return out


class Handler:
    def event_notification(self, event):
        print(line([getattr(event, n, None) for n in FIELDS]), flush=True)


async def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("url")
    ap.add_argument("--node", default="i=2253", help="notifier to subscribe to (default: the Server object)")
    ap.add_argument("--history", type=float, metavar="MIN", help="read the event history of the last MIN minutes")
    ap.add_argument("--seconds", type=float, default=0, help="stop after this many seconds (0 = run until Ctrl-C)")
    args = ap.parse_args()
    async with Client(args.url, timeout=10) as client:
        node = client.get_node(args.node)
        evfilter = ua.EventFilter(SelectClauses=select_clauses())
        print(f"{'time':8} {'sev':>4} {'event type':<26} {'source':<16} {'condition':<16} {'state':<17} "
              f"{'limit':<9} message")
        if args.history:
            end = datetime.now(timezone.utc)
            details = ua.ReadEventDetails(StartTime=end - timedelta(minutes=args.history), EndTime=end,
                                          NumValuesPerNode=0, Filter=evfilter)
            res = await client.uaclient.history_read(ua.HistoryReadParameters(
                HistoryReadDetails=details, TimestampsToReturn=ua.TimestampsToReturn.Both,
                NodesToRead=[ua.HistoryReadValueId(NodeId=node.nodeid)]))
            for ev in res[0].HistoryData.Events:
                print(line([v.Value for v in ev.EventFields]))
            return
        sub = await client.create_subscription(200, Handler())
        await sub._subscribe(node, ua.AttributeIds.EventNotifier, evfilter)
        print("-- ConditionRefresh: the alarms that are active or unacknowledged now --")
        await client.get_node(ua.ObjectIds.ConditionType).call_method(
            ua.NodeId(ua.ObjectIds.ConditionType_ConditionRefresh), ua.Variant(sub.subscription_id, ua.VariantType.UInt32))
        if args.seconds:
            await asyncio.sleep(args.seconds)
        else:
            await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
