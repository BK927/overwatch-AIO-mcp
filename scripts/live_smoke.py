"""Opt-in read-only integration check. No public names or replay contents are persisted."""

import argparse
import asyncio
import json
from pathlib import Path

from overwatch_skill.cli import query
from overwatch_skill.db import Repository
from overwatch_skill.models import utcnow
from overwatch_skill.service import Service


async def check(output: str | None) -> int:
    service = Service(Repository(":memory:"))
    cases = [
        ("catalog", "ow_catalog", {"type": "heroes", "locale": "ko-KR"}),
        (
            "overfast_meta",
            "ow_meta",
            {"heroes": ["reinhardt", "ramattra", "dva"], "region": "ASIA", "tier": "MASTER"},
        ),
        (
            "blizzard_meta",
            "ow_meta",
            {"source": "blizzard", "heroes": ["reinhardt"], "region": "ASIA", "tier": "MASTER"},
        ),
        ("owtics_meta", "ow_meta", {"heroes": ["reinhardt"], "region": "KR", "tier": "MASTER"}),
        (
            "map_comparison",
            "ow_meta",
            {
                "view": "map_comparison",
                "maps": ["kings-row", "eichenwalde"],
                "heroes": ["reinhardt"],
                "region": "ASIA",
                "tier": "MASTER",
            },
        ),
        (
            "stored_history",
            "ow_meta",
            {"view": "history", "heroes": ["reinhardt"], "region": "ASIA", "tier": "MASTER"},
        ),
        ("player_search", "ow_players_search", {"query": "TeKrop-2217", "limit": 1}),
        (
            "player_stats",
            "ow_player_get",
            {"player_id": "TeKrop-2217", "view": "hero", "hero": "reinhardt"},
        ),
        ("empty_registry", "ow_rankers_search", {"country": "KR"}),
        ("replay_search", "ow_replays_search", {"hero": "reinhardt", "platform": "pc", "limit": 1}),
        ("replay_detail", "ow_replay_get", {"code": "8D8VRC"}),
        ("patches", "ow_patches", {"view": "latest"}),
        ("esports", "ow_esports", {"view": "hero_meta", "hero": "reinhardt"}),
        ("status", "ow_status", {}),
    ]
    reports = []
    try:
        for label, tool, arguments in cases:
            result, code = await query(tool, arguments, service=service)
            data = result["data"]
            count = (
                len(data)
                if isinstance(data, list)
                else len(data["records"])
                if isinstance(data, dict) and "records" in data
                else int(data is not None)
            )
            report = {
                "check": label,
                "tool": tool,
                "status": result["status"],
                "exit_code": code,
                "count": count,
                "source": result["source"],
                "source_url": result["source_url"],
                "retrieved_at": result["retrieved_at"],
                "error": result["error"],
                "warnings": result["warnings"],
            }
            reports.append(report)
            print(f"{label}: {report['status']} ({count})", flush=True)
        report = {
            "checked_at": utcnow(),
            "network": "public anonymous GET",
            "database": "isolated in-memory",
            "checks": reports,
        }
        if output:
            Path(output).parent.mkdir(parents=True, exist_ok=True)
            Path(output).write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        return 1 if any(item["status"] in ("error", "partial") for item in reports) else 0
    finally:
        await service.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output")
    arguments = parser.parse_args()
    raise SystemExit(asyncio.run(check(arguments.output)))
