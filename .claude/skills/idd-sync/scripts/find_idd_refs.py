"""Find every place in the repo that references an IDD section, field name,
or byte offset -- so a change to the spec can be propagated everywhere it's
mirrored, not just in core/packet.py.

Usage:
    python find_idd_refs.py "Section 10.1"
    python find_idd_refs.py COMMAND_ACK
    python find_idd_refs.py "byte 44"

Searches .py, .md files (skips build/, dist/, __pycache__/) for the query,
case-insensitive, and prints file:line:text grouped by file.
"""
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
SKIP_DIRS = {"build", "dist", "__pycache__", ".git", "node_modules"}
EXTS = {".py", ".md"}


def iter_files():
    for path in REPO_ROOT.rglob("*"):
        if path.is_dir():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix in EXTS:
            yield path


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    query = sys.argv[1]
    pattern = re.compile(re.escape(query), re.IGNORECASE)

    hits_by_file = {}
    for path in iter_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                rel = path.relative_to(REPO_ROOT)
                hits_by_file.setdefault(str(rel), []).append((lineno, line.strip()))

    if not hits_by_file:
        print(f"No references to {query!r} found.")
        return

    total = 0
    for rel, hits in sorted(hits_by_file.items()):
        print(f"\n{rel}")
        for lineno, line in hits:
            print(f"  {lineno}: {line}")
            total += 1
    print(f"\n{total} reference(s) across {len(hits_by_file)} file(s).")


if __name__ == "__main__":
    main()
