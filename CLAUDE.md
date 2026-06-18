# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Purpose

`Paper-Cleaner` is a **mother repo** that bundles independent tools used to clean and validate a research paper before submission. It **vendors two upstream projects as plain source** (they were previously git submodules; the repo is now self-contained — `git clone` is enough, no `--recurse-submodules`). Each retains its own dependencies, license, and CI:

- `arxiv-latex-cleaner/` — strips a LaTeX source tree to an arXiv-ready ZIP (Google Research, Apache-2.0, Python ≥3.9). Upstream: `https://github.com/google-research/arxiv-latex-cleaner`.
- `FactReview/` — evidence-grounded paper reviewer; the relevant submodule for this repo is **`FactReview/RefCopilot/`**, a standalone reference-accuracy checker (AGPL-3.0, Python ≥3.11). Upstream: `https://github.com/DEFENSE-SEU/FactReview`.

Treat each submodule as the source of truth for its own conventions, licensing, and CI. Do not introduce cross-imports between them; the only thing they share is a pre-submission workflow ("clean the LaTeX, then verify the bibliography").

When developing here, the two active feature surfaces are:

1. **LaTeX cleaning** — anything that ships text/figures to arXiv (handled by `arxiv-latex-cleaner`).
2. **Reference fact-checking** — verifying citations are real, not retracted, not stale, and complete (handled by `FactReview/RefCopilot`).

Other FactReview stages (parse, claim_extract, positioning, execution, report, teaser) exist in this tree because they live in the same upstream repo, but they are **out of scope** for the mother-repo workflow. Touch them only when a change to RefCopilot requires it (see `FactReview/src/fact_generation/refcheck/` for the FactReview ↔ RefCopilot bridge).

## Vendored Layout & Upstream Sync

The two subprojects live as **plain checked-in directories** — no submodules, no
`.gitmodules`, no gitlinks. A fresh `git clone` already contains everything.

Each vendored tree is pinned to a specific upstream commit, recorded in `NOTICE`:

| Subtree | Upstream | Pinned | Local mods |
|---------|----------|--------|-----------|
| `arxiv-latex-cleaner/` | `github.com/google-research/arxiv-latex-cleaner` | `bcc1460` (v1.0.11-2) | none |
| `FactReview/` | `github.com/DEFENSE-SEU/FactReview` | `ac0f9ec` | Crossref backend — see `FactReview/CHANGES.md` |

### Pulling updates from upstream

Re-vendor a subtree from a newer upstream commit (this overwrites the subtree to
match upstream; see the script header for the caveats):

```bash
scripts/revendor.sh arxiv-latex-cleaner            # default branch HEAD
scripts/revendor.sh FactReview <commit-ish>        # a specific commit
```

After re-vendoring: update the pinned commit in `NOTICE` (and `README.md`), and
if you re-vendored **FactReview**, re-apply local modifications and update
`FactReview/CHANGES.md` (AGPL §5a requires dated change notices). Then:

```bash
git add arxiv-latex-cleaner FactReview NOTICE
git commit -m "chore: re-vendor <tool> to upstream <sha>"
```

### Working on changes inside a vendored subtree

Just edit the files in place and commit them in this repo — there is no separate
submodule to enter or push. **If you modify `FactReview/` (AGPL-3.0), add a dated
entry to `FactReview/CHANGES.md`** describing what changed (AGPL §5a). Changes to
`arxiv-latex-cleaner/` (Apache-2.0) should note modifications too, per Apache §4.

## Common Commands

All commands assume you are inside the relevant subdirectory.

### arxiv-latex-cleaner

```bash
# Install (editable) for development
cd arxiv-latex-cleaner && pip install -e .

# Run on a paper directory; produces ../<dir>_arXiv/ alongside the input
python -m arxiv_latex_cleaner /path/to/latex --config cleaner_config.yaml

# Common one-off knobs (see README.md for the full set)
python -m arxiv_latex_cleaner /path/to/latex \
  --resize_images --im_size 500 \
  --commands_to_delete todo note \
  --keep_bib

# Test suite (single test module)
python -m unittest arxiv_latex_cleaner.tests.arxiv_latex_cleaner_test

# Run a single test
python -m unittest arxiv_latex_cleaner.tests.arxiv_latex_cleaner_test.ArxivLatexCleanerTest.test_<name>
```

### FactReview / RefCopilot

RefCopilot **reuses FactReview's LLM client** by importing from `FactReview/src/`, so RefCopilot cannot be installed in isolation from the FactReview source tree. Install both:

```bash
cd FactReview
python -m venv .venv && source .venv/bin/activate
pip install -e ".[refcheck]"          # FactReview core + refcheck extras
pip install -e "./RefCopilot[dev]"    # RefCopilot CLI + tests
codex login                            # one-time, used by the LLM verifier
cp .env.example .env                   # then fill in SEMANTIC_SCHOLAR_API_KEY (recommended)

# Run the reference checker on any of: .bib | .pdf | arXiv URL | plain bib text
refcopilot check path/to/paper.pdf
refcopilot check path/to/refs.bib --output-dir out/
refcopilot cache prune --ttl-days 30

# Tests (RefCopilot)
cd RefCopilot && pytest                # fast tests; `pytest -m slow` for the rest

# Lint / type-check (FactReview-wide, also covers the refcheck bridge)
cd FactReview && ruff check . && ruff format --check .
mypy src/schemas src/util src/common   # narrow CI smoke; `mypy` alone for full pass
```

