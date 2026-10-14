"""TP DECK — Techyparts Data Entry Control Keeper."""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

# Fallback when this folder is not a git checkout. A release build replaces
# this with the git tag before the zip is packed.
__version__ = "0.3.1"


def _git_tag() -> str:
    """Latest v* tag in this checkout, without the leading v."""
    root = Path(__file__).resolve().parent.parent
    if not (root / ".git").exists():
        return ""
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0", "--match", "v*"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    tag = (result.stdout or "").strip()
    if result.returncode != 0 or len(tag) < 2 or tag[:1] not in {"v", "V"}:
        return ""
    return tag[1:]


@lru_cache(maxsize=1)
def app_version() -> str:
    """Version to show and to compare against GitHub releases.

    A git checkout uses the latest v* tag, so the number changes when a tag
    is created. A release install has no .git folder and uses the version
    stamped into this file at build time.
    """
    return _git_tag() or __version__
