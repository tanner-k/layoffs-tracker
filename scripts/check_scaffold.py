#!/usr/bin/env python3
"""Fail if this project still holds anything project init should have filled in.

Usage:
    python3 scripts/check_scaffold.py [ROOT]

ROOT defaults to the repository root (the parent of scripts/). Checks:

1. No placeholder token (two underscores, a name from KNOWN_TOKENS, two
   underscores) in any text file or in any file or folder name. Only the
   template's own tokens count: lowercase dunders such as the package-init
   file, and uppercase ones real code uses (a Next.js page-data global, a
   build-time version constant), are ordinary code, not tokens.
2. AGENTS.md exists and has the same bytes as CLAUDE.md. It is normally a
   symlink; a plain copy is accepted on filesystems without symlinks.
3. Every path in REQUIRED_FILES exists.

Exits 0 when clean. Otherwise prints one line per problem and exits 1.
Stdlib only; Python 3.11+. CI runs it on every push via scripts/check.sh.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Iterator
from pathlib import Path

PLACEHOLDER_RE = re.compile(r"__[A-Z][A-Z0-9_]*__")
# The names project init fills in, without their surrounding double underscores,
# so this file never holds a literal token. scripts/test_scaffold.py in the
# template repo checks this list against what scaffold.py actually replaces.
KNOWN_TOKENS: frozenset[str] = frozenset(
    {
        "PROJECT_NAME",
        "PROJECT_DESCRIPTION",
        "PKG",
        "GITHUB_REPO",
        "PROJECT_KEY",
        "WHAT",
        "WHO",
        "VALUE_PROP",
        "FEATURE_1",
        "FEATURE_2",
        "FEATURE_3",
        "LICENSE_TYPE",
        "INIT_DATE",
        "INIT_YEAR",
    }
)
REQUIRED_FILES: tuple[str, ...] = ("CLAUDE.md", "README.md", "CHANGELOG.md", ".onigiri.toml")
# Generated or vendored trees: never hand-written, never scaffolded.
SKIP_DIRS = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".next",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "node_modules",
    }
)


def iter_paths(root: Path) -> Iterator[Path]:
    """Every file and folder under root, skipping generated trees."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        base = Path(dirpath)
        for name in dirnames:
            yield base / name
        for name in sorted(filenames):
            yield base / name


def find_tokens(text: str) -> list[str]:
    """Template tokens found in text; other double-underscore names are skipped."""
    return [m.group(0) for m in PLACEHOLDER_RE.finditer(text) if m.group(0)[2:-2] in KNOWN_TOKENS]


def placeholder_problems(root: Path) -> list[str]:
    problems: list[str] = []
    for path in iter_paths(root):
        rel = path.relative_to(root).as_posix()
        for token in find_tokens(path.name):
            problems.append(f"{rel}: placeholder {token} in a file or folder name")
        if path.is_symlink() or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # binary: images, fonts, compiled blobs
        except OSError as exc:  # a file we cannot read could hide a token
            problems.append(f"{rel}: unreadable ({exc.__class__.__name__})")
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for token in find_tokens(line):
                problems.append(f"{rel}:{lineno}: placeholder {token} left unfilled")
    return problems


def agents_problems(root: Path) -> list[str]:
    claude, agents = root / "CLAUDE.md", root / "AGENTS.md"
    if not agents.exists():  # also False for a symlink whose target is gone
        return ["AGENTS.md: missing or a broken symlink (should point to CLAUDE.md)"]
    if not agents.is_file():  # a directory, or a symlink to one
        return ["AGENTS.md: not a file"]
    if not claude.is_file():
        return []  # required_problems already reports it
    try:
        same = agents.read_bytes() == claude.read_bytes()
    except OSError as exc:
        return [f"AGENTS.md: cannot compare with CLAUDE.md ({exc.__class__.__name__})"]
    return [] if same else ["AGENTS.md: differs from CLAUDE.md (should be a symlink to it)"]


def required_problems(root: Path) -> list[str]:
    return [
        f"{name}: required file is missing"
        for name in REQUIRED_FILES
        if not (root / name).is_file()  # a directory of that name does not count
    ]


def find_problems(root: Path) -> list[str]:
    return required_problems(root) + agents_problems(root) + placeholder_problems(root)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # the ✗/✓ marks must not crash a legacy console
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0]) if args else Path(__file__).resolve().parent.parent
    if not root.is_dir():
        print(f"✗ {root} is not a directory", file=sys.stderr)
        return 2
    problems = find_problems(root)
    for problem in problems:
        print(f"✗ {problem}", file=sys.stderr)
    if problems:
        print(
            f"{len(problems)} scaffold problem(s). Fill or remove them, then re-run.",
            file=sys.stderr,
        )
        return 1
    print("✓ scaffold clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
