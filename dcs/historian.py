"""Built-in DCS historian: exception-reported time series + event journal, in SQLite.

Storage follows what real process historians do rather than "store every scan":

* Exception (deadband) reporting — a point is stored only when it moved more than its deadband
  (`DEADBAND_PCT` of the tag's span) since the last stored value, or when `MAX_INTERVAL_S` has
  passed (a heartbeat, so a flat line still has samples to prove the point was alive).
* Quality is stored with the value: a sample taken while the field I/O link is down is written
  once as Bad with no value, so trends show a gap rather than a flat-lined "last good" value.
* Retention: rows older than `RETENTION_DAYS` are purged once an hour.

One writer (the scan loop); readers (HTTP threads, OPC UA HistoryRead) open their own
connections — WAL mode lets them read while the scan loop writes.
"""

import logging
import os
import sqlite3
import threading
import time

log = logging.getLogger("dcs.historian")

# Sample quality: GOOD; BAD = no data (field I/O down); NOT_APPLICABLE = the I/O is fine but a
# calculated value has no meaning right now (e.g. specific power while no GOX is produced).
GOOD, BAD, NOT_APPLICABLE = 0, 1, 2
NA = object()     # pass as a value to store() for NOT_APPLICABLE


class Historian:
    def __init__(self, path, points, deadband_pct=0.1, max_interval_s=60.0, retention_days=30.0):
        """`points`: {point_name: (unit, description, span)} — e.g. "FIC-101.PV"."""
        self.path = path
        self.points = points
        self.deadband_pct = deadband_pct
        self.max_interval = max_interval_s
        self.retention_s = retention_days * 86400
        self.last = {}            # point -> (ts, value, quality) last stored
        self.ids = {}
        self.local = threading.local()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        db = self._db()
        db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS points (
                id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, unit TEXT, description TEXT);
            CREATE TABLE IF NOT EXISTS samples (
                point_id INTEGER NOT NULL, ts REAL NOT NULL, value REAL, quality INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS samples_point_ts ON samples (point_id, ts);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, ts REAL NOT NULL, kind TEXT, source TEXT,
                message TEXT, priority INTEGER);
            CREATE INDEX IF NOT EXISTS events_ts ON events (ts);
        """)
        for name, (unit, desc, _) in points.items():
            db.execute("INSERT INTO points (name, unit, description) VALUES (?, ?, ?) "
                       "ON CONFLICT(name) DO UPDATE SET unit = excluded.unit, description = excluded.description",
                       (name, unit, desc))
        db.commit()
        self.ids = dict(db.execute("SELECT name, id FROM points").fetchall())
        self.names = {v: k for k, v in self.ids.items()}
        self.last_purge = 0.0
        self.stored = 0
        log.info("Historian at %s: %d points, deadband %.2f %% of span, heartbeat %ds, retention %g d",
                 path, len(points), deadband_pct, max_interval_s, retention_days)

    def _db(self):
        db = getattr(self.local, "db", None)
        if db is None:
            db = sqlite3.connect(self.path, timeout=10)
            db.execute("PRAGMA busy_timeout=5000")
            self.local.db = db
        return db

    # ---- writing (scan loop only) ----
    def store(self, ts, values, events):
        """`values`: {point: float, None (Bad quality) or NA (not applicable)}."""
        rows = []
        for name, v in values.items():
            pid = self.ids.get(name)
            if pid is None:
                continue
            q = GOOD if v is not None and v is not NA else NOT_APPLICABLE if v is NA else BAD
            if v is NA:
                v = None
            prev = self.last.get(name)
            if prev is not None:
                pts, pv, pq = prev
                if q != GOOD and q == pq:
                    continue
                if q == GOOD and pq == GOOD and ts - pts < self.max_interval:
                    span = self.points[name][2] or 1.0
                    if abs(v - pv) <= span * self.deadband_pct / 100.0:
                        continue
            self.last[name] = (ts, v, q)
            rows.append((pid, ts, v, q))
        db = self._db()
        if rows:
            db.executemany("INSERT INTO samples (point_id, ts, value, quality) VALUES (?, ?, ?, ?)", rows)
            self.stored += len(rows)
        if events:
            db.executemany("INSERT INTO events (ts, kind, source, message, priority) VALUES (?, ?, ?, ?, ?)",
                           [(e["ts"], e["kind"], e["source"], e["message"], e["priority"]) for e in events])
        db.commit()
        if ts - self.last_purge > 3600:
            self.last_purge = ts
            cut = ts - self.retention_s
            n = db.execute("DELETE FROM samples WHERE ts < ?", (cut,)).rowcount
            db.execute("DELETE FROM events WHERE ts < ?", (cut,))
            db.commit()
            if n:
                log.info("Purged %d samples older than retention", n)

    # ---- reading ----
    def raw(self, name, start, end, limit=0):
        """Raw samples [(ts, value, quality)] in [start, end], oldest first."""
        pid = self.ids.get(name)
        if pid is None:
            return []
        sql = "SELECT ts, value, quality FROM samples WHERE point_id = ? AND ts >= ? AND ts <= ? ORDER BY ts"
        args = [pid, start, end]
        if limit:
            sql += " LIMIT ?"
            args.append(limit)
        return self._db().execute(sql, args).fetchall()

    def trend(self, name, start, end, points=600):
        """Downsampled for display: per time bucket min/max/last (keeps spikes visible),
        plus the last stored value before `start` so a slow-moving tag isn't blank at the left."""
        pid = self.ids.get(name)
        if pid is None:
            return []
        db = self._db()
        prior = db.execute("SELECT ts, value FROM samples WHERE point_id = ? AND ts < ? ORDER BY ts DESC LIMIT 1",
                           (pid, start)).fetchone()
        width = max((end - start) / max(points, 1), 1e-3)
        rows = db.execute(
            "SELECT CAST((ts - ?) / ? AS INTEGER) AS b, MIN(value), MAX(value), "
            "MAX(ts), COUNT(value), COUNT(*) FROM samples "
            "WHERE point_id = ? AND ts >= ? AND ts <= ? GROUP BY b ORDER BY b",
            (start, width, pid, start, end)).fetchall()
        out = []
        if prior is not None:
            out.append([start, prior[1], prior[1]])
        for b, lo, hi, last_ts, n_good, n in rows:
            out.append([start + (b + 0.5) * width, lo, hi] if n_good else [start + (b + 0.5) * width, None, None])
        return out

    def events(self, start=None, end=None, limit=500):
        db = self._db()
        end = end or time.time() + 1
        start = start or 0
        rows = db.execute("SELECT ts, kind, source, message, priority FROM events WHERE ts >= ? AND ts <= ? "
                          "ORDER BY ts DESC, id DESC LIMIT ?", (start, end, limit)).fetchall()
        return [dict(ts=r[0], kind=r[1], source=r[2], message=r[3], priority=r[4]) for r in rows]

    def stats(self):
        db = self._db()
        n = db.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        first = db.execute("SELECT MIN(ts) FROM samples").fetchone()[0]
        size = os.path.getsize(self.path) if os.path.exists(self.path) else 0
        return dict(points=len(self.ids), samples=n, first_ts=first, size_bytes=size,
                    deadband_pct=self.deadband_pct, max_interval_s=self.max_interval,
                    retention_days=self.retention_s / 86400)
