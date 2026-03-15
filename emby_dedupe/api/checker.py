"""
EmbyChecker - Main Python library interface for checking media quality.

Usage:
    from emby_dedupe.api.checker import EmbyChecker

    # From config file
    checker = EmbyChecker.from_config()

    # Manual configuration
    checker = EmbyChecker(
        host="https://emby.example.com",
        api_key="your-api-key",
        libraries=["Movies", "TV Shows"],
        lang_priorities=["sk", "cs", "en"],
    )

    # Check a movie
    result = checker.check(name="Inception", year=2010, resolution="2160p")
    if result.should_download:
        logger.info("Download it!")

    # Simple boolean check
    should_dl = checker.should_download("Inception", year=2010, resolution="2160p")
"""

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import httpx

from emby_dedupe.api.client import (
    fetch_and_process_media_items,
    fetch_items_details,
    get_library_id,
)
from emby_dedupe.api.quality_compare import (
    ComparisonResult,
    ProposedQuality,
    compare_quality,
)
from emby_dedupe.api.search import SEARCH_FIELDS, search_media
from emby_dedupe.utils.http import make_http_request
from emby_dedupe.utils.config import Config, ensure_cache_dir
from emby_dedupe.utils.logging import logger


@dataclass
class CheckConfig:
    """Configuration for media quality check.

    Bundles all parameters needed for check() and should_download() methods.
    """

    name: Optional[str] = None
    year: Optional[int] = None
    imdb: Optional[str] = None
    tmdb: Optional[str] = None
    tvdb: Optional[str] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    resolution: Optional[str] = None
    codec: Optional[str] = None
    hdr: Optional[str] = None
    audio: Optional[str] = None
    audio_languages: Optional[list[str]] = None
    size_mb: Optional[int] = None
    bitrate_kbps: Optional[int] = None
    path: Optional[str] = None
    source_quality_tier: Optional[str] = None


