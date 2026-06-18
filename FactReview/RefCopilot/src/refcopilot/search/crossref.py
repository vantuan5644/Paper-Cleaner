"""Crossref backend — the official DOI registration agency (150M+ works).

Crossref holds publisher-deposited metadata for the published scholarly record:
journal articles, conference proceedings, books, and standards. It is the
authority for DOIs and published venue/title metadata, complementing arXiv
(preprints), Semantic Scholar, OpenReview, and OpenAlex. No API key is required,
so this backend is always on.

Endpoint priority (first match wins):
  1. /works/{doi}                      — direct lookup by DOI
  2. /works?query.bibliographic={title} — relevance-ranked reference match

Polite pool: Crossref routes requests carrying a ``mailto`` to a separate,
more reliable pool. When ``CROSSREF_MAILTO`` is configured the address is sent
as both the ``mailto`` query param and the ``User-Agent`` header.

Retraction: Crossref encodes retractions on the *retraction notice* (via its
``update-to`` relation pointing at the retracted DOI), not on the retracted
work itself, so we do not infer ``is_retracted`` from base metadata here. The
retraction signal is already unified from OpenAlex / Retraction Watch / arXiv;
the merger ORs ``is_retracted`` across backends, so contributing ``False`` is
harmless.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable, Protocol
from urllib.parse import quote

import httpx

from refcopilot.cache.disk_cache import DiskCache
from refcopilot.models import Backend, ExternalRecord, Reference
from refcopilot.ratelimit.crossref import CrossrefRateLimiter
from refcopilot.ratelimit.semantic_scholar import parse_retry_after
from refcopilot.verify.text_match import title_similarity
from refcopilot.verify.thresholds import SEARCH_RESULT_MIN_TITLE_SIM

logger = logging.getLogger(__name__)


_DEFAULT_BASE = "https://api.crossref.org"
# Trim search payloads to the fields we map. Crossref's ``select`` accepts a
# comma-separated allowlist of members valid for the /works route — ``subtype``
# is notably NOT one of them and triggers a 400, so it's omitted here (the DOI
# route returns the full record, subtype included).
_SELECT_FIELDS = (
    "DOI,title,author,issued,published,published-print,published-online,"
    "container-title,short-container-title,type,URL"
)


class _Response(Protocol):
    status_code: int
    headers: dict[str, str]

    def json(self) -> Any: ...


HttpFn = Callable[[str, dict[str, str], dict[str, str]], _Response]


class CrossrefBackend:
    name = Backend.CROSSREF.value

    def __init__(
        self,
        *,
        mailto: str | None = None,
        base_url: str = _DEFAULT_BASE,
        cache: DiskCache | None = None,
        rate_limiter: CrossrefRateLimiter | None = None,
        http_get: HttpFn | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.mailto = (mailto or "").strip() or None
        self.base_url = base_url.rstrip("/")
        self.cache = cache
        self.rate_limiter = rate_limiter or CrossrefRateLimiter()
        self._http_get = http_get
        self.timeout = timeout
        # Permanent failure (network down, persistent 5xx). Once set, all
        # subsequent lookups short-circuit to []. NOT set by 429 alone.
        self._failed = False
        # True when the most recent ``_get_json`` returned None for a transient
        # reason (429 exhaustion, network error). Used by callers to skip
        # caching empty results that should be retried next run.
        self._last_was_transient = False

    def lookup(self, ref: Reference) -> list[ExternalRecord]:
        if self._failed:
            return []
        if ref.doi:
            rec = self.lookup_by_doi(ref.doi)
            if rec:
                return [rec]
        if ref.title:
            return self.search_by_title(ref.title, year=ref.year, max_results=5)
        return []

    def lookup_by_doi(self, doi: str) -> ExternalRecord | None:
        clean_doi = _normalize_doi(doi)
        if not clean_doi:
            return None
        cache_key = f"doi_{clean_doi.replace('/', '_')}"
        # Encode the DOI for the path but keep the slash that separates the
        # registrant prefix from the suffix — Crossref expects an unencoded
        # ``/`` there (``/works/10.1234/abcd``).
        payload = self._cached_or_fetch(
            cache_key, f"/works/{quote(clean_doi, safe='/')}", {}
        )
        if not payload:
            return None
        message = payload.get("message")
        return _work_to_record(message) if isinstance(message, dict) else None

    def search_by_title(
        self, title: str, *, year: int | None = None, max_results: int = 5
    ) -> list[ExternalRecord]:
        clean = re.sub(r"\s+", " ", title.strip())
        cache_key = f"title_{clean[:80]}_{year or ''}"
        # ``query.bibliographic`` is Crossref's reference-matching field; it is
        # tuned for resolving a citation string to a work and outperforms a
        # plain ``query=`` for our use case.
        payload = self._cached_or_fetch(
            cache_key,
            "/works",
            {
                "query.bibliographic": clean,
                "rows": str(max_results),
                "select": _SELECT_FIELDS,
            },
        )
        if not payload:
            return []
        message = payload.get("message")
        items = message.get("items") if isinstance(message, dict) else None
        if not isinstance(items, list):
            return []
        records: list[ExternalRecord] = []
        for work in items[:max_results]:
            rec = _work_to_record(work)
            if rec is None:
                continue
            # Crossref ranks by relevance, not year; drop candidates whose year
            # is wildly off (allow ±1 for venues spanning calendar years,
            # mirroring OpenAlex / OpenReview).
            if year and rec.year and rec.year not in {year, year - 1, year + 1}:
                continue
            # Require a minimum title-token overlap so topical neighbours that
            # share a few keywords don't masquerade as the cited paper.
            if title_similarity(clean, rec.title) < SEARCH_RESULT_MIN_TITLE_SIM:
                continue
            records.append(rec)
        return records

    def _cached_or_fetch(
        self, cache_key: str, path: str, params: dict[str, str]
    ) -> dict[str, Any] | None:
        """Return the raw API payload (cache hit or fresh fetch).

        Cache stores the unmodified API response so changes to filtering /
        parsing logic don't require cache invalidation. Returns None for
        confirmed-empty (404) and for transient errors; only the former is
        cached.
        """
        if self.cache:
            cached = self.cache.get_api(self.name, cache_key)
            if cached is not None:
                return cached or None
        payload = self._get_json(path, params)
        if payload is None:
            if not self._last_was_transient and self.cache:
                self.cache.set_api(self.name, cache_key, {})
            return None
        if self.cache:
            self.cache.set_api(self.name, cache_key, payload)
        return payload

    def _get_json(self, path: str, params: dict[str, str]) -> Any | None:
        url = f"{self.base_url}{path}"
        full_params = dict(params)
        headers: dict[str, str] = {}
        if self.mailto:
            # Both forms put us in the polite pool; Crossref recommends the
            # mailto in the User-Agent and accepts it as a query param too.
            full_params["mailto"] = self.mailto
            headers["User-Agent"] = f"RefCopilot (mailto:{self.mailto})"

        self._last_was_transient = False
        attempt = 0
        while attempt <= self.rate_limiter.max_retries:
            self.rate_limiter.acquire()
            try:
                if self._http_get is not None:
                    resp = self._http_get(url, full_params, headers)
                else:
                    resp = httpx.get(
                        url, params=full_params, headers=headers, timeout=self.timeout
                    )
            except Exception as exc:
                logger.warning("crossref request failed: %s", exc)
                attempt += 1
                if attempt > self.rate_limiter.max_retries:
                    self._failed = True
                    self._last_was_transient = True
                    return None
                time.sleep(self.rate_limiter.backoff_for_attempt(attempt))
                continue

            if resp.status_code == 200:
                try:
                    return resp.json()
                except Exception as exc:
                    logger.warning("crossref json decode failed: %s", exc)
                    return None

            if resp.status_code == 404:
                return None

            if resp.status_code == 429:
                retry_after = parse_retry_after(resp.headers.get("Retry-After"))
                wait = self.rate_limiter.backoff_for_attempt(
                    attempt, retry_after_seconds=retry_after
                )
                logger.info("crossref 429; sleeping %.2fs", wait)
                time.sleep(wait)
                attempt += 1
                continue

            logger.warning(
                "crossref unexpected status %d for %s", resp.status_code, path
            )
            attempt += 1
            if attempt > self.rate_limiter.max_retries:
                self._failed = True
                self._last_was_transient = True
                return None
            time.sleep(self.rate_limiter.backoff_for_attempt(attempt))

        # Fell out of the loop without returning — only after 429 exhaustion.
        # Treat as transient: don't poison the backend.
        self._last_was_transient = True
        return None


_DOI_URL_PREFIXES = ("https://doi.org/", "http://doi.org/", "doi:")


def _normalize_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    s = doi.strip()
    for prefix in _DOI_URL_PREFIXES:
        if s.lower().startswith(prefix):
            s = s[len(prefix):]
            break
    return s.strip().lower() or None


def _first_str(value: Any) -> str | None:
    """Crossref returns ``title`` / ``container-title`` as arrays of strings."""
    if isinstance(value, list):
        for item in value:
            text = str(item or "").strip()
            if text:
                return text
        return None
    if isinstance(value, str):
        return value.strip() or None
    return None


def _author_names(authors: Any) -> list[str]:
    names: list[str] = []
    if not isinstance(authors, list):
        return names
    for entry in authors:
        if not isinstance(entry, dict):
            continue
        given = str(entry.get("given") or "").strip()
        family = str(entry.get("family") or "").strip()
        if given and family:
            names.append(f"{given} {family}")
        elif family:
            names.append(family)
        else:
            # Organizational / consortium authors carry only ``name``.
            org = str(entry.get("name") or "").strip()
            if org:
                names.append(org)
    return names


# Date-bearing members in Crossref order of preference. ``issued`` is the
# earliest known publication date and the best single "year" signal.
_DATE_FIELDS = ("issued", "published", "published-print", "published-online")


def _year_from_work(work: dict[str, Any]) -> int | None:
    for field in _DATE_FIELDS:
        block = work.get(field)
        if not isinstance(block, dict):
            continue
        parts = block.get("date-parts")
        if (
            isinstance(parts, list)
            and parts
            and isinstance(parts[0], list)
            and parts[0]
            and isinstance(parts[0][0], int)
        ):
            return parts[0][0]
    return None


def _work_to_record(work: Any) -> ExternalRecord | None:
    if not isinstance(work, dict):
        return None

    doi = _normalize_doi(work.get("DOI"))
    title = _first_str(work.get("title"))
    if not doi or not title:
        # A Crossref work is uniquely identified by its DOI; without a title
        # there's nothing to match against either.
        return None

    venue = _first_str(work.get("container-title")) or _first_str(
        work.get("short-container-title")
    )

    url = str(work.get("URL") or "").strip() or f"https://doi.org/{doi}"

    return ExternalRecord(
        backend=Backend.CROSSREF,
        record_id=doi,
        title=title,
        authors=_author_names(work.get("author")),
        year=_year_from_work(work),
        venue=venue,
        publication_venue=venue,
        journal=venue,
        doi=doi,
        url=url,
        raw={"type": str(work.get("type") or ""), "subtype": str(work.get("subtype") or "")},
    )
