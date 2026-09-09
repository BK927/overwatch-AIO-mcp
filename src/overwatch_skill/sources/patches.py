"""Official Blizzard server-rendered patch notes, with bounded archive traversal."""

import re
from datetime import UTC, date, datetime

from bs4 import BeautifulSoup

from overwatch_skill.models import SourceError, SourceResult
from overwatch_skill.sources.owreplays import slug

BASE_URL = "https://overwatch.blizzard.com"


class PatchAdapter:
    name = "blizzard_patches"
    capabilities = ("patch_notes", "hero_changes", "replay_compatibility")

    def __init__(self, client):
        self.client = client

    def _error(self, message, code="PARSE_ERROR", url=None):
        return SourceError(code, message, self.name, url or BASE_URL)

    def parse(self, html: str, source_url: str, hero: str | None = None) -> list[dict]:
        soup = BeautifulSoup(html, "html.parser")
        body = soup.select_one(".PatchNotes-body")
        if body is None:
            raise self._error("Blizzard patch-note container is missing.", url=source_url)
        if not body.select(".PatchNotes-patch") and body.get_text(" ", strip=True):
            empty_marker = body.select_one(".patch-notes-error")
            if (
                empty_marker is None
                or empty_marker.get_text(" ", strip=True) != "No Patch Notes Found"
            ):
                raise self._error(
                    "Blizzard returned an unrecognized patch-body response.", url=source_url
                )
        records = []
        for patch in body.select(".PatchNotes-patch"):
            anchor = patch.select_one('[id^="patch-"]')
            title_node = patch.select_one(".PatchNotes-patchTitle")
            if anchor is None or title_node is None:
                raise self._error("Blizzard patch date or title structure changed.", url=source_url)
            raw_date = str(anchor.get("id", ""))[6:]
            try:
                patch_date = date.fromisoformat(raw_date).isoformat()
            except ValueError as exc:
                raise self._error("Blizzard patch date is malformed.", url=source_url) from exc
            title = title_node.get_text(" ", strip=True)
            hero_changes = []
            context = []
            for update in patch.select(".PatchNotes-sectionTitle, .PatchNotesHeroUpdate"):
                if "PatchNotes-sectionTitle" in update.get("class", []):
                    heading = update.get_text(" ", strip=True)
                    context = (
                        [*context[:1], heading]
                        if heading in {"Tank", "Damage", "Support"}
                        else [heading]
                    )
                    continue
                name = update.select_one(".PatchNotesHeroUpdate-name")
                content = update.select_one(".PatchNotesHeroUpdate-body")
                if name is None or content is None:
                    raise self._error("Blizzard hero update structure changed.", url=source_url)
                hero_changes.append(
                    {
                        "hero": slug(name.get_text(" ", strip=True)),
                        "description": content.get_text(" ", strip=True),
                        "context_headings": list(context),
                    }
                )
            matching = [
                change for change in hero_changes if hero is None or change["hero"] == slug(hero)
            ]
            if hero and not matching:
                continue
            notices = list(
                dict.fromkeys(
                    node.get_text(" ", strip=True)
                    for node in patch.select("p, li")
                    if re.search(r"\breplay\s+codes?\b", node.get_text(" ", strip=True), re.I)
                )
            )
            invalidated = any(
                re.search(
                    r"\b(?:have been|were|are) (?:wiped|invalidated|reset)\b|\bno longer (?:available|valid|compatible)\b",
                    notice,
                    re.I,
                )
                for notice in notices
            )
            preserved = any(
                re.search(
                    r"\b(?:still (?:available|valid|compatible)|remain (?:available|valid|compatible))\b",
                    notice,
                    re.I,
                )
                for notice in notices
            )
            effect = (
                "invalidated"
                if invalidated and not preserved
                else "preserved"
                if preserved and not invalidated
                else "unknown"
            )
            version = re.search(r"\b\d+\.\d+\.\d+(?:\.\d+)?\b", title)
            clean_url = (
                source_url.split("/news/")[0]
                + f"/news/patch-notes/live/{raw_date[:4]}/{int(raw_date[5:7])}/#patch-{patch_date}"
            )
            records.append(
                {
                    "patch_date": patch_date,
                    "game_version": version.group() if version else None,
                    "title": title,
                    "hero": slug(hero) if hero else None,
                    "change_type": "hero_update" if hero else "patch_notes",
                    "description": "\n".join(
                        (
                            " / ".join(change["context_headings"]) + ": "
                            if change["context_headings"]
                            else ""
                        )
                        + change["description"]
                        for change in matching
                    )
                    if hero
                    else patch.get_text(" ", strip=True),
                    "hero_changes": matching,
                    "replay_compatibility": {"effect": effect, "notices": notices},
                    "source": self.name,
                    "source_url": clean_url,
                }
            )
        return sorted(records, key=lambda item: item["patch_date"], reverse=True)

    async def fetch(self, filters: dict) -> SourceResult:
        requested = dict(filters)
        allowed = {"hero", "after", "before", "limit", "view", "max_months", "locale"}
        unsupported = [
            key for key, value in filters.items() if key not in allowed and value is not None
        ]
        if unsupported:
            raise self._error(
                f"Unsupported patch filters: {', '.join(unsupported)}", "UNSUPPORTED_FILTER"
            )
        locale = filters.get("locale", "en-us")
        if locale != "en-us":
            raise self._error(
                "Patch parsing currently supports the verified en-us page structure.",
                "UNSUPPORTED_FILTER",
            )
        view = filters.get("view", "search")
        if view not in ("latest", "search", "history"):
            raise self._error(
                "Patch view must be latest, search, or history.", "UNSUPPORTED_FILTER"
            )
        try:
            today = datetime.now(UTC).date()
            before = date.fromisoformat(filters["before"]) if filters.get("before") else today
            after = date.fromisoformat(filters["after"]) if filters.get("after") else None
            limit = 1 if view == "latest" else int(filters.get("limit", 20))
            max_months = int(filters.get("max_months", 12))
            if not 1 <= limit <= 100 or not 1 <= max_months <= 24 or (after and after > before):
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise self._error(
                "Use ISO dates, after <= before, limit 1–100, and max_months 1–24.",
                "INVALID_ARGUMENT",
            ) from exc
        start = min(before, today).replace(day=1)
        month = start
        records, warnings, fetched_results = [], [], []
        checked_months = []
        for _ in range(max_months):
            if month < date(2016, 5, 1) or (
                after and (month.year, month.month) < (after.year, after.month)
            ):
                break
            url = f"{BASE_URL}/{locale}/news/patch-body/live/{month.year}/{month.month}"
            fetched = await self.client.get_text(
                self.name, url, ttl=21600, headers={"X-Requested-With": "XMLHttpRequest"}
            )
            fetched_results.append(fetched)
            checked_months.append(month.strftime("%Y-%m"))
            parsed = self.parse(fetched.data, url, filters.get("hero"))
            if any(item["patch_date"][:7] != month.strftime("%Y-%m") for item in parsed):
                raise self._error(
                    "Blizzard returned patch notes for a different requested month.", url=url
                )
            records.extend(
                item
                for item in parsed
                if item["patch_date"] <= before.isoformat()
                and (after is None or item["patch_date"] >= after.isoformat())
            )
            month = (
                date(month.year - 1, 12, 1)
                if month.month == 1
                else date(month.year, month.month - 1, 1)
            )
            if len(records) >= limit:
                break
        if not fetched_results:
            raise self._error(
                "Requested patch range predates the official archive.", "UNSUPPORTED_FILTER"
            )
        boundary_reached = month < date(2016, 5, 1) or (
            after is not None and (month.year, month.month) < (after.year, after.month)
        )
        if not boundary_reached and view != "latest":
            warnings.append(
                f"Patch search covers {checked_months[-1]} through {checked_months[0]}; older matching notes may exist. Use before to continue."
            )
        if view == "latest" and not records and not boundary_reached:
            warnings.append(
                f"No matching patch found within the {max_months}-month search bound; older notes were not checked."
            )
        last = fetched_results[-1]
        stale = any(item.stale for item in fetched_results)
        if stale:
            warnings.append("Some patch pages were returned from stale cache.")
        records.sort(key=lambda item: item["patch_date"], reverse=True)
        return SourceResult(
            records[:limit],
            self.name,
            f"{BASE_URL}/{locale}/news/patch-notes/",
            retrieved_at=last.retrieved_at,
            requested_filters=requested,
            applied_filters={**requested, "months_checked": checked_months},
            warnings=warnings,
            cached=all(item.cached for item in fetched_results),
            stale=stale,
        )
