"""Target configuration loading."""

from __future__ import annotations

import json
from collections.abc import Iterable
from importlib import resources
from pathlib import Path

from .providers.base import Target


def default_targets_text() -> str:
    return (
        resources.files("swe_scraper")
        .joinpath("data/targets.json")
        .read_text(encoding="utf-8")
    )


def load_targets(
    path: Path | str | None = None,
    providers: Iterable[str] = (),
    *,
    profile: str = "priority",
) -> list[Target]:
    """Load provider targets from the public JSON configuration format."""
    text = Path(path).read_text(encoding="utf-8") if path else default_targets_text()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("targets file must be a JSON object keyed by provider")
    selected = {value.casefold() for value in providers if value}
    target_profile = profile.casefold().strip()
    if target_profile not in {"priority", "all", "canary"}:
        raise ValueError("target profile must be priority, all, or canary")
    targets: list[Target] = []
    for provider, rows in data.items():
        if selected and provider.casefold() not in selected:
            continue
        if not isinstance(rows, list):
            raise ValueError(f"targets for '{provider}' must be a list")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(f"target under '{provider}' must be an object")
            profiles = row.get("profiles")
            if (
                target_profile != "all"
                and isinstance(profiles, list)
                and target_profile not in {str(value).casefold() for value in profiles}
            ):
                continue
            targets.append(Target.from_mapping(provider, row))
    if selected:
        configured = {target.provider for target in targets}
        missing = selected - configured
        if missing:
            raise ValueError(f"no configured targets for: {', '.join(sorted(missing))}")
    return targets
