"""Retrieval-layer tests.

Replaces the per-backend test_b_retrieval/* files. Covers the cache (TTL +
on-disk format wipe) and the multi-source merge (provenance ordering and
the cross-backend retraction OR). Live HTTP backends are exercised through
``RefCopilotPipeline`` integration tests in ``test_verify.py`` with stubbed
backends; we don't re-test their request shapes here.
"""

from __future__ import annotations

import os
import time

from refcopilot.cache.disk_cache import API_CACHE_VERSION, DiskCache
from refcopilot.merge import merge_records
from refcopilot.models import Backend, ExternalRecord, Reference, SourceFormat
from refcopilot.search.crossref import CrossrefBackend, _work_to_record


def test_disk_cache_set_get_and_ttl(tmp_path) -> None:
    c = DiskCache(tmp_path, ttl_days=30)
    c.set_api("semantic_scholar", "doi_10.x_y", {"hello": "world"})
    assert c.get_api("semantic_scholar", "doi_10.x_y") == {"hello": "world"}

    # An entry mtime'd past the TTL must be treated as a miss; otherwise stale
    # backend responses keep contradicting fresh ones.
    path = c._api_path("semantic_scholar", "doi_10.x_y")
    old = time.time() - (40 * 86400)
    os.utime(path, (old, old))
    assert c.get_api("semantic_scholar", "doi_10.x_y") is None


def test_disk_cache_wipes_on_version_mismatch(tmp_path) -> None:
    api_dir = tmp_path / "api_cache"
    (api_dir / "arxiv").mkdir(parents=True)
    stale = api_dir / "arxiv" / "id_old.json"
    stale.write_text('{"old": "format"}', encoding="utf-8")
    (api_dir / ".version").write_text(str(API_CACHE_VERSION - 1), encoding="utf-8")

    DiskCache(tmp_path)  # init re-reads the marker and wipes mismatches

    assert not stale.exists()
    assert (api_dir / ".version").read_text().strip() == str(API_CACHE_VERSION)


def _arxiv(**overrides) -> ExternalRecord:
    base = dict(
        backend=Backend.ARXIV,
        record_id="1706.03762",
        title="Attention Is All You Need (arXiv title)",
        authors=["A. Vaswani"],
        year=2017,
        arxiv_id="1706.03762",
    )
    base.update(overrides)
    return ExternalRecord(**base)


def _s2(**overrides) -> ExternalRecord:
    base = dict(
        backend=Backend.SEMANTIC_SCHOLAR,
        record_id="abc123",
        title="Attention Is All You Need (S2 title)",
        authors=["Vaswani A."],
        year=2017,
        venue="NeurIPS",
        publication_venue="NeurIPS",
        doi="10.5555/3295222.3295349",
        arxiv_id="1706.03762",
    )
    base.update(overrides)
    return ExternalRecord(**base)


def test_merge_uses_arxiv_for_title_authors_and_s2_for_venue_doi() -> None:
    merged = merge_records([_arxiv(), _s2()])
    assert merged is not None
    # arXiv is authoritative for title/authors/year (the canonical paper);
    # S2 wins venue/DOI (the published-record metadata).
    assert merged.title.endswith("(arXiv title)")
    assert merged.authors == ["A. Vaswani"]
    assert merged.provenance["title"] is Backend.ARXIV
    assert merged.venue == "NeurIPS"
    assert merged.doi == "10.5555/3295222.3295349"
    assert merged.provenance["doi"] is Backend.SEMANTIC_SCHOLAR


def test_merge_propagates_retraction_from_any_backend() -> None:
    # Cross-source retraction signal: even when the priority backend (arXiv)
    # says not retracted, an OpenAlex hit with is_retracted must flip the
    # merged record so the retraction guard fires.
    openalex_retracted = ExternalRecord(
        backend=Backend.OPENALEX,
        record_id="W123",
        title="Some retracted paper",
        authors=["X"],
        year=2020,
        doi="10.1109/access.2020.3018326",
        is_retracted=True,
    )
    merged = merge_records([_arxiv(is_retracted=False), openalex_retracted])
    assert merged is not None
    assert merged.is_retracted is True


def test_merge_empty_returns_none() -> None:
    assert merge_records([]) is None


# --------------------------------------------------------------------------
# Crossref backend
# --------------------------------------------------------------------------