`refcopilot check --help` lists all flags; `--no-llm-verify`, `--no-cache`, and `--max-refs N` are the ones most useful while iterating.

## Architecture Notes

### arxiv-latex-cleaner

A single-package Python tool. Almost all logic lives in `arxiv_latex_cleaner/arxiv_latex_cleaner.py` (~37k LOC). `__main__.py` parses CLI args (and the optional `--config cleaner_config.yaml`) and calls into it. The pipeline reads `input_folder/`, walks `.tex` files starting from the root, and writes a sibling `input_folder_arXiv/` containing only files that are reachable from the root and that survive the configured transformations: comment removal, `\if…\fi` constant-conditional simplification, regex-based command/environment deletion, image resizing/conversion, PDF compression (ghostscript), and TikZ externalization. The regex `patterns_and_insertions` rules from the YAML config run **before** `\includegraphics` resolution, so any inserted figure paths are also copied across.

### FactReview / RefCopilot

RefCopilot is a 4-stage pipeline (`inputs → extract → search → verify → report`) under `FactReview/RefCopilot/src/refcopilot/`:

- `inputs/` — accepts `.bib`, `.pdf`, arXiv URLs, or plain text; LLM-only extraction (no regex fallback) emits a normalized reference list.
- `search/` — concurrent lookups against arXiv, Semantic Scholar, OpenReview, and (when `OPENALEX_API_KEY` is set) OpenAlex. `cache/` and `ratelimit/` wrap these calls; cache lives under `~/.cache/refcopilot/` and is pruned via the CLI.
- `verify/` — fast offline heuristics (title similarity, author overlap, OCR-garble detection) classify each citation; the LLM is only invoked on ambiguous cases. Retraction detection unifies OpenAlex `is_retracted` + Retraction Watch + arXiv withdrawn-preprint flags. A "second-chance" LLM pass asks for canonical metadata when nothing matches but the citation looks real.
- `merge.py` + `bibtex_suggest.py` — produce corrected BibTeX entries with per-field provenance comments (which backend supplied each field, plus the lookup URL).
- `report.py` — emits Markdown + JSON; `factreview.py` is the adapter used by FactReview's refcheck stage.

Citation findings fall into two severities: **errors** (`fake/no_match`, `retracted`) and **warnings** (`outdated/arxiv_published`, `outdated/arxiv_version`, `outdated/workshop_to_full`, `incomplete`, `non_academic_downgrade`). The `non_academic_downgrade` bucket is how the LLM verifier rescues legitimate system cards / blog posts / standards / datasets that the offline title-mismatch heuristic flagged as fake.

### FactReview ↔ RefCopilot integration

When FactReview's full pipeline runs with `--enable-refcheck`, `FactReview/src/fact_generation/refcheck/refcheck.py` injects `FactReview/RefCopilot/src` into `sys.path` and calls `refcopilot.factreview.check_references()`. The result is written to `runs/<paper_key>_<timestamp>/stages/fact_generation/refcheck/reference_check.json`, and a fabricated-references summary is appended to `final_review.md`. RefCopilot reads FactReview's LLM client from `FactReview/src/`; override the discovery path with `REFCOPILOT_FACTREVIEW_SRC` if the layout changes.

## Conventions

- **Do not** merge the two subprojects into one package. They have incompatible licenses (Apache-2.0 vs AGPL-3.0) and independent upstreams. The repo is a **"mere aggregation"**: the two tools must stay independent programs — **never add a cross-import between `arxiv-latex-cleaner/` and `FactReview/`**, or you pull the Apache-2.0 code under AGPL-3.0.
- **Do not** relicense the vendored subtrees or strip their `LICENSE`/copyright headers. The top-level glue (`run.sh`, `scripts/`, docs) is MIT; the subtrees keep their upstream licenses. See `LICENSE` and `NOTICE`.
- **Do not** add a top-level `pyproject.toml` / `setup.py` / `requirements.txt`. Each vendored tree owns its own.
- When adding pre-submission tooling, decide first whether it belongs in `arxiv-latex-cleaner` (LaTeX-source transforms) or in RefCopilot (citation verification). If it fits neither, add thin glue under `scripts/` (MIT) rather than wedging it into a vendored subtree.
- Modifications to `FactReview/` (AGPL-3.0) must be recorded in `FactReview/CHANGES.md` with a date (AGPL §5a). Modifications to `arxiv-latex-cleaner/` (Apache-2.0) should be noted per Apache §4.
- Python versions differ: 3.9+ for `arxiv-latex-cleaner`, 3.11+ for FactReview/RefCopilot. `run.sh` installs both into one shared `.venv`.
