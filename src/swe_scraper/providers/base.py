"""Provider interfaces and target configuration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..models import Job


@dataclass(frozen=True, slots=True)
class Target:
    provider: str
    name: str
    slug: str
    options: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, provider: str, raw: Mapping[str, Any]) -> Target:
        name = str(raw.get("name") or "").strip()
        slug = str(raw.get("slug") or raw.get("site") or "").strip()
        if not provider or not name or not slug:
            raise ValueError("target requires provider, name, and slug/site")
        options = {
            key: value
            for key, value in raw.items()
            if key not in {"name", "slug", "profiles"}
        }
        return cls(provider.casefold(), name, slug, options)


class HttpClient(Protocol):
    def get_json(self, url: str, **kwargs: Any) -> Any: ...

    def post_json(self, url: str, payload: Mapping[str, Any], **kwargs: Any) -> Any: ...

    def get_text(self, url: str, **kwargs: Any) -> str: ...


# Backward-compatible name retained through the 1.x series.
JsonClient = HttpClient


class Provider(Protocol):
    name: str
    required_options: tuple[str, ...]

    def validate_target(self, target: Target) -> None: ...

    def fetch(self, target: Target, client: HttpClient) -> list[Job]: ...
