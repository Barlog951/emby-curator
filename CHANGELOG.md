# Changelog

All notable changes to **Emby Curator** are documented here.

This project is a maintained fork of [emby-dedupe](https://github.com/troykelly/emby-dedupe)
by Troy Kelly (inactive since May 2024), distributed under the Apache License 2.0.

## [3.0.2] — 2026-07-30

### Fixed

- **Python 3.12 / 3.13 support.** Code merged after 3.0.1 was unusable on any Python
  below 3.14: seven modules — including `cli/main`, `cli/check`, `api/checker`,
  `api/deduplication`, `api/metadata`, `api/quality_compare` and `utils/config` —
  raised `NameError` at import, so the `dedupe` and `check` commands could not run.
  The cause was self-referencing annotations (e.g. `def from_emby_item(cls, ...) ->
  ExistingQuality` inside the `ExistingQuality` class body). Python 3.14 defers
  annotation evaluation (PEP 649) and accepts them; 3.12 and 3.13 evaluate them
  while the class body is still executing and fail. The affected modules now use
  `from __future__ import annotations`.

  **Release 3.0.1 itself was not affected** — the regression was introduced after it
  was published and never reached a release artifact. It did affect anyone tracking
  `main` from a git checkout on Python 3.12/3.13.

### Changed

- CI runs the test suite on Python 3.12, 3.13 and 3.14 instead of 3.14 only — the
  gap that allowed the above to ship against a declared `requires-python = ">=3.12"`.
- Added `tests/unit/test_python_compat.py`: a static AST guard that rejects
  eagerly-evaluated self-referencing annotations on any interpreter, plus a test
  that every module in the package imports.

## [3.0.1] — 2026

- Repository renamed `emby-dedupe` → `emby-curator`; updated all repo/image URLs
  in metadata, README, and CI.
- Fixed PyPI project metadata (URLs pointed at the pre-rename repo) and made
  README links absolute so they render on PyPI.
- Added automated PyPI publishing on GitHub release (PyPI Trusted Publishing).

## [3.0.0] — 2026 — "Curator" (first release under the new name)

Renamed from `emby-dedupe` to **`emby-curator`** to reflect a scope that has grown
well beyond deduplication. The `emby-dedupe` console command and the `emby_dedupe`
import package are retained for backward compatibility.

### Significant changes from upstream `emby-dedupe`

- **Package refactor** — modular `api/ · cli/ · models/ · reports/ · utils/`
  layout with a Typer subcommand CLI (`dedupe`, `cleanup`, `genres`,
  `descriptions`, `check`, `missing-episodes`).
- **Genre management** (`genres audit|normalize|fix|process`) — fill and
  normalize genres from TMDB/OMDb with rate limiting and a persistent cache;
  real-time webhook listener for new media.
- **Description localization** (`descriptions fill`) — Slavic (SK/CZ)
  Overview/Tagline/Name localization from TMDB with a 30-day persistent cache
  and `lingua`-based language detection.
- **Library cleanup** (`cleanup`) — remove stale, unwatched media with a
  dynamic rating-decay protection model and path/provider-ID/actor protections.
- **Missing-episode analysis** (`missing-episodes`) — detect gaps in series and
  franchises with deep-link reports.
- **Quality comparison** — score and compare media quality across copies.
- **Analytics dashboards** — three interactive `marimo` dashboards (unplayed,
  missing, yearly analytics).
- **Engineering** — 1000+ test suite, `ruff` + `mypy` clean, SonarQube quality
  gate, GitHub Actions CI/CD, multi-arch (amd64/arm64) container builds,
  modern `pyproject.toml` packaging.
- **Licensing/metadata** — corrected license metadata to Apache-2.0; added
  `NOTICE` and upstream attribution.

For the original project's history, see the upstream repository.
