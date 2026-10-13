# Changelog

All notable changes to **Emby Curator** are documented here.

This project is a maintained fork of [emby-dedupe](https://github.com/troykelly/emby-dedupe)
by Troy Kelly (inactive since May 2024), distributed under the Apache License 2.0.

## [Unreleased]

### Fixed

- **Log lines with arguments no longer fail.** The redaction filter put the arguments into
  the message but left them on the record, so every `%`-style log call (report posters,
  fold-safe delete errors, the dedupe history) raised "not all arguments converted" and
  lost its line.

- **Dedupe never pairs two films whose IMDb or TMDb ids differ.** Copies were grouped
  when they shared ANY provider id, and TVDB gives some unrelated films one id: Squared
  Love Everlasting and Squared Love All Over Again (2023) were paired through TVDB 348427
  and only the running-time check stopped the delete. Grouping now skips such pairs, and
  the delete guard refuses any keeper/delete pair with different IMDb or TMDb ids.

- **The frame check reads the picture rate behind Emby's frame rate.** Emby reports interlaced
  TV recordings at their field rate (50 for 25) and some x265 encodes at 23.809525 for 23.976,
  so the speed the two copies should play at came out wrong and nine identical The Killing pairs
  stayed blocked. Rates of 47 and more are halved and rates within 1 % of a standard one
  (23.976, 24, 25, 29.97, 30) are snapped to it. Saved verdicts are measured again (version 5).

## [3.4.1] — 2026-10-12

### Changed

- **Dedupe removes an episode copy of a different length when the kept copy holds all of it.**
  The frame check now samples the copy that would be deleted, from 45 s in to 75 s before its
  end, and needs every point found in the kept copy on one straight line, at the speed the two
  frame rates predict (±0.6 %), with at most 10 s left over at the edges. This replaces the
  fixed "25/23.976 ± 0.6 %" running-time test for episodes: 8 English-only Vampire Diaries
  copies (0.03 % outside it) and a Czech NCIS 480p shorter than its kept copy now go, while a
  copy with a TV lead-in (61 s), a double-episode file, or extra minutes spread through the
  episode (NCIS S05E15) stays. Films keep the PAL-only rule, so two cuts of a film both stay.
  Saved frame-check verdicts are measured again once (method version 4).

### Fixed

- A frame-check search window that would start before 0 s is now read from 0 with matching
  frame times (ffmpeg clamped it, so the offsets came out shifted).

## [3.4.0] — 2026-10-12

### Added

- **The dedupe report opens with a verdict and one row per title and outcome.** One sentence
  says what happened ("191 files removed, 152.56 GB freed." / "Nothing would be removed."), then
  an outcome bar whose legend is the page navigation (Check first, Failed, Removed, Blocked by
  guard, Excluded, each with its count) and a folded "Next steps" line with the `--exclude-ids`
  value for every guarded pair. The nine "Summary Statistics" tiles are gone. Sections follow in
  risk order: Check first (risky deletions, open by default), Failed, Removed, Blocked by the
  safety guard, Skipped by `--exclude-ids`; every header carries file counts and sizes. A row is
  two calm lines: "Call the Midwife · S12 · 8 files · 10.54 GB · blocked in 6 runs" and only what
  differs between the copies ("1080p H264 HDTV vs 1080i H264 HDTV · cs vs eng audio · runtime
  52 vs 58 min (×1.12, different cut)"), plus short chips when there is something to say
  ("scores lower", "loses eng", "loses HDR", "'(n)' name", "different folders", "name lies"),
  amber on a deletion, grey on a blocked pair, the full sentence on hover. Opening a row says
  the facts that are the same for every file once (folder, kept copy, other copy, the guard's
  reason or the error) and then one table line per episode: the part of each file name that
  differs, runtime, size and score for both copies, Emby links; a file whose fact differs from
  the rest of the row carries that fact as a small mark. A file name that claims a resolution
  the video does not have is flagged. The `--exclude-ids` hint names the IMDb id only (a bare
  number matches TMDB and TVDB alike) and appears where a decision is still open. The header
  says mode, time and libraries; version, run id, host, the exact command (credentials redacted),
  the JSON twin and a reconciling sentence live in the footer. Every section and row has an
  `id`, rows and files carry `data-outcome` / `data-reason` / `data-flags` / `data-item-id`, and
  the same facts sit in a `#report-data` JSON island and in a `.json` twin written beside the
  report (its SHA-256 is in the footer). Blocked pairs are remembered in
  `~/.emby-dedupe/dedupe-history.json` (`DEDUPE_HISTORY_FILE`), so a row can say "blocked in
  6 runs". Season rows show the series poster; the other copy shows its own poster only when it
  differs from the kept one. The per-group cards are gone: their facts live in the rows. Print
  opens every row on a light page; the layout stacks at phone width.

- **`csfd fill --ai-match` can ask Claude, with Jev as a cross-check.** The new `--ai-provider auto|jev|claude|both`
  chooses the backend; `auto` (the default) uses every backend that has a key, so existing setups are unchanged.
  Claude Haiku 5.5 is asked 5 times, and the share of votes is its confidence. On 43 hand-checked titles it
  found 30 of 31 matches, against Jev's 19, but 2 of its picks were wrong, both unanimous. Every pick both models
  agreed on was right (19 of 19). With `both`, each review line is tagged `both-agree`, `claude-only`, `jev-only`
  or `conflict`, and `--ai-auto` applies agreed picks only. With Claude alone, `--ai-auto` is refused. Install
  with `pip install 'emby-curator[claude]'` and set `DEDUPE_ANTHROPIC_API_KEY`.

- **Dedupe compares the pictures before it deletes an episode.** Running time, season and
  episode number cannot tell two episodes of similar length apart: a Slovak NCIS S19E08 that was
  really E07 (swapped in the source pack) matched the Czech E08 within 2 %, and the Czech copy was
  deleted. Before deleting an episode, dedupe now takes keyframes of the kept copy at up to five
  points, finds their twins in the other copy (sampled 4 times a second) and needs most of them to
  line up at one time offset. Different or unclear pictures keep both copies. On 134 live pairs,
  every different-episode control scored 0.0-0.1 and every real pair 0.5-1.0. With
  `--frame-check-host user@host` (`DEDUPE_FRAME_CHECK_HOST`) ffmpeg runs on the media host next to
  the files and only 32x18 frames come back (about 1-3.5 s per pair); without it the copies are streamed
  from Emby (~0.5 GB per 1080p pair). Verdicts are saved in `~/.emby-dedupe/frame-check-cache.json`
  (`DEDUPE_FRAME_CHECK_CACHE`) and reused while both files stay the same, so a pair the guard keeps
  is checked once, not every run. The stream always names the item's own media source: Emby
  otherwise serves a version group's default copy for every item in the group. It needs `ffmpeg`;
  without it, or with `--no-frame-check` (`DEDUPE_FRAME_CHECK=false`), the other checks decide alone.
- **A PAL speed-up is no longer blocked as a different cut.** A 25 fps copy of a 23.976/24 fps show
  runs 4.2 % shorter, so 42 Slovak/Czech NCIS pairs were refused every run. Such a pair now goes
  when the frame rates and the running-time ratio fit a speed-up, the pictures match, and the time
  offset stays flat across the episode (within 15 s). A copy whose offset keeps growing is longer
  for another reason, so it is still kept: the extra minutes may hold scenes the other copy lacks.

### Changed

- **`cleanup` removes a collection (franchise) whole or not at all.** Collections are Emby BoxSets
  with a TMDb id. If any owned part is protected, new or not yet scanned, every part stays. A
  delete that is blocked by the safety guard or fails keeps the rest of its collection. A film that
  is the only owned part is judged like any other film. The console, JSON and HTML reports show the
  collection and count kept and removed collections.

### Fixed

- **Dedupe paired episodes of two different shows with the same name.** Episodes without
  provider ids were matched by series name, season and episode, so the 2005 *Avatar* cartoon and
  the 2024 live-action show paired. The key now includes the Emby series id.
- **Dedupe could delete an episode whose copy had no running time yet.** A download that Emby
  had not probed yet passed the running-time check. Either copy without a running time now keeps
  both.
- **The pre-download check matched an episode to a film with the same id number.** TMDb and TVDB
  number films and shows separately (NCIS is TMDb 4614, and so is *The Sum of All Fears*); an
  episode check now only looks at episodes.
- **"AI Upscale" with a space was not detected as an AI upscale.**
- **A dry run now shows what `--doit` would really do.** The safety guard already ran in a dry
  run, but its verdict only reached the log, so a dry run promised every deletion (one promised
  30 deletions and 42 GB; the real run blocked 24). Each delete the guard would refuse is now
  marked "Would be blocked by safety guard" with its reason, the summary shows "Would Delete" /
  "Would Be Blocked", and the space to free leaves the blocked ones out. Dry runs also stopped
  reporting "Size Removed 0 B" (every dry-run item was counted as an attempted deletion).
- **Dedupe reports are about 60% smaller.** Each distinct poster is embedded once (as a CSS
  class) instead of once per card, and posters are fetched at JPEG quality 80 instead of 90. A
  333-group report was 29.6 MB, 27.5 MB of it 670 posters of which 336 were distinct.
- **A long `--exclude-ids` list no longer buries the dedupe report.** The summary now shows the count
  ("52 provider IDs excluded … 27 duplicate groups skipped this run") with the IDs folded underneath.
  The "Excluded Media" section shows only the groups an excluded ID actually skipped, folded too,
  instead of one card per configured ID.
- **`genres origin` tagged a Polish series as Czech.** TMDb lists *The Thaw* (*Odwilż*) as a
  Polish–Czech production only because HBO Europe, which made it, is registered in Prague. A
  co-production whose only Czech/Slovak company is HBO Europe no longer counts. TMDb answers cached
  without the production companies are fetched again once.
- **The pre-download check (`check`, used by the torrents scraper) let a worse 4K copy
  through.** A proposed copy only states its resolution class, so "2160p" was scored as a full
  16:9 frame (3840×2160). A scope film is 3840×1600 in every release, and the copy already in the
  library was scored at that real size. That handed the torrent about 21 points of resolution it
  doesn't have: a 4.8 GB / 5.9 Mbps *The Voyeurs* beat the 13.9 GB / 15.9 Mbps copy already owned,
  was downloaded, and dedupe then deleted it. When an existing copy is in the same width class
  (within 2%), the proposed copy now takes its frame size before scoring.
- **The pre-download check matched a show by a word inside another title.** The series name
  fallback accepted any title that merely contained the requested one, so *Eden* resolved to
  *Welcome to Eden* or *East of Eden* and missing episodes looked already downloaded. A partial
  match now has to be a subtitle ("Eden" → "Eden - Du bezahlst für jede Lüge") or differ only by
  a possessive brand ("Daredevil" → "Marvel's Daredevil"), unless the candidate carries the very
  provider id the caller supplied.

## [3.3.0] — 2026-10-02

### Added

- **`genres origin`**: gives Czech and Slovak movies and TV series one extra genre (default
  `Česko-slovenský`, set with `--genre-name`), so they can be browsed in Emby's Genres view. A title
  counts when TMDb gives its original language as Czech or Slovak, or when it is a non-English
  co-production with Czechia, Slovakia or Czechoslovakia. English-language films that TMDb lists as
  Czech only because they were shot in Prague don't count. Titles TMDb doesn't know fall back to the
  first country of origin on ČSFD. A film whose Emby metadata names only other countries is skipped,
  because Emby then holds the wrong TMDb id. Dry run by default. Existing genres are kept, and the
  command never removes the genre, so one added by hand is kept too. Each run lists the tagged titles
  the rules no longer back, for a person to review. `genres audit --suggest` no longer flags the genre
  as unknown.
- The genre webhook listener runs `genres origin` on newly added items, right after `genres process`.

### Fixed

- **ČSFD gave a series' country as `Česko (`.** On a series page the year range sits in brackets
  after the country, and the bracket stayed in the country name. This affected the country shown to
  `csfd fill --ai-match`, and it would have kept Czech series out of `genres origin`.

## [3.2.0] — 2026-10-01

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

- **Dedupe deleted distinct episodes stored as one folder per season** (data loss, 2026-09-27).
  Duplicates were keyed on Emby's season/episode and the file name's SxxEyy only; the season
  **folder** was ignored. A series with one folder per stage but every file named `S01Exx`
  (*Initial D*: `S05/… S01E10`, `S04/… S01E10 (3)`) therefore had different episodes grouped and
  deleted. Now:
  - the season folder (`S05`, `Season 5`, `7. Série`, `Staffel 5`, season-pack folders like
    `Show.S02.1080p`) is part of the grouping key, so files in different season folders never group;
  - a delete-time backstop refuses any delete whose season folders disagree, or whose file sits in a
    season folder that contradicts its own SxxEyy;
  - the same backstop refuses a delete when the running times differ beyond tolerance (Emby had filed
    *Rust*, 140 min, under *Runt*'s id, 92 min; a "(2)" copy of an episode was 18.6 vs 22.5 min).
  These refusals never mark the item for `--fold-safe-delete`, so it cannot remove them file-only either.
- **Language priority deleted a better copy whose audio language was just untagged** (*Count Duckula*:
  a Czech 720p BluRay with no audio tag was deleted to keep a tagged English 480p). When the best-quality
  copy's language is unknown (`unknown` or `und`), the group is now left alone.
- **Dedupe report summary under-counted deletions and over-counted space.** Duplicates removed
  file-only by `--fold-safe-delete` were counted as "skipped". "Size Removed" also added up every
  planned duplicate, including the ones the safety guard kept on disk. A real run's report said "10
  deleted, 12 skipped, 26 GB removed", when the truth was 14 deleted and 8 kept. Only files actually
  removed now count, and a dry run's figure is labelled "Size to Remove".
- **Cleanup report showed no poster for the items it deleted.** The report is rendered after the
  deletions, when Emby no longer has those images. Their posters are now fetched just before deleting.
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