class EmbyChecker:
    """Main interface for checking if media should be downloaded."""

    def __init__(
        self,
        host: Optional[str] = None,
        api_key: Optional[str] = None,
        libraries: Optional[list[str]] = None,
        lang_priorities: Optional[list[str]] = None,
        exclude_ids: Optional[list[str]] = None,
        use_cache: bool = True,
        cache_ttl_minutes: int = 10,
        config: Optional[Config] = None,
    ):
        """Initialize EmbyChecker.

        Args:
            host: Emby server URL.
            api_key: Emby API key.
            libraries: List of libraries to search. None = all libraries.
            lang_priorities: Language priority list (e.g., ['sk', 'cs', 'en']).
            exclude_ids: Provider IDs to exclude from checking.
            use_cache: Whether to cache library data.
            cache_ttl_minutes: Cache TTL in minutes.
            config: Optional Config object to use instead of individual params.
        """
        if config:
            self.host = config.host
            self.api_key = config.api_key
            self.libraries = config.libraries
            self.lang_priorities = config.lang_priorities
            self.exclude_ids = config.exclude_ids or []
            self.use_cache = config.cache_enabled
            self.cache_ttl_minutes = config.cache_ttl_minutes
        else:
            self.host = host
            self.api_key = api_key
            self.libraries = libraries
            self.lang_priorities = lang_priorities
            self.exclude_ids = exclude_ids or []
            self.use_cache = use_cache
            self.cache_ttl_minutes = cache_ttl_minutes

        self._client: Optional[httpx.Client] = None
        self._cache_dir: Optional[Path] = None
        self._provider_tables: Optional[dict] = None  # Cached provider ID tables

    @classmethod
    def from_config(cls, **overrides) -> 'EmbyChecker':
        """Create EmbyChecker from config file.

        Args:
            **overrides: Values to override from config file.

        Returns:
            EmbyChecker instance.
        """
        config = Config.from_config_file(**overrides)
        return cls(config=config)

    def _ensure_config(self) -> tuple[str, str]:
        """Ensure host and api_key are configured and return them."""
        if not self.host or not self.api_key:
            msg = "EmbyChecker requires host and api_key to be configured"
            raise ValueError(msg)
        return self.host, self.api_key

    def _get_client(self) -> httpx.Client:
        """Get or create HTTP client."""
        if self._client is None:
            # Use shorter timeouts for faster fallback to name search
            # connect=10s, read=15s, write=10s, pool=10s
            timeout = httpx.Timeout(
                connect=10.0,
                read=15.0,  # Provider ID searches can be slow, but not too long
                write=10.0,
                pool=10.0
            )
            # Add API key to headers for authenticated requests
            headers = {"X-Emby-Token": self.api_key} if self.api_key else {}
            self._client = httpx.Client(timeout=timeout, headers=headers)
        return self._client

    def _get_cache_path(self, cache_key: str) -> Path:
        """Get path for a cache file."""
        if self._cache_dir is None:
            self._cache_dir = ensure_cache_dir()
        return self._cache_dir / f"{cache_key}.json"

    def _get_provider_tables_cache_path(self) -> Path:
        """Get path for provider tables cache."""
        if self._cache_dir is None:
            self._cache_dir = ensure_cache_dir()
        return self._cache_dir / "provider_tables.json"

    def _load_provider_tables(self) -> Optional[dict]:
        """Load cached provider ID tables."""
        if not self.use_cache:
            return None

        cache_path = self._get_provider_tables_cache_path()
        if not cache_path.exists():
            return None

        try:
            with open(cache_path, 'r') as f:
                data = json.load(f)

            # Check TTL
            cached_time = data.get("timestamp", 0)
            ttl_seconds = self.cache_ttl_minutes * 60
            if time.time() - cached_time > ttl_seconds:
                logger.debug("Provider tables cache expired")
                return None

            logger.info("Using cached provider ID tables for instant IMDB lookups")
            return data.get("tables")

        except Exception as e:
            logger.warning(f"Error loading provider tables cache: {e}")
            return None

    def _save_provider_tables(self, tables: dict) -> None:
        """Save provider ID tables to cache."""
        if not self.use_cache:
            return

        cache_path = self._get_provider_tables_cache_path()
        try:
            with open(cache_path, 'w') as f:
                json.dump({
                    "timestamp": time.time(),
                    "tables": tables,
                }, f)
            logger.info("Saved provider ID tables to cache")
        except Exception as e:
            logger.warning(f"Error saving provider tables cache: {e}")

    def _get_library_names(self, client) -> list:
        """Get list of library names to process."""
        if self.libraries:
            return self.libraries

        from emby_dedupe.api.search import get_all_library_ids
        host, api_key = self._ensure_config()
        all_libs = get_all_library_ids(client, host, api_key)
        return [lib["name"] for lib in all_libs]

    def _merge_provider_tables(self, all_tables: dict, tables: dict) -> None:
        """Merge library tables into all_tables (in-place)."""
        for provider in ["imdb", "tvdb", "tmdb", "series_episode"]:
            for pid, items in tables[provider].items():
                if pid not in all_tables[provider]:
                    all_tables[provider][pid] = []
                all_tables[provider][pid].extend(items)

    def _build_provider_tables(self) -> dict:
        """Build provider ID tables from configured libraries.

        Returns:
            dict: Provider tables with 'imdb', 'tvdb', 'tmdb' keys.
        """
        logger.info("Building provider ID index from libraries (this may take 30-60s)...")

        client = self._get_client()
        host, _ = self._ensure_config()
        all_tables: dict[str, dict] = {"imdb": {}, "tvdb": {}, "tmdb": {}, "series_episode": {}}

        library_names = self._get_library_names(client)

        # Fetch from each library and build tables
        for lib_name in library_names:
            logger.info(f"  Fetching items from library: {lib_name}")
            try:
                lib_id = get_library_id(client, host, lib_name)
                if not lib_id:
                    logger.warning(f"  Library '{lib_name}' not found, skipping")
                    continue

                tables = fetch_and_process_media_items(client, host, lib_id, lib_name)
                self._merge_provider_tables(all_tables, tables)

            except Exception as e:
                logger.warning(f"  Error fetching library '{lib_name}': {e}")
                continue

        # Count total provider IDs
        total_imdb = len(all_tables["imdb"])
        total_tmdb = len(all_tables["tmdb"])
        total_tvdb = len(all_tables["tvdb"])
        total_se = len(all_tables["series_episode"])
        logger.info(f"Provider ID index built: {total_imdb} IMDB, {total_tmdb} TMDB, {total_tvdb} TVDB, {total_se} series-episode groups")

        return all_tables

    def _get_provider_tables(self) -> dict:
        """Get provider ID tables (from cache or build new)."""
        if self._provider_tables is not None:
            return self._provider_tables

        # Try to load from cache
        self._provider_tables = self._load_provider_tables()

        if self._provider_tables is None:
            # Build new tables
            self._provider_tables = self._build_provider_tables()
            # Save to cache
            self._save_provider_tables(self._provider_tables)

        return self._provider_tables

    def _lookup_by_provider_id(self, provider_id: str, provider_type: str = "imdb") -> list[dict]:
        """Fast lookup by provider ID using cached tables.

        Args:
            provider_id: Provider ID (e.g., 'tt1375666').
            provider_type: Provider type ('imdb', 'tmdb', 'tvdb').

        Returns:
            List of item IDs with this provider ID.
        """
        tables = self._get_provider_tables()
        provider_table = tables.get(provider_type.lower(), {})

        # Case-insensitive lookup
        item_ids = provider_table.get(provider_id.lower(), [])

        if not item_ids:
            return []

        # Extract just the IDs (tables store dicts with 'id' key)
        if isinstance(item_ids[0], dict):
            item_ids = [item['id'] for item in item_ids]

        # Fetch full item details
        client = self._get_client()
        host, _ = self._ensure_config()
        items = fetch_items_details(client, host, item_ids)
        return items

    def _get_from_cache(self, cache_key: str) -> Optional[list[dict]]:
        """Get data from cache if valid."""
        if not self.use_cache:
            return None

        cache_path = self._get_cache_path(cache_key)
        if not cache_path.exists():
            return None

        try:
            with open(cache_path, 'r') as f:
                data = json.load(f)

            # Check TTL
            cached_time = data.get("timestamp", 0)
            ttl_seconds = self.cache_ttl_minutes * 60
            if time.time() - cached_time > ttl_seconds:
                logger.debug(f"Cache expired for {cache_key}")
                return None

            logger.debug(f"Cache hit for {cache_key}")
            return data.get("items", [])

        except Exception as e:
            logger.warning(f"Error reading cache: {e}")
            return None

    def _save_to_cache(self, cache_key: str, items: list[dict]) -> None:
        """Save data to cache."""
        if not self.use_cache:
            return

        cache_path = self._get_cache_path(cache_key)
        try:
            with open(cache_path, 'w') as f:
                json.dump({
                    "timestamp": time.time(),
                    "items": items,
                }, f)
            logger.debug(f"Saved to cache: {cache_key}")
        except Exception as e:
            logger.warning(f"Error saving to cache: {e}")

    def _make_cache_key(self, **params) -> str:
        """Generate a cache key from search parameters."""
        # Create a deterministic hash of the parameters
        key_data = json.dumps(params, sort_keys=True)
        return hashlib.md5(key_data.encode()).hexdigest()[:16]

    def validate(self) -> list[str]:
        """Validate configuration.

        Returns:
            List of validation errors. Empty if valid.
        """
        errors = []
        if not self.host:
            errors.append("host is required")
        if not self.api_key:
            errors.append("api_key is required")
        return errors

    def rebuild_index(self) -> None:
        """Rebuild the provider ID index from scratch.

        This will:
        1. Clear cached provider tables
        2. Fetch all items from configured libraries
        3. Build new provider ID index
        4. Save to cache

        Use this when you want to refresh the index with newly added content.
        """
        logger.info("Rebuilding provider ID index...")

        # Clear cached tables
        self._provider_tables = None
        cache_path = self._get_provider_tables_cache_path()
        if cache_path.exists():
            cache_path.unlink()
            logger.debug("Cleared cached provider tables")

        # Build new tables (will automatically cache)
        self._get_provider_tables()

        logger.info("Provider ID index rebuilt successfully")

    def _lookup_by_any_provider_id(self, imdb: Optional[str], tmdb: Optional[str], tvdb: Optional[str]) -> Optional[list]:
        """Try to lookup items by provider ID (IMDB > TMDB > TVDB priority)."""
        if imdb:
            logger.debug(f"Looking up IMDB ID: {imdb}")
            items = self._lookup_by_provider_id(imdb, "imdb")
            if items:
                logger.debug(f"Found {len(items)} items via IMDB lookup")
                return items

        if tmdb:
            logger.debug(f"Looking up TMDB ID: {tmdb}")
            items = self._lookup_by_provider_id(tmdb, "tmdb")
            if items:
                logger.debug(f"Found {len(items)} items via TMDB lookup")
                return items

        if tvdb:
            logger.debug(f"Looking up TVDB ID: {tvdb}")
            items = self._lookup_by_provider_id(tvdb, "tvdb")
            if items:
                logger.debug(f"Found {len(items)} items via TVDB lookup")
                return items

        return None

    def _find_validated_series(
        self, client: httpx.Client, host: str, pid: str, ptype: str,
    ) -> Optional[dict]:
        """Search Emby for a series matching the given provider ID.

        Emby may ignore the AnyXxxId filter for Series items and return
        ALL series. This method validates that the returned series actually
        has the expected provider ID.

        Returns:
            Matching series dict, or None if not found.
        """
        url = f"{host}/Items"
        params = {
            f"Any{ptype}Id": pid,
            "IncludeItemTypes": "Series",
            "Recursive": "true",
            "Fields": "ProviderIds",
        }

        response = make_http_request(client, "GET", url, params=params)
        series_items = response.json().get("Items", [])

        for candidate in series_items:
            candidate_pids = candidate.get("ProviderIds", {})
            if candidate_pids.get(ptype, "").lower() == pid.lower():
                return candidate

        if series_items:
            logger.debug(
                f"No series with {ptype} ID {pid} found "
                f"(API returned {len(series_items)} unrelated series)"
            )
        return None

    def _fetch_episode_from_series(
        self, client: httpx.Client, host: str, series: dict,
        season: int, episode: int, ptype: str,
    ) -> list[dict]:
        """Fetch a specific episode from a known series.

        Returns:
            List of matching episodes (may be empty).
        """
        series_id = series["Id"]
        series_name = series.get("Name", "Unknown")

        url = f"{host}/Items"
        ep_params = {
            "ParentId": series_id,
            "IncludeItemTypes": "Episode",
            "Recursive": "true",
            "Fields": SEARCH_FIELDS,
        }

        response = make_http_request(client, "GET", url, params=ep_params)
        episodes = response.json().get("Items", [])

        matching = [
            ep for ep in episodes
            if ep.get("ParentIndexNumber") == season
            and ep.get("IndexNumber") == episode
        ]

        for ep in matching:
            if not ep.get("SeriesName"):
                ep["SeriesName"] = series_name

        label = f"S{season:02d}E{episode:02d}"
        if matching:
            logger.debug(f"Found {label} in series '{series_name}' via {ptype} provider ID fallback")
        else:
            logger.debug(f"{label} not found in series '{series_name}'")

        return matching

    def _lookup_episode_via_series(
        self,
        imdb: Optional[str],
        tmdb: Optional[str],
        tvdb: Optional[str],
        season: int,
        episode: int,
    ) -> Optional[list[dict]]:
        """Find episode by searching for the parent series via provider ID.

        The cached provider tables only index non-folder items (episodes).
        Individual episodes often don't carry the series-level IMDB ID in their
        own ProviderIds — that ID lives on the Series item. This method queries
        the Emby API directly to find the series, then looks up the episode.

        Args:
            imdb: IMDB ID (series-level).
            tmdb: TMDB ID (series-level).
            tvdb: TVDB ID (series-level).
            season: Season number.
            episode: Episode number.

        Returns:
            List of matching episodes if series found (may be empty if episode
            doesn't exist in that series). None if no series found via any
            provider ID.
        """
        client = self._get_client()
        host, _ = self._ensure_config()

        for pid, ptype in [(imdb, "Imdb"), (tmdb, "Tmdb"), (tvdb, "Tvdb")]:
            if not pid:
                continue

            logger.debug(f"Series fallback: searching for series via {ptype} ID: {pid}")

            try:
                series = self._find_validated_series(client, host, pid, ptype)
                if series is None:
                    continue

                logger.debug(
                    f"Found series '{series.get('Name', 'Unknown')}' "
                    f"(ID: {series['Id']}) via {ptype} ID"
                )

                # Series was found — return result even if empty (episode doesn't exist)
                return self._fetch_episode_from_series(
                    client, host, series, season, episode, ptype
                )

            except Exception as e:
                logger.debug(f"Series provider ID lookup failed ({ptype}): {e}")
                continue

        return None  # No series found via any provider ID

    def _search_by_name(self, name: str, year: Optional[int], season: Optional[int], episode: Optional[int]) -> list:
        """Search for existing media by name with caching."""
        logger.debug(f"Provider ID not found or not provided, searching by name: {name}")

        cache_key = self._make_cache_key(name=name, year=year, season=season, episode=episode, libraries=self.libraries)
        existing_items = self._get_from_cache(cache_key)

        if existing_items is None:
            client = self._get_client()
            host, api_key = self._ensure_config()
            existing_items = search_media(
                client=client,
                host=host,
                api_key=api_key,
                name=name,
                year=year,
                imdb=None,
                tmdb=None,
                tvdb=None,
                season=season,
                episode=episode,
                library_names=self.libraries,
                skip_provider_search=True,
            )
            self._save_to_cache(cache_key, existing_items)

        return existing_items

    def check(
        self,
        config: Optional[CheckConfig] = None,
        **kwargs
    ) -> ComparisonResult:
        """Check if media should be downloaded.

        Args:
            config: CheckConfig object with all parameters (preferred).
            **kwargs: Individual parameters (name, year, imdb, tmdb, tvdb, season,
                episode, resolution, codec, hdr, audio, audio_languages, size_mb,
                bitrate_kbps, path, source_quality_tier).

        Returns:
            ComparisonResult with recommendation.
        """
        # Use config object if provided, otherwise use individual parameters from kwargs
        if config:
            name = config.name
            year = config.year
            imdb = config.imdb
            tmdb = config.tmdb
            tvdb = config.tvdb
            season = config.season
            episode = config.episode
            resolution = config.resolution
            codec = config.codec
            hdr = config.hdr
            audio = config.audio
            audio_languages = config.audio_languages
            size_mb = config.size_mb
            bitrate_kbps = config.bitrate_kbps
            path = config.path
            source_quality_tier = config.source_quality_tier
        else:
            # Extract from kwargs
            name = kwargs.get('name')
            year = kwargs.get('year')
            imdb = kwargs.get('imdb')
            tmdb = kwargs.get('tmdb')
            tvdb = kwargs.get('tvdb')
            season = kwargs.get('season')
            episode = kwargs.get('episode')
            resolution = kwargs.get('resolution')
            codec = kwargs.get('codec')
            hdr = kwargs.get('hdr')
            audio = kwargs.get('audio')
            audio_languages = kwargs.get('audio_languages')
            size_mb = kwargs.get('size_mb')
            bitrate_kbps = kwargs.get('bitrate_kbps')
            path = kwargs.get('path')
            source_quality_tier = kwargs.get('source_quality_tier')

        # Validate configuration
        errors = self.validate()
        if errors:
            raise ValueError(f"Invalid configuration: {', '.join(errors)}")

        # Check if provider ID is excluded
        for provider_id in [imdb, tmdb, tvdb]:
            if provider_id and provider_id in self.exclude_ids:
                logger.info(f"Skipping excluded provider ID: {provider_id}")
                return ComparisonResult(
                    recommendation="skip",
                    reason="excluded_id",
                    status="excluded",
                )

        # Create proposed quality object
        proposed = ProposedQuality(
            resolution=resolution,
            codec=codec,
            hdr=hdr,
            audio=audio,
            audio_languages=audio_languages,
            size_mb=size_mb,
            bitrate_kbps=bitrate_kbps,
            path=path,
            name=name,
            source_quality_tier=source_quality_tier,
        )

        # Try provider ID lookup first (instant with cached tables)
        existing_items = self._lookup_by_any_provider_id(imdb, tmdb, tvdb)

        # Fallback for TV episodes: cached tables only index episodes (IsFolder=False),
        # so series-level provider IDs (e.g., IMDB on the Series item) won't be found.
        # Search the Emby API directly for the series by provider ID, then find the episode.
        if not existing_items and season is not None and episode is not None:
            series_result = self._lookup_episode_via_series(
                imdb, tmdb, tvdb, season, episode
            )
            if series_result is not None:
                existing_items = series_result

        # Fall back to name search only if no provider-based result found
        if existing_items is None and name:
            existing_items = self._search_by_name(name, year, season, episode)
        if not existing_items:
            existing_items = []

        # Compare quality
        return compare_quality(proposed, existing_items, self.lang_priorities)

    def should_download(
        self,
        config: Optional[CheckConfig] = None,
        **kwargs
    ) -> bool:
        """Check if media should be downloaded (simple boolean interface).

        Returns True if:
        - Media doesn't exist in Emby (not found)
        - Proposed quality is better than existing

        Returns False if:
        - Existing media is same or better quality
        - Provider ID is excluded

        Args:
            config: CheckConfig object with all parameters (preferred).
            **kwargs: Individual parameters (same as check()).

        Returns:
            True if should download, False otherwise.
        """
        # Use config if provided, otherwise pass kwargs to check
        if config:
            result = self.check(config=config)
        else:
            result = self.check(**kwargs)
        return result.should_download

    def check_batch(
        self,
        items: list[dict[str, Any]],
    ) -> list[ComparisonResult]:
        """Check multiple items at once.

        Args:
            items: List of dicts with check parameters.

        Returns:
            List of ComparisonResults.
        """
        results = []
        for item in items:
            result = self.check(**item)
            results.append(result)
        return results

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None

    def __enter__(self) -> 'EmbyChecker':
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.close()
