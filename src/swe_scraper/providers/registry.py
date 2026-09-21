"""Provider registry used by the scanner and third-party extensions."""

from __future__ import annotations

from collections.abc import Iterable
from importlib import metadata
from typing import TypedDict, cast

from .ashby import AshbyProvider
from .base import Provider
from .greenhouse import GreenhouseProvider
from .lever import LeverProvider
from .oracle import OracleProvider
from .smartrecruiters import SmartRecruitersProvider
from .workday import WorkdayProvider

BUILTIN_PROVIDERS: tuple[Provider, ...] = (
    cast(Provider, GreenhouseProvider()),
    cast(Provider, LeverProvider()),
    cast(Provider, AshbyProvider()),
    cast(Provider, WorkdayProvider()),
    cast(Provider, SmartRecruitersProvider()),
    cast(Provider, OracleProvider()),
)


class ProviderDescription(TypedDict):
    name: str
    module: str
    required_options: list[str]
    builtin: bool


class ProviderRegistry:
    """Mutable provider registry with optional Python entry-point discovery."""

    def __init__(
        self,
        providers: Iterable[Provider] = (),
        *,
        discover: bool = True,
        defer_discovery: bool = False,
    ) -> None:
        self._providers: dict[str, Provider] = {}
        self._discovered = not discover
        self._discovering = False
        for provider in providers:
            self.register(provider)
        if discover and not defer_discovery:
            self.discover()

    def _ensure_discovered(self) -> None:
        if not self._discovered and not self._discovering:
            self.discover()

    @property
    def names(self) -> tuple[str, ...]:
        self._ensure_discovered()
        return tuple(sorted(self._providers))

    def register(self, provider: Provider, *, replace: bool = False) -> None:
        name = str(getattr(provider, "name", "")).strip().casefold()
        if not name:
            raise ValueError("provider extension must define a non-empty name")
        if name in self._providers and not replace:
            raise ValueError(f"provider '{name}' is already registered")
        self._providers[name] = provider

    def discover(self) -> None:
        """Load installed ``swe_scraper.providers`` entry points."""
        if self._discovered or self._discovering:
            return
        self._discovering = True
        try:
            entry_points = metadata.entry_points()
            selected = entry_points.select(group="swe_scraper.providers")
            for entry_point in selected:
                loaded = entry_point.load()
                provider = loaded() if isinstance(loaded, type) else loaded
                self.register(provider)
        finally:
            self._discovering = False
        self._discovered = True

    def get(self, name: str) -> Provider:
        self._ensure_discovered()
        key = name.casefold()
        try:
            return self._providers[key]
        except KeyError as exc:
            supported = ", ".join(self.names) or "none"
            raise ValueError(
                f"unsupported provider '{name}'; choose from {supported}"
            ) from exc

    def describe(self) -> tuple[ProviderDescription, ...]:
        """Return stable, machine-readable provider inventory information."""
        self._ensure_discovered()
        rows: list[ProviderDescription] = []
        for name in self.names:
            provider = self._providers[name]
            rows.append(
                {
                    "name": name,
                    "module": type(provider).__module__,
                    "required_options": list(getattr(provider, "required_options", ())),
                    "builtin": any(provider is item for item in BUILTIN_PROVIDERS),
                }
            )
        return tuple(rows)


DEFAULT_REGISTRY = ProviderRegistry(BUILTIN_PROVIDERS, defer_discovery=True)
PROVIDERS = DEFAULT_REGISTRY._providers


def get_provider(name: str) -> Provider:
    return DEFAULT_REGISTRY.get(name)
