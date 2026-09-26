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
    configured: set[str] = set()
    for provider, rows in data.items():
        if selected and provider.casefold() not in selected:
            continue
        if not isinstance(rows, list):
            raise ValueError(f"targets for '{provider}' must be a list")
        configured.add(provider.casefold())
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(f"target under '{provider}' must be an object")
            profiles = row.get("profiles", ["priority", "all"])
            if not isinstance(profiles, list) or any(
                not isinstance(value, str)
                or value.casefold() not in {"priority", "all", "canary"}
                for value in profiles
            ):
                raise ValueError(
                    f"profiles for target under '{provider}' must list "
                    "priority, all, or canary"
                )
            if target_profile != "all" and target_profile not in {
                value.casefold() for value in profiles
            }:
                continue
            targets.append(Target.from_mapping(provider, row))
    if selected:
        missing = selected - configured
        if missing:
            raise ValueError(f"no configured targets for: {', '.join(sorted(missing))}")
        missing_profile = selected - {target.provider for target in targets}
        if missing_profile:
            raise ValueError(
                f"no targets match profile '{target_profile}' for: "
                f"{', '.join(sorted(missing_profile))}"
            )
    if not targets and configured and target_profile != "all":
        raise ValueError(f"no targets match profile '{target_profile}'")
    return targets