_CROSSREF_WORK = {
    "DOI": "10.5555/3295222.3295349",
    "title": ["Attention Is All You Need"],
    "author": [
        {"given": "Ashish", "family": "Vaswani"},
        {"family": "Shazeer"},
        {"name": "The NeurIPS Consortium"},
    ],
    "issued": {"date-parts": [[2017, 6, 12]]},
    "container-title": ["Advances in Neural Information Processing Systems"],
    "type": "proceedings-article",
    "URL": "https://doi.org/10.5555/3295222.3295349",
}


def test_crossref_work_to_record_parses_arrays_authors_and_dates() -> None:
    # Crossref returns title/container-title as arrays, authors as
    # given/family (or org ``name``) objects, and the year nested under
    # ``issued.date-parts`` — the parser must flatten all three.
    rec = _work_to_record(_CROSSREF_WORK)
    assert rec is not None
    assert rec.backend is Backend.CROSSREF
    assert rec.title == "Attention Is All You Need"
    assert rec.authors == ["Ashish Vaswani", "Shazeer", "The NeurIPS Consortium"]
    assert rec.year == 2017
    assert rec.venue == "Advances in Neural Information Processing Systems"
    assert rec.doi == "10.5555/3295222.3295349"
    assert rec.record_id == "10.5555/3295222.3295349"


def test_crossref_work_to_record_requires_doi_and_title() -> None:
    assert _work_to_record({"title": ["No DOI here"]}) is None
    assert _work_to_record({"DOI": "10.1/x"}) is None
    assert _work_to_record("not a dict") is None


class _FakeResp:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self.headers: dict[str, str] = {}
        self._payload = payload

    def json(self):
        return self._payload


def test_crossref_lookup_by_doi_unwraps_message(tmp_path) -> None:
    captured: dict[str, object] = {}

    def fake_get(url, params, headers):
        captured["url"] = url
        captured["params"] = params
        return _FakeResp(200, {"message": _CROSSREF_WORK})

    backend = CrossrefBackend(http_get=fake_get, cache=DiskCache(tmp_path))
    recs = backend.lookup(
        Reference(raw="x", source_format=SourceFormat.BIBTEX, doi="10.5555/3295222.3295349")
    )
    assert len(recs) == 1
    assert recs[0].doi == "10.5555/3295222.3295349"
    # The registrant-prefix slash must survive into the request path.
    assert captured["url"].endswith("/works/10.5555/3295222.3295349")


def test_crossref_search_by_title_unwraps_items_and_filters(tmp_path) -> None:
    def fake_get(url, params, headers):
        # ``query.bibliographic`` is the reference-matching field we rely on.
        assert "query.bibliographic" in params
        return _FakeResp(
            200,
            {
                "message": {
                    "items": [
                        _CROSSREF_WORK,
                        {  # topical neighbour — low title overlap, must be dropped
                            "DOI": "10.1/unrelated",
                            "title": ["A Completely Different Paper About Turtles"],
                            "issued": {"date-parts": [[2017]]},
                        },
                    ]
                }
            },
        )

    backend = CrossrefBackend(http_get=fake_get, cache=DiskCache(tmp_path))
    recs = backend.lookup(
        Reference(
            raw="x",
            source_format=SourceFormat.BIBTEX,
            title="Attention Is All You Need",
            year=2017,
        )
    )
    assert [r.title for r in recs] == ["Attention Is All You Need"]


def test_merge_prefers_crossref_for_doi_and_venue() -> None:
    # Crossref is the DOI authority and the published-venue source, so when it
    # and arXiv both match, the merged record takes title from arXiv but DOI
    # and venue from Crossref.
    crossref = ExternalRecord(
        backend=Backend.CROSSREF,
        record_id="10.5555/abc",
        title="Attention Is All You Need (Crossref title)",
        authors=["Ashish Vaswani"],
        year=2017,
        venue="Advances in Neural Information Processing Systems",
        publication_venue="Advances in Neural Information Processing Systems",
        doi="10.5555/abc",
        url="https://doi.org/10.5555/abc",
    )
    merged = merge_records([_arxiv(), crossref])
    assert merged is not None
    assert merged.title.endswith("(arXiv title)")
    assert merged.doi == "10.5555/abc"
    assert merged.provenance["doi"] is Backend.CROSSREF
    assert merged.venue == "Advances in Neural Information Processing Systems"
    assert merged.provenance["venue"] is Backend.CROSSREF
