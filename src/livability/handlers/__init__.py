"""Handler registration for the livability domain."""

from __future__ import annotations

from .livability_handlers import register_handlers as _register_livability


def register_all_registry_handlers(runner) -> None:
    _register_livability(runner)
