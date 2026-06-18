#!/usr/bin/env python3
"""Apply RefCopilot's suggested BibTeX fixes to a .bib file.

Reads a RefCopilot ``reference_check.json`` report, finds every warning that
carries a ``corrected_bibtex`` block, locates the matching @type{key, ...}
entry in the source .bib, and replaces it. Multiple warnings on the same
citation key are deduplicated — RefCopilot already merges all field
suggestions into one corrected entry per reference.

Usage:
    scripts/apply-refcheck.py REPORT.json TARGET.bib            # write TARGET.bib.refcopilot
    scripts/apply-refcheck.py REPORT.json TARGET.bib --in-place # overwrite (backup at .bak)
    scripts/apply-refcheck.py REPORT.json TARGET.bib --dry-run  # report only
    scripts/apply-refcheck.py REPORT.json TARGET.bib --strip-provenance
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path


CITE_KEY_RE = re.compile(r"^\s*@\w+\s*\{\s*([^,\s]+)\s*,", re.MULTILINE)


def extract_key(bibtex_block: str) -> str | None:
    """Return the citation key from a single @type{key, ...} block."""
    m = CITE_KEY_RE.search(bibtex_block)
    return m.group(1) if m else None


def strip_provenance(corrected: str) -> str:
    """Drop the leading ``% Suggested by RefCopilot. ...`` comment lines."""
    lines = corrected.splitlines()
    i = 0
    while i < len(lines) and (lines[i].startswith("%") or not lines[i].strip()):
        i += 1
    return "\n".join(lines[i:])


# Match a whole ``url = {...},?`` BibTeX field (and its trailing newline if
# present). We restrict to ``^\s*url\s*=`` so we don't touch fields that happen
# to *contain* a URL (e.g. ``howpublished``).
_URL_FIELD_RE = re.compile(r"^[ \t]*url\s*=\s*\{[^}]*\},?[ \t]*\n?", re.MULTILINE)


def strip_url_field(bibtex_block: str) -> str:
    """Remove ``url = {...}`` lines from a BibTeX entry."""
    return _URL_FIELD_RE.sub("", bibtex_block)


def find_entry_span(bib_text: str, key: str) -> tuple[int, int] | None:
    """Locate the byte span of the @type{key, ... } block in ``bib_text``.

    Walks balanced braces forward from the opening ``{``. Returns ``None`` if
    the key isn't found or braces aren't balanced.
    """
    # Match @type{ where the lookahead guarantees the entry's key follows.
    # The match consumes up to and including the opening '{' so m.end() is the
    # first byte *inside* the entry; depth starts at 1.
    pattern = re.compile(
        rf"@\w+\s*\{{(?=\s*{re.escape(key)}\s*,)",
        re.MULTILINE,
    )
    m = pattern.search(bib_text)
    if not m:
        return None
    start = m.start()
    depth = 1
    i = m.end()
    while i < len(bib_text):
        c = bib_text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return start, i + 1
        i += 1
    return None


def collect_replacements(report: dict) -> dict[str, str]:
    """Map citation key → corrected_bibtex, deduped across warning entries.

    If the same key appears multiple times (e.g. one entry per missing field),
    the longest corrected_bibtex wins — that's the most complete suggestion.
    """
    out: dict[str, str] = {}
    for w in report.get("warning_details", []):
        corrected = (w.get("corrected_bibtex") or "").strip()
        if not corrected:
            continue
        key = extract_key(corrected)
        if not key:
            continue
        prev = out.get(key)
        if prev is None or len(corrected) > len(prev):
            out[key] = corrected
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("report", type=Path, help="Path to RefCopilot reference_check.json")
    ap.add_argument("bib", type=Path, help="Path to the .bib file to patch")
    ap.add_argument("--in-place", action="store_true", help="Overwrite bib (backup at <bib>.bak)")
    ap.add_argument("--dry-run", action="store_true", help="Don't write; just summarize")
    ap.add_argument(
        "--strip-provenance",
        action="store_true",
        help="Drop the leading '% Suggested by RefCopilot' comment lines from each replacement",
    )
    ap.add_argument(
        "--keep-url",
        action="store_true",
        help="Keep the 'url = {...}' field in suggested entries (stripped by default — "
        "venues that need URLs typically derive them from doi/eprint instead).",
    )
    args = ap.parse_args()

    if not args.report.is_file():
        print(f"ERROR: report not found: {args.report}", file=sys.stderr)
        return 2
    if not args.bib.is_file():
        print(f"ERROR: bib not found: {args.bib}", file=sys.stderr)
        return 2

    report = json.loads(args.report.read_text(encoding="utf-8"))
    bib_text = args.bib.read_text(encoding="utf-8")

    replacements = collect_replacements(report)
    if not replacements:
        print("No corrected_bibtex blocks found in the report; nothing to apply.")
        return 0

    # Apply from the end of the file to the start, so earlier byte offsets stay
    # valid as we splice in length-changing replacements.
    spans: list[tuple[int, int, str, str]] = []   # (start, end, key, new_block)
    missing: list[str] = []
    for key, corrected in replacements.items():
        block = corrected
        if args.strip_provenance:
            block = strip_provenance(block)
        if not args.keep_url:
            block = strip_url_field(block)
        span = find_entry_span(bib_text, key)
        if span is None:
            missing.append(key)
            continue
        spans.append((*span, key, block))
    spans.sort(key=lambda s: s[0], reverse=True)

    applied: list[str] = []
    patched = bib_text
    for start, end, key, block in spans:
        patched = patched[:start] + block + patched[end:]
        applied.append(key)

    # Summary
    print(f"replacements suggested : {len(replacements)}")
    print(f"replacements applied   : {len(applied)}")
    print(f"keys not found in bib  : {len(missing)}")
    if missing:
        for k in sorted(missing):
            print(f"  - missing key: {k}")

    if args.dry_run:
        print("\n--dry-run: not writing.")
        return 0

    if args.in_place:
        backup = args.bib.with_suffix(args.bib.suffix + ".bak")
        shutil.copy2(args.bib, backup)
        args.bib.write_text(patched, encoding="utf-8")
        print(f"\npatched in place : {args.bib}")
        print(f"backup           : {backup}")
    else:
        out = args.bib.with_suffix(args.bib.suffix + ".refcopilot")
        out.write_text(patched, encoding="utf-8")
        print(f"\npatched output   : {out}")
        print("Review the diff, then move it over the original when you're happy:")
        print(f"  mv {out} {args.bib}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
