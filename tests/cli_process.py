"""Hermetic upstream fixtures for testing the real CLI in a separate interpreter."""

import logging
import sys

import httpx2 as httpx
from test_overfast import FakeClient
from test_service import meta_result

from overwatch_skill import cli
from overwatch_skill.http import HttpClient
from overwatch_skill.models import SourceError
from overwatch_skill.service import Service
from overwatch_skill.sources.overfast import OverFastAdapter

scenario = sys.argv[1]


def fixture_service(repository):
    status = 503 if scenario == "unavailable" else 429 if scenario == "limited" else 200
    payload = {"is_public": False} if scenario == "private" else {}
    service = Service(
        repository,
        HttpClient(
            repository,
            transport=httpx.MockTransport(lambda req: httpx.Response(status, json=payload)),
            min_interval=0,
            retries=0,
        ),
    )
    if scenario == "korean":
        service.overfast = OverFastAdapter(FakeClient([{"key": "reinhardt", "name": "라인하르트"}]))
    if scenario in ("empty", "stale", "partial", "failed_comparison"):

        async def meta(filters):
            if (
                scenario == "failed_comparison"
                or scenario == "partial"
                and filters["map"] == "ilios"
            ):
                raise SourceError("SOURCE_UNAVAILABLE", "fixture offline", "overfast")
            return meta_result(stale=scenario == "stale", data=[] if scenario == "empty" else None)

        service.overfast.meta = meta
    if scenario in ("invalid_output", "exception", "log"):
        original = service.call

        async def call(name, arguments):
            if scenario == "exception":
                raise RuntimeError("private-debug-value")
            if scenario == "invalid_output":
                return {"status": "ok", "data": "broken", "private-debug-value": 1}
            logging.warning("fixture diagnostic on stderr")
            return await original(name, arguments)

        service.call = call
    return service


cli.Service = fixture_service
raise SystemExit(cli.main(sys.argv[2:]))
