"""Bounded asynchronous GETs, raw-response caching, and observable source failures."""

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx2 as httpx

from . import __version__
from .db.repository import Repository, dumps
from .models import SourceError, utcnow


@dataclass
class FetchResult:
    data: Any
    url: str
    retrieved_at: str
    headers: dict[str, str]
    cached: bool = False
    stale: bool = False


class HttpClient:
    def __init__(
        self,
        repository: Repository,
        *,
        transport=None,
        timeout: float = 25,
        min_interval: float = 0.5,
        retries: int = 2,
        max_bytes: int = 12 * 1024 * 1024,
    ):
        self.repository = repository
        self.client = httpx.AsyncClient(
            transport=transport,
            timeout=timeout,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
            headers={
                "User-Agent": f"Overwatch-AIO-Skill/{__version__} (public-data research)",
                "Accept": "application/json,text/html;q=0.9",
            },
        )
        self.min_interval = min_interval
        self.retries = retries
        self.max_bytes = max_bytes
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_request: dict[str, float] = {}
        self._semaphore = asyncio.Semaphore(4)

    async def close(self):
        await self.client.aclose()

    async def get_json(
        self, source: str, url: str, params=None, ttl: float = 3600, headers: dict | None = None
    ) -> FetchResult:
        return await self._get(source, url, params, ttl, "json", headers)

    async def get_text(
        self, source: str, url: str, params=None, ttl: float = 3600, headers: dict | None = None
    ) -> FetchResult:
        return await self._get(source, url, params, ttl, "text", headers)

    def _decode(self, row: dict, kind: str, *, cached: bool, stale: bool = False) -> FetchResult:
        body = row["body"].decode("utf-8-sig")
        value = json.loads(body) if kind == "json" else body
        return FetchResult(
            value, row["url"], row["retrieved_at"], json.loads(row["headers"]), cached, stale
        )

    async def _get(self, source, url, params, ttl, kind, headers):
        key = hashlib.sha256(
            dumps([source, url, params or {}, kind, headers or {}]).encode()
        ).hexdigest()
        async with self._locks.setdefault(source, asyncio.Lock()):
            row = self.repository.cache_get(key)
            if row and row["expires_at"] > time.time():
                return self._decode(row, kind, cached=True)
            failure = None
            for attempt in range(self.retries + 1):
                delay = self.min_interval - (time.monotonic() - self._last_request.get(source, 0))
                if delay > 0:
                    await asyncio.sleep(delay)
                self._last_request[source] = time.monotonic()
                try:
                    async with (
                        self._semaphore,
                        self.client.stream("GET", url, params=params, headers=headers) as response,
                    ):
                        chunks = bytearray()
                        async for chunk in response.aiter_bytes():
                            chunks.extend(chunk)
                            if len(chunks) > self.max_bytes:
                                raise SourceError(
                                    "SOURCE_UNAVAILABLE",
                                    "Source response exceeded the bounded download size.",
                                    source,
                                    str(response.url),
                                )
                        text_body = chunks.decode("utf-8-sig", errors="replace")
                        try:
                            response_body = json.loads(text_body)
                        except ValueError:
                            response_body = text_body[:2000]
                        if response.status_code >= 400:
                            rate_limited = response.status_code == 429 or (
                                response.status_code == 503 and "retry-after" in response.headers
                            )
                            code = (
                                "RATE_LIMITED"
                                if rate_limited
                                else "UNSUPPORTED_FILTER"
                                if response.status_code in (400, 422)
                                else "SOURCE_UNAVAILABLE"
                            )
                            failure = SourceError(
                                code,
                                f"Source returned HTTP {response.status_code}.",
                                source,
                                str(response.url),
                            )
                            failure.http_status = response.status_code
                            failure.response_body = response_body
                            if (
                                response.status_code in (429, 502, 503, 504)
                                and attempt < self.retries
                            ):
                                try:
                                    retry_delay = min(
                                        5.0,
                                        max(
                                            0.0,
                                            float(response.headers.get("retry-after", 2**attempt)),
                                        ),
                                    )
                                except ValueError:
                                    retry_delay = float(2**attempt)
                                await asyncio.sleep(retry_delay)
                                continue
                            raise failure
                        try:
                            data = json.loads(text_body) if kind == "json" else text_body
                        except ValueError as exc:
                            raise SourceError(
                                "PARSE_ERROR",
                                "Expected JSON but source returned another format.",
                                source,
                                str(response.url),
                            ) from exc
                        fetched_at = utcnow()
                        saved_headers = {
                            k: v
                            for k, v in response.headers.items()
                            if k.lower()
                            in (
                                "age",
                                "last-modified",
                                "etag",
                                "date",
                                "cache-control",
                                "content-type",
                                "x-cache-status",
                                "x-cache-ttl",
                            )
                        }
                        self.repository.cache_put(
                            key,
                            source,
                            str(response.url),
                            bytes(chunks),
                            saved_headers,
                            fetched_at,
                            ttl,
                        )
                        self.repository.source_record(source)
                        upstream_stale = saved_headers.get("x-cache-status") == "stale"
                        return FetchResult(
                            data, str(response.url), fetched_at, saved_headers, stale=upstream_stale
                        )
                except httpx.HTTPError as exc:
                    failure = SourceError(
                        "SOURCE_UNAVAILABLE",
                        f"Source connection failed ({type(exc).__name__}).",
                        source,
                        url,
                    )
                    if attempt < self.retries:
                        await asyncio.sleep(2**attempt)
                        continue
                except SourceError as exc:
                    failure = exc
                break
            assert failure is not None
            self.repository.source_record(source, f"{failure.code}: {failure.message}")
            # Never mask bad filters, forbidden/private profiles, or parser errors with cache.
            if (
                row
                and failure.code in ("SOURCE_UNAVAILABLE", "RATE_LIMITED")
                and failure.http_status not in (401, 403, 404)
                and time.time() - row["created_epoch"] <= min(7 * ttl, 7 * 86400)
            ):
                return self._decode(row, kind, cached=True, stale=True)
            raise failure
