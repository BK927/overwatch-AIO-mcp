"""Opt-in, durable due-job scheduling with an SQLite lease and bounded job lists."""

import asyncio
import hashlib
import json
import time

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .db import Repository
from .db.repository import dumps
from .requests import REQUESTS
from .service import Service


class CollectionJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    tool: str
    arguments: dict = Field(default_factory=dict)
    interval_seconds: int = Field(ge=60, le=604800)

    @model_validator(mode="after")
    def check_tool(self):
        if self.tool not in {
            "ow_catalog",
            "ow_meta",
            "ow_player_get",
            "ow_replays_search",
            "ow_patches",
            "ow_esports",
        }:
            raise ValueError("Collection supports bounded public source queries only")
        REQUESTS[self.tool].model_validate(self.arguments)
        if self.tool == "ow_meta" and self.arguments.get("view") == "history":
            raise ValueError("History reads do not collect new data")
        return self


class CollectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    jobs: list[CollectionJob] = Field(max_length=32)

    @model_validator(mode="after")
    def unique_names(self):
        if len({j.name for j in self.jobs}) != len(self.jobs):
            raise ValueError("Collection job names must be unique")
        return self


class Collector:
    def __init__(self, service: Service, config: dict):
        self.service = service
        self.config = CollectionConfig.model_validate(config)

    async def collect_due(self, now: float | None = None) -> list[dict]:
        reports = []
        for job in self.config.jobs:
            current = time.time() if now is None else now
            key = hashlib.sha256(dumps(job.model_dump()).encode()).hexdigest()
            with self.service.repo.transaction() as db:
                db.execute(
                    "INSERT OR IGNORE INTO collection_jobs(job_key,name) VALUES (?,?)",
                    (key, job.name),
                )
                claimed = db.execute(
                    "UPDATE collection_jobs SET lease_until=? WHERE job_key=? AND next_run<=? AND lease_until<=?",
                    (current + 1800, key, current, current),
                ).rowcount
            if not claimed:
                continue
            try:
                async with asyncio.timeout(300):
                    result = await self.service.call(job.tool, job.arguments)
                status = result["status"]
                report = {
                    "job": job.name,
                    "tool": job.tool,
                    "status": status,
                    "error": result.get("error"),
                }
            except TimeoutError:
                status = "error"
                report = {
                    "job": job.name,
                    "tool": job.tool,
                    "status": status,
                    "error": {
                        "code": "SOURCE_UNAVAILABLE",
                        "message": "Collection job exceeded its five-minute limit.",
                    },
                }
            # Save a due time even on failure to avoid a tight retry loop; other jobs still run.
            with self.service.repo.transaction() as db:
                db.execute(
                    "UPDATE collection_jobs SET next_run=?,lease_until=0,last_status=?,last_run=? WHERE job_key=?",
                    (current + job.interval_seconds, status, current, key),
                )
            reports.append(report)
        return reports


async def run_collector(db_path: str, config: dict, once: bool = False) -> int:
    service = Service(Repository(db_path))
    try:
        collector = Collector(service, config)
        while True:
            reports = await collector.collect_due()
            if reports:
                print(json.dumps(reports, ensure_ascii=False), flush=True)
            if once:
                return 1 if any(report["status"] == "error" for report in reports) else 0
            await asyncio.sleep(30)
    finally:
        await service.close()
