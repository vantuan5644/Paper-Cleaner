# Paper-Cleaner

Pre-submission hygiene for a research paper, in one self-contained repository:

1. **Clean the LaTeX** — strip a LaTeX source tree down to an arXiv-ready bundle
   (remove comments, dead `\if…\fi` branches, `\todo`/`\note` commands, resize
   images, compress PDFs, externalize TikZ).
2. **Verify the bibliography** — check that every citation is real, not
   retracted, not stale, and complete, against arXiv / Semantic Scholar /
   OpenReview / Crossref / OpenAlex.

`run.sh` walks you through both steps interactively.

> This repo **bundles** two independent upstream tools as vendored source (no
> git submodules). Each keeps its own license — see [Licensing](#licensing).

## Bundled tools & their original repositories

This project does not reimplement either tool; it vendors and orchestrates them.
Please star / cite the upstreams — all credit for the heavy lifting is theirs:

| Tool | What it does | Original repository | License |
|------|--------------|---------------------|---------|
| **arxiv-latex-cleaner** | LaTeX → arXiv-ready source | <https://github.com/google-research/arxiv-latex-cleaner> (Google Research) | Apache-2.0 |
| **FactReview / RefCopilot** | Reference / citation fact-checker | <https://github.com/DEFENSE-SEU/FactReview> | AGPL-3.0 |

Vendored revisions: `arxiv-latex-cleaner` @ `bcc1460` (v1.0.11-2), `FactReview`
@ `ac0f9ec` (plus local modifications — see [`FactReview/CHANGES.md`](FactReview/CHANGES.md)).

## Quick start

```bash
# Prerequisite: uv  (https://docs.astral.sh/uv/)
#   curl -LsSf https://astral.sh/uv/install.sh | sh

git clone https://github.com/vantuan5644/Paper-Cleaner.git
cd Paper-Cleaner
./run.sh
```

`run.sh` creates one shared `.venv` (via `uv`), installs both tools editable
into it, and then prompts you through cleaning and reference-checking. Press
Enter to accept the `[bracketed default]` at each step.

### LLM verifier & API keys

RefCopilot's reference verifier calls an LLM (there is no regex fallback) and
benefits from a Semantic Scholar key:

```bash
# 1. An LLM provider — either Codex CLI…
npm install -g @openai/codex && codex login
#    …or set MODEL_PROVIDER + keys in FactReview/.env

# 2. Recommended: a Semantic Scholar API key (higher rate limits)
cp FactReview/.env.example FactReview/.env   # then fill in SEMANTIC_SCHOLAR_API_KEY
```

Without an LLM provider configured, reference checking will fail; LaTeX
cleaning works regardless.

## Requirements

- [`uv`](https://docs.astral.sh/uv/) — manages the virtualenv and installs.
- **Python ≥ 3.11** (required by FactReview/RefCopilot; also satisfies
  arxiv-latex-cleaner's ≥ 3.9).
- An LLM provider for RefCopilot (Codex CLI, or any provider configured in
  `FactReview/.env`).
- Optional: `SEMANTIC_SCHOLAR_API_KEY` (recommended), `OPENALEX_API_KEY`.
- Optional: `ghostscript` for PDF compression in arxiv-latex-cleaner.

## Manual usage

If you'd rather drive the tools directly (after `./run.sh` has built `.venv`, or
in your own environment):

```bash
source .venv/bin/activate

# 1. Clean a LaTeX tree → produces a sibling <dir>_arXiv/
arxiv_latex_cleaner /path/to/latex --keep_bib --verbose
#    common knobs:
arxiv_latex_cleaner /path/to/latex --resize_images --im_size 500 \
                    --commands_to_delete todo note

# 2. Check references (accepts .bib | .pdf | arXiv URL | plain bib text)
refcopilot check /path/to/paper.pdf --output-dir out/
refcopilot cache prune --ttl-days 30

# 3. (Optional) apply RefCopilot's suggested BibTeX fixes back into a .bib
scripts/apply-refcheck.py out/reference_check.json refs.bib            # writes refs.bib.refcopilot
scripts/apply-refcheck.py out/reference_check.json refs.bib --in-place # overwrite (backup at .bak)
```

See `arxiv_latex_cleaner --help` and `refcopilot check --help` for the full
flag sets. While iterating, RefCopilot's `--no-llm-verify`, `--no-cache`, and
`--max-refs N` are the handy ones.

## Repository layout

```
Paper-Cleaner/
├── run.sh                  # interactive driver (MIT)
├── scripts/
│   ├── apply-refcheck.py   # apply RefCopilot BibTeX fixes to a .bib (MIT)
│   └── revendor.sh         # re-vendor a tool from a pinned upstream commit (MIT)
├── arxiv-latex-cleaner/    # vendored — Apache-2.0 (unmodified)
├── FactReview/             # vendored — AGPL-3.0 (modified; see CHANGES.md)
│   └── RefCopilot/         #   the reference checker used here
├── LICENSE                 # MIT — scope note + the top-level glue's terms
└── NOTICE                  # per-component attribution + AGPL §13 notice
```

The glue is deliberately loose coupling: `run.sh` only invokes the two CLIs as
subprocesses and `apply-refcheck.py` only reads RefCopilot's JSON output — the
two tools never import each other.

## Licensing

**This repository is an aggregate of independently licensed components. There is
no single license for the whole repo.**

| Path | License | Notes |
|------|---------|-------|
| `LICENSE`, `run.sh`, `scripts/`, docs | **MIT** | Top-level glue authored here |
| `arxiv-latex-cleaner/` | **Apache-2.0** | © 2018 The Google Research Authors; unmodified |
| `FactReview/` (incl. `RefCopilot/`) | **AGPL-3.0** | © FactReview authors; **modified** (Crossref backend, [`FactReview/CHANGES.md`](FactReview/CHANGES.md)) |

Why these can be bundled: Apache-2.0 is one-way compatible into AGPL-3.0, and
the two tools are run as separate programs (no cross-imports), so this is a
**"mere aggregation"** — each component keeps its upstream license and the MIT
glue does not relicense anything.

**AGPL-3.0 network-use notice (§13):** RefCopilot is AGPL-3.0 and is modified
here. If you run a modified version to interact with users over a network, you
must offer those users the Corresponding Source of your version. The complete
source bundled here is this repository.

Full texts: [`arxiv-latex-cleaner/LICENSE`](arxiv-latex-cleaner/LICENSE),
[`FactReview/LICENSE`](FactReview/LICENSE), and [`NOTICE`](NOTICE).

## Updating a vendored tool

Vendored trees are pinned by commit (recorded in `NOTICE`). To pull a newer
upstream revision into a subtree:

```bash
scripts/revendor.sh arxiv-latex-cleaner   # re-vendor from its recorded upstream
scripts/revendor.sh FactReview <commit>   # pin a specific commit
```

After re-vendoring, update the pinned commit in `NOTICE`, and if you re-vendor
**FactReview**, re-apply / re-document any local modifications in
`FactReview/CHANGES.md`.

## Contributing

- Changes to a vendored subtree must respect that subtree's upstream license.
  Modifications to `FactReview/` (AGPL-3.0) must be recorded in
  `FactReview/CHANGES.md` with a date (AGPL §5a).
- **Never** add an import that crosses between `arxiv-latex-cleaner/` and
  `FactReview/` — that would break the aggregation boundary and pull the
  Apache-2.0 code under AGPL-3.0.
- Run each tool's own test suite before submitting (see below).

## Tests

```bash
source .venv/bin/activate
python -m unittest arxiv_latex_cleaner.tests.arxiv_latex_cleaner_test   # arxiv-latex-cleaner
( cd FactReview/RefCopilot && pytest )                                  # RefCopilot
```
