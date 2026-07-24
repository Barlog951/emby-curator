"""
Emby Curator (import package: emby_dedupe) — curate Emby libraries: deduplicate,
localize genres & descriptions, clean up stale media, and analyze missing episodes.

Public Python API (import from the top-level package)::

    from emby_dedupe import EmbyChecker, CheckConfig

    with EmbyChecker.from_config() as checker:
        result = checker.check(name="Inception", year=2010, resolution="2160p")
        if result.should_download:
            ...

``EmbyChecker`` / ``CheckConfig`` / ``ComparisonResult`` / ``ExistingQuality`` /
``ProposedQuality`` / ``Config`` are the supported surface. They are re-exported
lazily (PEP 562) so ``import emby_dedupe`` stays cheap — importing e.g. the version
string does not pull in httpx or the checker machinery.
"""

from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING

try:
    # Single source of truth: the installed distribution's metadata (PyPI name
    # "emby-curator" — the import package stays "emby_dedupe" deliberately).
    __version__ = version("emby-curator")
except PackageNotFoundError:  # running from a raw source checkout without install
    __version__ = "0.0.0.dev0"

__all__ = [
    "EmbyChecker",
    "CheckConfig",
    "ComparisonResult",
    "ExistingQuality",
    "ProposedQuality",
    "Config",
    "__version__",
]

# Map each public name to the module it lives in, resolved on first access.
_CHECKER_MOD = "emby_dedupe.api.checker"
_QUALITY_COMPARE_MOD = "emby_dedupe.api.quality_compare"

_LAZY_EXPORTS = {
    "EmbyChecker": _CHECKER_MOD,
    "CheckConfig": _CHECKER_MOD,
    "ComparisonResult": _QUALITY_COMPARE_MOD,
    "ExistingQuality": _QUALITY_COMPARE_MOD,
    "ProposedQuality": _QUALITY_COMPARE_MOD,
    "Config": "emby_dedupe.utils.config",
}

if TYPE_CHECKING:  # give type checkers/IDEs the real symbols without runtime import cost
    from emby_dedupe.api.checker import CheckConfig, EmbyChecker
    from emby_dedupe.api.quality_compare import (
        ComparisonResult,
        ExistingQuality,
        ProposedQuality,
    )
    from emby_dedupe.utils.config import Config


def __getattr__(name: str):
    """Lazily import and return a public API symbol (PEP 562)."""
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    value = getattr(importlib.import_module(module_path), name)
    globals()[name] = value  # cache so subsequent accesses skip __getattr__
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
