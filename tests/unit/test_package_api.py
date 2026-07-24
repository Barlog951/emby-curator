"""Tests for the declared public API surface of the emby_dedupe package.

Code review 2026-07-10: the package now re-exports its supported symbols lazily
(PEP 562) and single-sources __version__ from distribution metadata.
"""
import subprocess
import sys


def test_version_matches_distribution_metadata():
    from importlib.metadata import version

    import emby_dedupe

    assert emby_dedupe.__version__ == version("emby-curator")


def test_public_symbols_are_exported():
    import emby_dedupe

    for name in (
        "EmbyChecker",
        "CheckConfig",
        "ComparisonResult",
        "ExistingQuality",
        "ProposedQuality",
        "Config",
    ):
        assert name in emby_dedupe.__all__
        obj = getattr(emby_dedupe, name)
        assert obj.__name__ == name


def test_top_level_import_is_lazy():
    """Importing the package must not eagerly pull the heavy checker/quality modules.

    Runs in a fresh subprocess so this test never mutates the live interpreter's
    module/logging global state (which other tests in the suite assert on).
    """
    script = (
        "import sys, emby_dedupe\n"
        "assert emby_dedupe.__version__\n"
        # touching the version must not have imported the heavy submodules
        "assert 'emby_dedupe.api.checker' not in sys.modules, 'checker eagerly imported'\n"
        "assert 'emby_dedupe.api.quality_compare' not in sys.modules, 'quality eagerly imported'\n"
        # accessing a re-export resolves its backing module on demand
        "_ = emby_dedupe.EmbyChecker\n"
        "assert 'emby_dedupe.api.checker' in sys.modules\n"
        "print('LAZY-OK')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "LAZY-OK" in proc.stdout


def test_unknown_attribute_raises_attributeerror():
    import emby_dedupe

    try:
        emby_dedupe.DoesNotExist
    except AttributeError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected AttributeError for unknown attribute")


def test_checker_from_top_level_is_same_object():
    import emby_dedupe
    from emby_dedupe.api.checker import EmbyChecker as DeepEmbyChecker

    assert emby_dedupe.EmbyChecker is DeepEmbyChecker
