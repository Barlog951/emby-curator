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
        print("Download it!")

    # Simple boolean check
    should_dl = checker.should_download("Inception", year=2010, resolution="2160p")
"""

import hashlib
import json
import time
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
    ExistingQuality,
    ProposedQuality,
    compare_quality,
)
from emby_dedupe.api.search import search_media
from emby_dedupe.utils.config import Config, ensure_cache_dir
from emby_dedupe.utils.logging import logger


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

    def _build_provider_tables(self) -> dict:
        """Build provider ID tables from configured libraries.

        Returns:
            dict: Provider tables with 'imdb', 'tvdb', 'tmdb' keys.
        """
        logger.info("Building provider ID index from libraries (this may take 30-60s)...")

        client = self._get_client()
        all_tables = {"imdb": {}, "tvdb": {}, "tmdb": {}}

        # Determine which libraries to fetch
        if self.libraries:
            library_names = self.libraries
        else:
            # Get all libraries
            from emby_dedupe.api.search import get_all_library_ids
            all_libs = get_all_library_ids(client, self.host, self.api_key)
            library_names = [lib["name"] for lib in all_libs]

        # Fetch from each library and build tables
        for lib_name in library_names:
            logger.info(f"  Fetching items from library: {lib_name}")
            try:
                # Get library ID
                lib_id = get_library_id(client, self.host, lib_name)
                if not lib_id:
                    logger.warning(f"  Library '{lib_name}' not found, skipping")
                    continue

                # Fetch items and build provider tables
                tables = fetch_and_process_media_items(client, self.host, lib_id, lib_name)

                # Merge tables
                for provider in ["imdb", "tvdb", "tmdb"]:
                    for pid, items in tables[provider].items():
                        if pid not in all_tables[provider]:
                            all_tables[provider][pid] = []
                        all_tables[provider][pid].extend(items)

            except Exception as e:
                logger.warning(f"  Error fetching library '{lib_name}': {e}")
                continue

        # Count total provider IDs
        total_imdb = len(all_tables["imdb"])
        total_tmdb = len(all_tables["tmdb"])
        total_tvdb = len(all_tables["tvdb"])
        logger.info(f"Provider ID index built: {total_imdb} IMDB, {total_tmdb} TMDB, {total_tvdb} TVDB")

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
        items = fetch_items_details(client, self.host, item_ids)
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

    def check(
        self,
        name: Optional[str] = None,
        year: Optional[int] = None,
        imdb: Optional[str] = None,
        tmdb: Optional[str] = None,
        tvdb: Optional[str] = None,
        season: Optional[int] = None,
        episode: Optional[int] = None,
        resolution: Optional[str] = None,
        codec: Optional[str] = None,
        hdr: Optional[str] = None,
        audio: Optional[str] = None,
        audio_languages: Optional[list[str]] = None,
        size_mb: Optional[int] = None,
        bitrate_kbps: Optional[int] = None,
        path: Optional[str] = None,
        source_quality_tier: Optional[str] = None,
    ) -> ComparisonResult:
        """Check if media should be downloaded.

        Args:
            name: Media name (or full torrent filename for auto-detection).
            year: Release year.
            imdb: IMDB ID.
            tmdb: TMDB ID.
            tvdb: TVDB ID.
            season: Season number (for TV).
            episode: Episode number (for TV).
            resolution: Resolution (2160p, 1080p, etc.).
            codec: Video codec (x265, x264, etc.).
            hdr: HDR type (HDR, DV, etc.).
            audio: Audio type (Atmos, DTS-HD, etc.).
            audio_languages: Audio languages in torrent.
            size_mb: File size in MB.
            bitrate_kbps: Bitrate in kbps.
            path: File path (for source quality auto-detection).
            source_quality_tier: Pre-detected source quality tier
                (bluray_remux, bluray, webdl, hdtv, unknown).

        Returns:
            ComparisonResult with recommendation.
        """
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
        existing_items = None
        if imdb:
            logger.debug(f"Looking up IMDB ID: {imdb}")
            existing_items = self._lookup_by_provider_id(imdb, "imdb")
            if existing_items:
                logger.debug(f"Found {len(existing_items)} items via IMDB lookup")
        elif tmdb:
            logger.debug(f"Looking up TMDB ID: {tmdb}")
            existing_items = self._lookup_by_provider_id(tmdb, "tmdb")
            if existing_items:
                logger.debug(f"Found {len(existing_items)} items via TMDB lookup")
        elif tvdb:
            logger.debug(f"Looking up TVDB ID: {tvdb}")
            existing_items = self._lookup_by_provider_id(tvdb, "tvdb")
            if existing_items:
                logger.debug(f"Found {len(existing_items)} items via TVDB lookup")

        # If provider ID lookup didn't find anything, try name search
        if existing_items is None or len(existing_items) == 0:
            if name:
                logger.debug(f"Provider ID not found or not provided, searching by name: {name}")
                # Generate cache key for name search
                cache_key = self._make_cache_key(
                    name=name,
                    year=year,
                    season=season,
                    episode=episode,
                    libraries=self.libraries,
                )

                # Try to get from cache
                existing_items = self._get_from_cache(cache_key)

                if existing_items is None:
                    # Search by name
                    client = self._get_client()
                    existing_items = search_media(
                        client=client,
                        host=self.host,
                        api_key=self.api_key,
                        name=name,
                        year=year,
                        imdb=None,  # Already tried provider ID
                        tmdb=None,
                        tvdb=None,
                        season=season,
                        episode=episode,
                        library_names=self.libraries,
                        skip_provider_search=True,  # Already did provider lookup above
                    )
                    # Save to cache
                    self._save_to_cache(cache_key, existing_items)
            else:
                existing_items = []

        # Compare quality
        return compare_quality(proposed, existing_items, self.lang_priorities)

    def should_download(
        self,
        name: Optional[str] = None,
        year: Optional[int] = None,
        imdb: Optional[str] = None,
        tmdb: Optional[str] = None,
        tvdb: Optional[str] = None,
        season: Optional[int] = None,
        episode: Optional[int] = None,
        resolution: Optional[str] = None,
        codec: Optional[str] = None,
        hdr: Optional[str] = None,
        audio: Optional[str] = None,
        audio_languages: Optional[list[str]] = None,
        size_mb: Optional[int] = None,
        bitrate_kbps: Optional[int] = None,
    ) -> bool:
        """Check if media should be downloaded (simple boolean interface).

        Returns True if:
        - Media doesn't exist in Emby (not found)
        - Proposed quality is better than existing

        Returns False if:
        - Existing media is same or better quality
        - Provider ID is excluded

        Args:
            Same as check().

        Returns:
            True if should download, False otherwise.
        """
        result = self.check(
            name=name,
            year=year,
            imdb=imdb,
            tmdb=tmdb,
            tvdb=tvdb,
            season=season,
            episode=episode,
            resolution=resolution,
            codec=codec,
            hdr=hdr,
            audio=audio,
            audio_languages=audio_languages,
            size_mb=size_mb,
            bitrate_kbps=bitrate_kbps,
        )
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
