# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Purpose

`Paper-Cleaner` is a **mother repo** that bundles independent tools used to clean and validate a research paper before submission. It tracks two upstream projects as **git submodules**, each with its own dependencies, license, and CI:

- `arxiv-latex-cleaner/` — strips a LaTeX source tree to an arXiv-ready ZIP (Google Research, Apache-2.0, Python ≥3.9). Upstream: `https://github.com/google-research/arxiv-latex-cleaner`.
- `FactReview/` — evidence-grounded paper reviewer; the relevant submodule for this repo is **`FactReview/RefCopilot/`**, a standalone reference-accuracy checker (AGPL-3.0, Python ≥3.11). Upstream: `https://github.com/DEFENSE-SEU/FactReview`.

Treat each submodule as the source of truth for its own conventions, licensing, and CI. Do not introduce cross-imports between them; the only thing they share is a pre-submission workflow ("clean the LaTeX, then verify the bibliography").

When developing here, the two active feature surfaces are:

1. **LaTeX cleaning** — anything that ships text/figures to arXiv (handled by `arxiv-latex-cleaner`).
2. **Reference fact-checking** — verifying citations are real, not retracted, not stale, and complete (handled by `FactReview/RefCopilot`).

Other FactReview stages (parse, claim_extract, positioning, execution, report, teaser) exist in this tree because they live in the same upstream repo, but they are **out of scope** for the mother-repo workflow. Touch them only when a change to RefCopilot requires it (see `FactReview/src/fact_generation/refcheck/` for the FactReview ↔ RefCopilot bridge).

## Submodule Layout, Upstream Sync, and Forks

The two subprojects are git submodules. After cloning the mother repo, populate them with:

```bash
git submodule update --init --recursive
```

### Two-remote convention (`origin` = your fork, `upstream` = public)

We don't have push access to the public repos, so the intended setup inside each submodule is:

| Remote     | Points at                       | Used for          |
|------------|----------------------------------|-------------------|
| `origin`   | **your fork** on GitHub          | `git push`        |
| `upstream` | the canonical public repo        | `git fetch` only  |

The current state (before any fork has been created) has both `origin` and `upstream` aliased to the public URL. The mother repo's `.gitmodules` also records the public URL, so a fresh `git clone --recurse-submodules` Just Works for read-only use. Once you create a fork on GitHub, point `origin` at it (the in-submodule `upstream` remote keeps fetching from public unchanged):

```bash
# Replace <you> with your GitHub username
git -C arxiv-latex-cleaner remote set-url origin git@github.com:<you>/arxiv-latex-cleaner.git
git -C FactReview         remote set-url origin git@github.com:<you>/FactReview.git

# Also record the fork URL in .gitmodules so future clones of the mother repo
# pull from your fork by default:
git submodule set-url arxiv-latex-cleaner git@github.com:<you>/arxiv-latex-cleaner.git
git submodule set-url FactReview          git@github.com:<you>/FactReview.git
git add .gitmodules && git commit -m "chore: point submodule origins at forks"
```

### Pulling updates from upstream

```bash
scripts/sync-upstream.sh          # fetch upstream for each submodule; show new commits
scripts/sync-upstream.sh --ff     # also fast-forward each submodule to upstream's default branch
```

After `--ff`, commit the bumped submodule pointers from the mother repo so collaborators get the new revisions:

```bash
git add arxiv-latex-cleaner FactReview
git commit -m "chore: bump submodules to upstream HEAD"
```

If `--ff` refuses to fast-forward (because you have local commits on a submodule), open the submodule, decide between `git merge upstream/main` and `git rebase upstream/main`, then push to **your fork's `origin`** before bumping the pointer.

### Working on changes inside a submodule

Submodule HEADs are detached by default after `git submodule update`. Before making changes:

```bash
cd FactReview                 # or arxiv-latex-cleaner
git checkout -b feature/my-change   # branch off the pinned commit
# ...edit, commit, then:
git push -u origin feature/my-change # pushes to your fork (once origin is your fork)
```

Then from the mother repo, bump the pointer:

```bash
git add FactReview && git commit -m "chore: bump FactReview to feature/my-change"
```

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

- **Do not** merge the two subprojects into one package or move them out of submodules. They have incompatible licenses (Apache-2.0 vs AGPL-3.0) and independent upstreams; cross-imports would taint either side.
- **Do not** add a top-level `pyproject.toml` / `setup.py` / `requirements.txt`. Each submodule owns its own.
- When adding pre-submission tooling, decide first whether it belongs in `arxiv-latex-cleaner` (LaTeX-source transforms) or in RefCopilot (citation verification). If it fits neither, propose a new sibling submodule (or a top-level `tools/` directory for thin glue) rather than wedging it into an existing one.
- Any change to a submodule's code must be committed **inside that submodule first** and pushed to your fork's `origin`. The mother repo only tracks the submodule SHA — committing changes to submodule files from the parent without entering the submodule will fail.
- Python versions differ: 3.9+ for `arxiv-latex-cleaner`, 3.11+ for FactReview/RefCopilot. Use separate virtualenvs.
