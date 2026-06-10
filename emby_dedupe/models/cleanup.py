"""
Data models for the library cleanup feature.

Holds the configuration and candidate dataclasses shared by the cleanup
pipeline (emby_dedupe.api.cleanup_pipeline), report formatting
(emby_dedupe.reports.cleanup) and the CLI entry point
(emby_dedupe.cli.cleanup).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_PROTECT_PATH = "/Dokumenty/"

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class CleanupConfig:
    """Configuration for the library cleanup pipeline.

    Args:
        min_age_years: Movie must be at least this old (by DateCreated) to be eligible.
        protect_paths: Path substrings that protect a movie (e.g., "/Dokumenty/").
        base_rating: Minimum required CommunityRating at min_age_years.
        decay_step: Rating requirement increase per year over min_age_years.
        max_rating: Cap on required rating (reached after enough years).
        top_actors: Number of top actors from primary user's watch history to protect.
        excluded_provider_ids: Set of provider ID values (IMDB/TMDB/TVDB) to skip.
    """

    min_age_years: int = 3
    protect_paths: list[str] = field(default_factory=lambda: [_DEFAULT_PROTECT_PATH])
    base_rating: float = 6.0
    decay_step: float = 0.5
    max_rating: float = 8.0
    no_actor_protection_after_years: int = 10
    masterpiece_only_after_years: int = 12
    masterpiece_rating: float = 9.0
    excluded_provider_ids: set[str] = field(default_factory=set)
    near_miss_count: int = 5


@dataclass
class CleanupCandidate:
    """A movie identified as a cleanup candidate after passing all filter layers.

    Args:
        item_id: Emby item ID.
        name: Movie title.
        year: Production year (None if unknown).
        rating: CommunityRating from Emby (None = unrated, distinct from 0.0).
        critic_rating: CriticRating from Emby on 0-100 scale (None if absent).
        threshold: Computed age-decay rating threshold.
        age_years: Age in years since DateCreated.
        library: Library name.
        size_bytes: File size in bytes (0 if Size is None/missing).
        path: File system path on the Emby server.
        deletion_result: Result dict from delete_item() if --doit was used.
    """

    item_id: str
    name: str
    year: Optional[int]
    rating: Optional[float]
    critic_rating: Optional[float]
    threshold: float
    age_years: float
    library: str
    size_bytes: int
    path: str
    deletion_result: Optional[dict] = None
    days_left: Optional[int] = None


@dataclass
class SeriesCleanupCandidate:
    """A TV series identified as a cleanup candidate after passing all filter layers.

    Uses staleness (years since last episode added) instead of movie age.

    Args:
        item_id: Emby series item ID.
        name: Series title.
        year: Production year (None if unknown).
        rating: CommunityRating from Emby (None = unrated).
        critic_rating: CriticRating from Emby on 0-100 scale (None if absent).
        threshold: Computed staleness-decay rating threshold.
        stale_years: Years since the last episode was added to Emby.
        last_episode_added: ISO date string of the most recently added episode.
        episode_count: Total episode count (RecursiveItemCount).
        library: Library name.
        size_bytes: Total size of all episodes in bytes.
        path: Series root path on the Emby server.
        deletion_result: Result dict from delete_item() if --doit was used.
    """

    item_id: str
    name: str
    year: Optional[int]
    rating: Optional[float]
    critic_rating: Optional[float]
    threshold: float
    stale_years: float
    last_episode_added: Optional[str]
    episode_count: int
    library: str
    size_bytes: int
    path: str
    deletion_result: Optional[dict] = None
    days_left: Optional[int] = None
