"""livability domain — equal-weight county livability composite + rankings.

Discovered by the Facetwork runner via the ``facetwork.domains`` entry point::

    [project.entry-points."facetwork.domains"]
    livability = "livability:domain"
"""

from __future__ import annotations

from pathlib import Path

from facetwork.domains import DomainPackage

from .handlers import register_all_registry_handlers

domain = DomainPackage(
    name="livability",
    ffl_dir=Path(__file__).parent / "ffl",
    register_handlers=register_all_registry_handlers,
    runner_env={
        "FW_TASK_EXECUTION_TIMEOUT_MS": "1800000",  # 30 min (NRI zip ~30MB + 4 fetches; cached after)
        "FW_STUCK_TIMEOUT_MS": "2100000",
    },
)
