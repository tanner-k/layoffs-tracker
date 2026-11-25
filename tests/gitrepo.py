"""Real git repositories in a temp dir: a bare 'origin' and a clone of it."""

from __future__ import annotations

import subprocess
from pathlib import Path

IDENT = ["-c", "user.name=Test", "-c", "user.email=test@example.com"]


def git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), *IDENT, *args], check=True, capture_output=True)
    return out.stdout.decode()


def make_origin(tmp_path: Path, files: dict[str, bytes]) -> Path:
    """A bare repo on branch main whose one commit holds `files`."""
    bare, seed = tmp_path / "origin.git", tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    for name, body in files.items():
        (seed / name).parent.mkdir(parents=True, exist_ok=True)
        (seed / name).write_bytes(body)
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "initial")
    git(seed, "push", "-q", str(bare), "main")
    return bare


def commit_count(repo: Path) -> int:
    return int(git(repo, "rev-list", "--count", "main").strip())
