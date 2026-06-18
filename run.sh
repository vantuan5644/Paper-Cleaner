#!/usr/bin/env bash
# Paper-Cleaner — interactive driver.
#
# Bootstraps a single uv-managed .venv that has both tools installed, then
# prompts you through:
#   1. arxiv-latex-cleaner   — strip a LaTeX source tree to an arXiv-ready ZIP
#   2. RefCopilot            — verify every citation against arXiv / S2 /
#                              OpenReview (and OpenAlex if configured)
#
# Defaults are tuned so Enter-Enter-Enter does the right thing for
# inputs/. Anywhere you see [bracketed default], press Enter to accept.

set -euo pipefail

# ── locate repo root (this script lives at <root>/run.sh) ────────────────────
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

VENV="$ROOT/.venv"
PY_MIN="3.11"
DEFAULT_INPUT="inputs/"

# ── 0. preflight: uv must be installed ───────────────────────────────────────
if ! command -v uv >/dev/null 2>&1; then
  cat >&2 <<EOF
ERROR: uv not found on PATH.

Install it (one of):
  curl -LsSf https://astral.sh/uv/install.sh | sh
  pipx install uv
  brew install uv

Then re-run this script.
EOF
  exit 1
fi

# ── 1. bootstrap: create .venv and install both tools editable ───────────────
# A venv pins its base interpreter as an absolute symlink. If that interpreter
# is later removed (uv upgrade/gc, a different user's ~/.local uv tree, a moved
# home dir), .venv/bin/python becomes a dangling symlink and uv aborts with
# "Python interpreter not found". Validate the interpreter actually runs and
# rebuild in place with --clear if it doesn't — don't just check the dir exists.
if [[ ! -d "$VENV" ]]; then
  echo ">>> Creating $VENV (Python >=$PY_MIN) via uv..."
  uv venv --python "$PY_MIN" "$VENV"
elif ! "$VENV/bin/python" -c '' >/dev/null 2>&1; then
  echo ">>> Existing $VENV is broken (interpreter missing); rebuilding..."
  uv venv --clear --python "$PY_MIN" "$VENV"
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"

# uv pip install -e is idempotent and fast for no-op resolves, so we run it
# every time and let uv decide whether anything needs to change. The first run
# takes a couple of minutes; subsequent runs return in well under a second.
echo ">>> Ensuring editable installs are current..."
uv pip install --quiet -e ./arxiv-latex-cleaner
uv pip install --quiet -e './FactReview[refcheck]'
uv pip install --quiet -e ./FactReview/RefCopilot

# Surface a one-time hint about codex auth (RefCopilot's LLM verifier needs it
# and there is no regex fallback). We don't gate on it — the user may have
# already set up a non-Codex provider in FactReview/.env.
if ! command -v codex >/dev/null 2>&1; then
  echo "NOTE: 'codex' CLI not found. RefCopilot's LLM verifier needs an LLM"
  echo "      provider configured. Either 'npm install -g @openai/codex &&"
  echo "      codex login', or set MODEL_PROVIDER in FactReview/.env to a"
  echo "      provider you have keys for. Reference checking will fail otherwise."
fi

echo

# ── 2. interactive: pick the paper source directory ─────────────────────────
read -r -p "Path to paper source dir [$DEFAULT_INPUT]: " INPUT
INPUT="${INPUT:-$DEFAULT_INPUT}"

if [[ ! -d "$INPUT" ]]; then
  echo "ERROR: not a directory: $INPUT" >&2
  exit 1
fi
INPUT="$(cd "$INPUT" && pwd)"          # normalize to absolute
INPUT_NAME="$(basename "$INPUT")"
INPUT_PARENT="$(dirname "$INPUT")"
echo "    using: $INPUT"
echo

# ── 3. LaTeX cleaning ────────────────────────────────────────────────────────
read -r -p "Run arxiv-latex-cleaner on this directory? [Y/n]: " ANS
if [[ ! "${ANS:-y}" =~ ^[Nn]$ ]]; then
  CLEANED="${INPUT_PARENT}/${INPUT_NAME}_arXiv"
  if [[ -d "$CLEANED" ]]; then
    read -r -p "    $CLEANED exists. Overwrite? [y/N]: " OVR
    if [[ "${OVR:-n}" =~ ^[Yy]$ ]]; then
      rm -rf "$CLEANED"
    else
      echo "    keeping existing $CLEANED, skipping cleaner."
      CLEANED=""
    fi
  fi
  if [[ -n "$CLEANED" ]]; then
    echo ">>> Cleaning LaTeX → $CLEANED"
    # --keep_bib so the cleaned tree still contains the bibliography the user
    # can pass to RefCopilot if they prefer the cleaned bib over the original.
    arxiv_latex_cleaner "$INPUT" --keep_bib --verbose
    echo ">>> Cleaned tree: $CLEANED"
  fi
fi
echo

# ── 4. reference check ──────────────────────────────────────────────────────
read -r -p "Run RefCopilot reference check? [Y/n]: " ANS
if [[ "${ANS:-y}" =~ ^[Nn]$ ]]; then
  echo ">>> Skipping reference check. All done."
  exit 0
fi

# Discover candidate inputs: .bib files first (cheapest path), then a .pdf.
mapfile -t BIBS < <(find "$INPUT" -maxdepth 2 -name "*.bib" -type f | sort)
mapfile -t PDFS < <(find "$INPUT" -maxdepth 2 -name "*.pdf" -type f | sort)

CANDIDATES=("${BIBS[@]}" "${PDFS[@]}")
REFINPUT=""

if (( ${#CANDIDATES[@]} == 0 )); then
  read -r -p "    no .bib/.pdf found. Path to .bib / .pdf / arXiv URL: " REFINPUT
elif (( ${#CANDIDATES[@]} == 1 )); then
  REFINPUT="${CANDIDATES[0]}"
  echo "    auto-selected: $REFINPUT"
else
  echo "    bibliography / paper candidates:"
  for i in "${!CANDIDATES[@]}"; do
    SIZE=$(stat -c%s "${CANDIDATES[i]}" 2>/dev/null || stat -f%z "${CANDIDATES[i]}")
    printf "      %d) %s  (%s bytes)\n" "$((i+1))" "${CANDIDATES[i]}" "$SIZE"
  done
  read -r -p "    pick [1]: " PICK
  PICK="${PICK:-1}"
  if ! [[ "$PICK" =~ ^[0-9]+$ ]] || (( PICK < 1 || PICK > ${#CANDIDATES[@]} )); then
    echo "ERROR: invalid choice: $PICK" >&2
    exit 1
  fi
  REFINPUT="${CANDIDATES[$((PICK-1))]}"
fi

OUTDIR="${INPUT_PARENT}/${INPUT_NAME}_refcheck"
mkdir -p "$OUTDIR"
echo ">>> Checking references in $REFINPUT"
echo "    output → $OUTDIR"
refcopilot check "$REFINPUT" --output-dir "$OUTDIR"

echo
echo ">>> Done."
echo "    cleaned LaTeX  : ${INPUT_PARENT}/${INPUT_NAME}_arXiv (if cleaner ran)"
echo "    refcheck report: $OUTDIR"
