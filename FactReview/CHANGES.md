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

These modifications remain licensed under AGPL-3.0. The complete corresponding
source is this repository.
