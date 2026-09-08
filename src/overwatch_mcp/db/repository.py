"""Small transactional store. Public observations never overwrite manual evidence."""

import hashlib
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..models import SourceResult, utcnow


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def series_key(source: str, filters: dict) -> str:
    # Hero selections and display ordering must not fragment a statistical series.
    region = str(filters.get("source_region") or filters.get("region") or "ASIA").upper()
    if source == "owtics" and region == "KR":
        region = "KOREA"
    context = {
        "platform": str(filters.get("platform") or "pc").lower() if source != "owtics" else None,
        "mode": str(filters.get("mode") or "competitive").lower(),
        "region": region,
        "tier": str(filters.get("tier") or "ALL").upper(),
        "map": filters.get("map") or "all-maps",
        "role": str(filters["role"]).lower() if filters.get("role") else None,
    }
    return hashlib.sha256(dumps([source, context]).encode()).hexdigest()


class Repository:
    def __init__(self, path: str | Path = "data/overwatch.db"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=10000")
        self.conn.executescript(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))

    @contextmanager
    def transaction(self):
        with self._lock, self.conn:
            yield self.conn

    def close(self):
        self.conn.close()

    def cache_get(self, key: str) -> dict | None:
        with self._lock:
            row = self.conn.execute("SELECT * FROM http_cache WHERE cache_key=?", (key,)).fetchone()
        return dict(row) if row else None

    def cache_put(
        self,
        key: str,
        source: str,
        url: str,
        body: bytes,
        headers: dict,
        retrieved_at: str,
        ttl: float,
    ):
        now = time.time()
        with self.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO http_cache VALUES (?,?,?,?,?,?,?,?)",
                (key, source, url, body, dumps(headers), retrieved_at, now + ttl, now),
            )
            db.execute("DELETE FROM http_cache WHERE created_epoch < ?", (now - 7 * 86400,))
            # Bound total retained raw response bytes for a 2 GB Raspberry Pi.
            rows = db.execute(
                "SELECT cache_key,length(body) AS size FROM http_cache ORDER BY created_epoch DESC"
            ).fetchall()
            total = 0
            for index, row in enumerate(rows):
                total += row["size"]
                if total > 64 * 1024 * 1024 or index >= 256:
                    db.execute("DELETE FROM http_cache WHERE cache_key=?", (row["cache_key"],))

    def source_record(self, source: str, error: str | None = None):
        now = utcnow()
        with self.transaction() as db:
            db.execute("INSERT OR IGNORE INTO source_status(source) VALUES (?)", (source,))
            if error:
                db.execute(
                    "UPDATE source_status SET status='degraded',last_attempt=?,last_error=? WHERE source=?",
                    (now, error, source),
                )
            else:
                db.execute(
                    "UPDATE source_status SET status='ok',last_attempt=?,last_success=?,last_error=NULL WHERE source=?",
                    (now, now, source),
                )

    def source_states(self) -> dict:
        with self._lock:
            return {
                row["source"]: dict(row) for row in self.conn.execute("SELECT * FROM source_status")
            }

    def save_meta(self, result: SourceResult):
        key = series_key(result.source, result.applied_filters)
        with self.transaction() as db:
            for row in result.data:
                db.execute(
                    "INSERT OR IGNORE INTO hero_meta_snapshots(series_key,source,hero,filters,payload,retrieved_at,source_updated_at,data_period,data_patch) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        key,
                        result.source,
                        row["hero"],
                        dumps(result.applied_filters),
                        dumps(
                            {**row, "source_url": result.source_url, "warnings": result.warnings}
                        ),
                        result.retrieved_at,
                        result.source_updated_at,
                        result.data_period,
                        result.data_patch,
                    ),
                )

    def meta_history(
        self,
        source: str,
        filters: dict,
        heroes: list[str] | None = None,
        after: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        clauses, parameters = ["series_key=?"], [series_key(source, filters)]
        if heroes:
            clauses.append("hero IN (" + ",".join("?" for _ in heroes) + ")")
            parameters.extend(heroes)
        if after:
            clauses.append("retrieved_at>=?")
            parameters.append(after)
        parameters.append(limit)
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM hero_meta_snapshots WHERE "
                + " AND ".join(clauses)
                + " ORDER BY retrieved_at DESC,id DESC LIMIT ?",
                parameters,
            ).fetchall()
        result = []
        for row in rows:
            if heroes and row["hero"] not in heroes:
                continue
            if after and row["retrieved_at"] < after:
                continue
            result.append(
                {
                    **json.loads(row["payload"]),
                    "source": row["source"],
                    "applied_filters": json.loads(row["filters"]),
                    "retrieved_at": row["retrieved_at"],
                    "source_updated_at": row["source_updated_at"],
                    "data_period": row["data_period"],
                    "data_patch": row["data_patch"],
                }
            )
            if len(result) >= limit:
                break
        return result

    def save_replays(self, records: list[dict]):
        with self.transaction() as db:
            for record in records:
                now = utcnow()
                db.execute(
                    "INSERT INTO replays(code,payload,discovered_at,refreshed_at) VALUES (?,?,?,?) ON CONFLICT(code) DO UPDATE SET payload=excluded.payload,refreshed_at=excluded.refreshed_at",
                    (record["code"], dumps(record), now, now),
                )

    def replay_rows(self, code: str | None = None) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM replays WHERE (? IS NULL OR code=?) ORDER BY refreshed_at DESC",
                (code, code),
            ).fetchall()
            result = []
            for row in rows:
                data = json.loads(row["payload"])
                data.update(
                    player_id=row["player_id"],
                    discovered_at=row["discovered_at"],
                    refreshed_at=row["refreshed_at"],
                )
                data["evidence"] = [
                    dict(e)
                    for e in self.conn.execute(
                        "SELECT * FROM replay_evidence WHERE code=? ORDER BY observed_at DESC",
                        (row["code"],),
                    )
                ]
                validation = self.conn.execute(
                    "SELECT * FROM replay_validations WHERE code=? ORDER BY observed_at DESC,id DESC LIMIT 1",
                    (row["code"],),
                ).fetchone()
                if validation:
                    data["source_replay_status"] = data.get("replay_status")
                    data["validation"] = dict(validation)
                    data["replay_status"] = validation["status"]
                result.append(data)
        return result

    def counts(self) -> dict:
        with self._lock:
            return {
                name: self.conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
                for name in ("players", "replays", "hero_meta_snapshots", "http_cache")
            }
