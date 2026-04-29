"""Small JSON-file store for extraction profiles."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from schema import DEFAULT_PROFILE, profile_copy, validate_profile


PROFILE_DIR = Path(__file__).resolve().parent / "extraction_profiles"


def slugify(name: str) -> str:
    """Convert a profile name into a safe JSON filename stem."""

    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "_", name.strip()).strip("_")
    return slug or "profile"


def ensure_profile_dir() -> None:
    """Create the profile directory if it does not already exist."""

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)


def profile_path(name: str) -> Path:
    """Return the filesystem path for one saved profile name."""

    return PROFILE_DIR / f"{slugify(name)}.json"


def list_profiles() -> list[str]:
    """List saved profile names, falling back to the built-in default profile."""

    ensure_profile_dir()
    names: list[str] = []
    for path in sorted(PROFILE_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("name"), str):
            names.append(data["name"])
    if DEFAULT_PROFILE["name"] not in names:
        names.insert(0, DEFAULT_PROFILE["name"])
    return names


def load_profile(name: str) -> dict[str, Any]:
    """Load a saved profile, or return the built-in default profile."""

    if name == DEFAULT_PROFILE["name"]:
        return profile_copy()
    path = profile_path(name)
    if not path.exists():
        return profile_copy()
    return json.loads(path.read_text())


def save_profile(profile: dict[str, Any]) -> Path:
    """Validate and save a profile as a JSON file."""

    errors = validate_profile(profile)
    if errors:
        raise ValueError("; ".join(errors))

    ensure_profile_dir()
    path = profile_path(profile["name"])
    path.write_text(json.dumps(profile, indent=2, ensure_ascii=False))
    return path
