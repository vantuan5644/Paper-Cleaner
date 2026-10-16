# Changes from upstream FactReview

This is a **modified** copy of FactReview, vendored into the Paper-Cleaner
repository. It is licensed under the GNU Affero General Public License v3.0,
the same as upstream (see `LICENSE`).

This file records our modifications as required by **AGPL-3.0 section 5(a)**
("you must cause the modified files to carry prominent notices stating that you
changed the files, and giving a relevant date").

## Base revision

Vendored from <https://github.com/DEFENSE-SEU/FactReview> at commit
`ac0f9ecaeca1f8b15de034cdac0a295f03fd3d3a`.

## Modifications

### 2026-06 — Add a Crossref search backend to RefCopilot

Adds Crossref as an additional metadata source for reference verification,
alongside the existing arXiv / Semantic Scholar / OpenReview / OpenAlex
backends, with its own rate limiter.

New files:

- `RefCopilot/src/refcopilot/search/crossref.py` — Crossref lookup backend.
- `RefCopilot/src/refcopilot/ratelimit/crossref.py` — Crossref rate limiter.

Modified files:

- `RefCopilot/src/refcopilot/search/__init__.py` — register the Crossref backend.
- `RefCopilot/src/refcopilot/cli.py` — wire Crossref into the CLI.
- `RefCopilot/src/refcopilot/pipeline.py` — include Crossref in the search stage.
- `RefCopilot/src/refcopilot/merge.py` — merge Crossref-provided fields with provenance.
- `RefCopilot/src/refcopilot/models.py` — model/source additions for Crossref.
- `RefCopilot/src/refcopilot/factreview.py` — FactReview adapter updates.
- `RefCopilot/tests/test_retrieval.py`, `RefCopilot/tests/test_verify.py` — test coverage.
- `RefCopilot/README.md`, `.env.example` — documentation / config for the new backend.

### 2026-10-16 — Stop Crossref fuzzy matches from leaking into suggested BibTeX

Crossref's title search always returns its best guesses. Only a title-overlap
floor of 0.40 stood between those guesses and the merger, so unrelated works
(supplementary files, theses, Research Square / OSF preprints, a different
paper by the same first author) passed through. Their DOI, venue, and sometimes
title/authors then appeared in the suggested BibTeX.

Modified files:

- `RefCopilot/src/refcopilot/search/crossref.py` — a title-search candidate
  must now match the citation on title *and* authors (see the `CROSSREF_*`
  thresholds); survivors are ranked by match quality so the merger, which keeps
  the first Crossref record, gets the best one. `component` / `peer-review` /
  `grant` records are dropped.
- `RefCopilot/src/refcopilot/verify/thresholds.py` — new `CROSSREF_*` thresholds.
- `RefCopilot/src/refcopilot/merge.py` — prefer a publisher DOI over an arXiv
  DOI (`10.48550/arxiv.*`), and drop an arXiv DOI when the arXiv ID is known.
- `RefCopilot/src/refcopilot/verify/text_match.py` — new `is_arxiv_venue()`
  that also recognises `CoRR 2026` / `arXiv preprint arXiv:…`.
- `RefCopilot/src/refcopilot/verify/outdated.py`,
  `RefCopilot/src/refcopilot/bibtex_suggest.py` — use `is_arxiv_venue()`, so a
  "CoRR" venue no longer triggers an `arxiv_published` upgrade.
- `RefCopilot/tests/test_retrieval.py`, `RefCopilot/tests/test_verify.py` —
  regression tests from the mismatches seen in real reports.

These modifications remain licensed under AGPL-3.0. The complete corresponding
source is this repository.
