# Changelog

All notable changes to **Emby Curator** are documented here.

This project is a maintained fork of [emby-dedupe](https://github.com/troykelly/emby-dedupe)
by Troy Kelly (inactive since May 2024), distributed under the Apache License 2.0.

## [Unreleased]

### Added

- **`csfd fill --ai-match`** (opt-in): for titles the strict matcher leaves unmatched, TypeSafe's
  Jev model picks among the year-plausible ČSFD candidates, or answers "none". Each candidate is
  described with its country and original titles, which is what tells same-title, same-year films
  apart. Suggestions are **review-only** by default and go to `--ai-review-file` (default
  `csfd-ai-review.tsv`) as a ready-to-use `--map` file. `--ai-auto` applies picks at or above
  `--ai-threshold` (default 0.9). The model is pinned (`--ai-model`, default `jev-1.13.0`). Only the
  title, year, type and folder name are sent, never the full path. An API outage never fails the
  run, and a rejected key turns the fallback off for the rest of the run. The key is read from
  `DEDUPE_TYPESAFE_API_KEY` (or `TYPESAFE_API_KEY`).
- **Never-match marker for `--map`:** a line `<emby_id><TAB>-` records that a title has no ČSFD
  entry, so it is never searched or suggested again. Older versions ignore these lines.

### Fixed

- **`csfd fill` only ever searched by the item's Name.** Emby leaves out `OriginalTitle` and `Path`
  unless asked, and the item fetch never asked. The strict matcher (whose matches are applied) now
  also searches Emby's `OriginalTitle`. The folder title is deliberately *not* used there: a live
  check found folders naming a different film than Emby's metadata (folder `Peninsula (2020)`, item
  "Buklog: The Ritual System"). The folder title only feeds `--ai-match`, which sees both and
  suggests for review.

## [3.1.0] — 2026-09-22

### Added

- **`csfd fill`**: fills metadata for titles that TMDb, TVDb and IMDb can't identify, using csfd.sk
  (reached through FlareSolverr). It fills only EMPTY fields: Slovak overview, genres (mapped to
  English), year, rating, directors and actors with their roles, and a 1080×1600 poster. It accepts
  only unambiguous matches and stamps a `Csfd` provider id so later runs skip the item. `--map <tsv>`
  applies hand-resolved matches, and `--overwrite-poster` replaces fallback frame posters with ČSFD
  artwork. ČSFD's `a.z.` role shorthand is expanded to `archívne zábery`.
- **`csfd people`**: portraits for actors that no provider has a photo for, using real photos only
  (never ČSFD's silhouette placeholder). It also fills biography, birth and death dates and birthplace.

### Fixed

- **Series cleanup never deleted anything.** The fold-delete guard treated a series folder like a
  media file and refused every series. Series cleanup now works, and a refused item shows the reason
  in the report.
- **Deletion guard:** a duplicate with no known path is now refused. Before, it was deleted with no
  fold-delete protection at all.
- **TV search:** a series whose provider id or year contradicts the one requested is never accepted.
  A substring match once mapped *Malcolm in the Middle* to *The Middle* and dropped 149 episodes as
  duplicates.
- **Checker:** looks series up with `AnyProviderIdEquals` and compares provider-id keys
  case-insensitively. 877 series stored under `IMDB` were invisible to the check, which led to
  titles already in the library being downloaded again.
- **Reports:** show the path the guard actually used instead of `unknown`. They also record what
  happened to duplicates the guard refused (fold-safe delete now runs before the report is written).
- **HTML reports could overwrite each other.** File names had one-second resolution, so two reports
  written in the same second shared a file. Each report now gets a unique file (created atomically,
  readable only by its owner).
- **ČSFD:** never sends a `Cast` lock. Emby has no such value and drops the whole `LockedFields`
  list when it gets one.

### Security

- Removed the last place a report could embed the live Emby API key (the Excluded Media section).
- Removed a real Emby API key from a public test file.
- Require `anyio>=4.14.2` (pulled in through httpx) to fix CVE-2026-63374 and CVE-2026-64847.

### Changed

- Dependency refresh: `typer` 0.27.2, `tqdm` 4.70.1, `python-dotenv` 1.2.3 and `rank-torrent-name`
  1.11.1. Dev tools: `mypy` 2.3.1, `ruff` 0.16.8 and `pytest` 9.1.1.
- `requirements.txt` now lists `typer` and `lingua-language-detector`, so the CI `pip-audit` scan
  covers every runtime dependency.

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
