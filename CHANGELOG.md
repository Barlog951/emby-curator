# Changelog

All notable changes to **Emby Curator** are documented here.

This project is a maintained fork of [emby-dedupe](https://github.com/troykelly/emby-dedupe)
by Troy Kelly (inactive since May 2024), distributed under the Apache License 2.0.

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
