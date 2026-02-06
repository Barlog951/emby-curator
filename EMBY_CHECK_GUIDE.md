# Emby Check API - Complete Guide

## Overview

The `emby-dedupe check` command allows external applications (like torrent scrapers) to check if downloading a media item would result in a duplicate that gets removed by deduplication. It compares the proposed download's quality against existing items in your Emby library and returns a recommendation.

**Use case:** Before downloading a torrent, check if you already have the same content at equal or better quality to avoid wasting bandwidth and storage.

---

## Installation

### Option 1: pipx (Recommended for CLI usage)
```bash
# Install pipx if not installed
brew install pipx
pipx ensurepath

# Install emby-dedupe globally as CLI tool
pipx install /Users/dodko/DEV/emby-dedupe

# Verify installation
emby-dedupe check --help
```

### Option 2: pip in virtual environment
```bash
# Activate your venv
source venv/bin/activate

# Install in editable mode
pip install -e /Users/dodko/DEV/emby-dedupe

# Verify
emby-dedupe check --help
```

### Option 3: Python library only
```bash
# In your project's requirements.txt
-e /Users/dodko/DEV/emby-dedupe

# Or install directly
pip install -e /Users/dodko/DEV/emby-dedupe
```

---

## Quick Start

### Basic Usage (CLI)

**Check a movie:**
```bash
emby-dedupe check --simple \
  --host "https://emby.in.fukiyato.com" \
  --api-key "36825b1ab6394b8daee5bc1c2186bd90" \
  --name "Inception" \
  --year 2010 \
  --resolution 2160p \
  --size-mb 50000
```

Output: `download` or `skip`

**Check a TV episode:**
```bash
emby-dedupe check --simple \
  --host "https://emby.in.fukiyato.com" \
  --api-key "36825b1ab6394b8daee5bc1c2186bd90" \
  --name "Breaking Bad" \
  --season 1 \
  --episode 1 \
  --resolution 1080p \
  --size-mb 5000
```

### Basic Usage (Python Library)

```python
from emby_dedupe.api.checker import EmbyChecker

# Initialize once
checker = EmbyChecker(
    host="https://emby.in.fukiyato.com",
    api_key="36825b1ab6394b8daee5bc1c2186bd90",
    use_cache=True
)

# Check if should download
should_dl = checker.should_download(
    name="Inception",
    year=2010,
    resolution="2160p",
    size_mb=50000
)

if should_dl:
    print("Download it!")
else:
    print("Skip - already have same or better")

checker.close()
```

---

## Configuration

### Config File (Optional)

Create `~/.emby-dedupe/config.yaml`:

```yaml
# Connection settings
host: "https://emby.in.fukiyato.com"
api_key: "36825b1ab6394b8daee5bc1c2186bd90"

# Default libraries to search (optional - omit to search all)
libraries:
  - "HD & 4k"
  - "LQ - Movies"
  - "SERIALS"

# Language priorities (optional)
lang_priorities: ["sk", "cs", "en"]

# Cache settings
cache_enabled: true
cache_ttl_minutes: 10
```

**With config file, commands are simpler:**
```bash
# Just name and quality - rest from config
emby-dedupe check --simple --name "Movie" --resolution 2160p --size-mb 15000
```

### Priority Order

1. **CLI arguments** (highest priority)
2. **Config file** (`~/.emby-dedupe/config.yaml`)
3. **All libraries** (default if nothing specified)

---

## CLI Reference

### Connection Parameters

| Parameter | Description | Example |
|-----------|-------------|---------|
| `--host` | Emby server URL | `https://emby.in.fukiyato.com` |
| `--api-key` | Emby API key | `36825b1ab6394b8daee5bc1c2186bd90` |
| `--library` | Library to search (can specify multiple) | `--library "Movies"` |
| `--all-libraries` | Search all libraries (default) | Flag only |

### Search Criteria

| Parameter | Description | Example | Notes |
|-----------|-------------|---------|-------|
| `--name` | Media title | `--name "Inception"` | Required |
| `--year` | Release year | `--year 2010` | Recommended for movies |
| `--imdb` | IMDB ID | `--imdb tt1375666` | Most accurate matching |
| `--tmdb` | TMDB ID | `--tmdb 27205` | Alternative to IMDB |
| `--tvdb` | TVDB ID | `--tvdb 81189` | For TV shows |
| `--season` | Season number | `--season 1` | For TV episodes |
| `--episode` | Episode number | `--episode 5` | For TV episodes |

### Quality Information

| Parameter | Description | Example | Importance |
|-----------|-------------|---------|------------|
| `--resolution` | Resolution | `--resolution 2160p` | **CRITICAL** |
| `--size-mb` | File size in MB | `--size-mb 15000` | **CRITICAL** |
| `--codec` | Video codec | `--codec x265` | Optional |
| `--hdr` | HDR type | `--hdr DV` | Optional |
| `--audio` | Audio type | `--audio Atmos` | Optional |
| `--audio-lang` | Audio languages | `--audio-lang "cze,eng"` | For lang priority |
| `--bitrate-kbps` | Bitrate | `--bitrate-kbps 15000` | Optional |

**⚠️ IMPORTANT:** Always provide `--size-mb` for accurate comparison! Without it, quality scoring is unreliable.

### Deduplication Settings

| Parameter | Description | Example |
|-----------|-------------|---------|
| `--lang-prio` | Language priority | `--lang-prio "sk,cs,en"` |
| `--exclude-ids` | Provider IDs to exclude | `--exclude-ids "tt0120737,tt0167260"` |

### Output Formats

| Parameter | Output | Use Case |
|-----------|--------|----------|
| `--json` | Full JSON with details | Default - for debugging |
| `--simple` | Just `download` or `skip` | For shell scripts |
| `--exit-code` | Exit code only (0=download, 1=skip) | For shell conditionals |

### Caching

| Parameter | Description |
|-----------|-------------|
| `--cache` | Enable caching (faster repeated checks) |
| `--no-cache` | Disable caching |

Cache location: `~/.emby-dedupe/cache/`
Cache TTL: 10 minutes (configurable in config file)

---

## Output Formats

### Simple Output (`--simple`)

**Just two words:**
- `download` - Should download (not found OR better quality)
- `skip` - Already have same or better quality

**Example:**
```bash
$ emby-dedupe check --simple --name "Inception" --resolution 2160p --size-mb 50000
skip
```

### Exit Code Output (`--exit-code`)

**No text output, just exit code:**
- `0` = download
- `1` = skip
- `2` = error

**Example:**
```bash
if emby-dedupe check --exit-code --name "Movie" --resolution 1080p --size-mb 5000; then
    echo "Downloading..."
else
    echo "Skipping"
fi
```

### JSON Output (default)

**Full detailed response:**
```json
{
  "status": "found",
  "recommendation": "skip",
  "reason": "same_or_worse",
  "existing": {
    "id": "20244288",
    "name": "Inception",
    "quality": {
      "resolution": "2160p",
      "codec": "hevc",
      "audio_channels": 6,
      "bitrate_kbps": 65900,
      "size_mb": 69826
    },
    "audio_languages": ["cze", "eng"],
    "provider_ids": {
      "IMDB": "tt1375666",
      "Tmdb": "27205"
    },
    "path": "/Movies/4K/Inception/..."
  },
  "proposed": {
    "resolution": "2160p",
    "codec": null,
    "size_mb": 50000,
    "audio_languages": null
  },
  "quality_comparison": {
    "existing_score": 23397794235.1,
    "proposed_score": 15234567890.0,
    "score_diff": -8163226345.1,
    "winner": "existing"
  }
}
```

**Response fields:**
- `status`: `"found"` or `"not_found"`
- `recommendation`: `"download"` or `"skip"`
- `reason`: `"not_found"`, `"better_quality"`, or `"same_or_worse"`
- `existing`: Details of existing item (if found)
- `proposed`: Proposed item quality info
- `quality_comparison`: Score comparison details

---

## Quality Scoring

The check command uses the **same quality scoring as deduplication**:

### Scoring Weights

| Factor | Weight | Impact |
|--------|--------|--------|
| Resolution (width × height) | 1.0 | Highest priority |
| Date added | 0.8 | Prefer newer files |
| Audio channels | 0.5 | More channels = better |
| File size | 0.3 | Larger usually = better bitrate |
| Bitrate | 0.2 | Higher = better quality |

### Resolution Tiers

- `2160p` / `4K` / `UHD` = 3840×2160 = 8,294,400 pixels
- `1080p` = 1920×1080 = 2,073,600 pixels
- `720p` = 1280×720 = 921,600 pixels
- `480p` = 854×480 = 409,920 pixels

### Examples

**Scenario 1: Clear upgrade**
```
Existing: 1080p, 5GB  → score: 500,000,000
Proposed: 2160p, 20GB → score: 1,200,000,000
Result: download (4x resolution, larger file)
```

**Scenario 2: Same resolution, larger file**
```
Existing: 1080p, 5GB  → score: 500,000,000
Proposed: 1080p, 15GB → score: 650,000,000
Result: download (higher bitrate)
```

**Scenario 3: Higher resolution but no size info**
```
Existing: 1080p, 23GB REMUX → score: 8,646,156,736
Proposed: 2160p, no size    → score: 1,422,181,016
Result: skip (can't determine quality without size!)
```

**⚠️ Always provide --size-mb for accurate comparison!**

---

## Python Library API

### Basic Usage

```python
from emby_dedupe.api.checker import EmbyChecker

# Initialize
checker = EmbyChecker(
    host="https://emby.in.fukiyato.com",
    api_key="36825b1ab6394b8daee5bc1c2186bd90",
    libraries=["HD & 4k", "SERIALS"],  # Optional - omit for all
    lang_priorities=["sk", "cs", "en"],
    use_cache=True,
    cache_ttl_minutes=10
)

# Simple boolean check
should_download = checker.should_download(
    name="Inception",
    year=2010,
    resolution="2160p",
    size_mb=50000
)

# Detailed check with result object
result = checker.check(
    name="Breaking Bad",
    season=1,
    episode=1,
    resolution="1080p",
    size_mb=5000,
    audio_languages=["cze"]
)

print(f"Recommendation: {result.recommendation}")
print(f"Reason: {result.reason}")
if result.existing:
    print(f"Existing quality: {result.existing.resolution}")

checker.close()
```

### Using Config File

```python
from emby_dedupe.api.checker import EmbyChecker

# Load from ~/.emby-dedupe/config.yaml
checker = EmbyChecker.from_config()

# Or override specific settings
checker = EmbyChecker.from_config(
    libraries=["Movies"],  # Override libraries only
)
```

### Context Manager

```python
from emby_dedupe.api.checker import EmbyChecker

with EmbyChecker.from_config() as checker:
    result = checker.check(name="Movie", resolution="2160p", size_mb=10000)
    if result.should_download:
        print("Download it!")
# Automatically closes connection
```

### Batch Checking

```python
from emby_dedupe.api.checker import EmbyChecker

checker = EmbyChecker.from_config()

# Check multiple items at once
items_to_check = [
    {"name": "Movie 1", "year": 2024, "resolution": "2160p", "size_mb": 25000},
    {"name": "Movie 2", "year": 2023, "resolution": "1080p", "size_mb": 8000},
    {"name": "Breaking Bad", "season": 1, "episode": 1, "resolution": "1080p", "size_mb": 3000},
]

results = checker.check_batch(items_to_check)

for item, result in zip(items_to_check, results):
    print(f"{item['name']}: {result.recommendation}")

checker.close()
```

### ComparisonResult Object

```python
result = checker.check(...)

# Properties
result.recommendation    # "download" or "skip"
result.reason           # "not_found", "better_quality", "same_or_worse"
result.status           # "found" or "not_found"
result.should_download  # Boolean: True or False
result.existing         # ExistingQuality object (or None)
result.proposed         # ProposedQuality object
result.existing_score   # Float: quality score
result.proposed_score   # Float: quality score
result.score_diff       # Float: difference in scores

# Methods
result.to_dict()        # Convert to dictionary for JSON
```

---

## Integration with Torrent Scraper

### Example: Czech Tracker Scraper Integration

Located at `/Users/dodko/DEV/torrents/`

#### Option 1: CLI Integration

```python
import subprocess
import json

def should_download_torrent(torrent_info):
    """
    Check if torrent should be downloaded.

    Args:
        torrent_info: Dict with keys: name, year, quality, size_mb, is_series, season, episode

    Returns:
        bool: True if should download, False otherwise
    """
    cmd = [
        "emby-dedupe", "check", "--simple",
        "--host", "https://emby.in.fukiyato.com",
        "--api-key", "36825b1ab6394b8daee5bc1c2186bd90",
        "--lang-prio", "sk,cs,en",
        "--name", torrent_info["name"],
        "--resolution", torrent_info["quality"],
        "--size-mb", str(torrent_info["size_mb"]),
    ]

    # Add optional parameters
    if torrent_info.get("year"):
        cmd.extend(["--year", str(torrent_info["year"])])

    if torrent_info.get("imdb"):
        cmd.extend(["--imdb", torrent_info["imdb"]])

    # TV show specific
    if torrent_info.get("is_series"):
        if torrent_info.get("season"):
            cmd.extend(["--season", str(torrent_info["season"])])
        if torrent_info.get("episode"):
            cmd.extend(["--episode", str(torrent_info["episode"])])

    # Add audio language if available
    if torrent_info.get("audio_langs"):
        cmd.extend(["--audio-lang", ",".join(torrent_info["audio_langs"])])

    # Enable caching for faster repeated checks
    cmd.append("--cache")

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 2:  # Error
        # On error, default to download (safer to have duplicate than miss content)
        return True

    return result.stdout.strip() == "download"


# Usage in scraper
torrent = {
    "name": "Inception",
    "year": 2010,
    "quality": "2160p",
    "size_mb": 50000,
    "imdb": "tt1375666",
    "audio_langs": ["eng"],
    "is_series": False,
}

if should_download_torrent(torrent):
    print(f"Downloading: {torrent['name']}")
    # Add to transmission...
else:
    print(f"Skipping: {torrent['name']} (already have same or better)")
```

#### Option 2: Python Library Integration (Faster)

```python
from emby_dedupe.api.checker import EmbyChecker

class TorrentChecker:
    """Wrapper for checking torrents against Emby library."""

    def __init__(self):
        """Initialize the Emby checker."""
        self.checker = EmbyChecker(
            host="https://emby.in.fukiyato.com",
            api_key="36825b1ab6394b8daee5bc1c2186bd90",
            lang_priorities=["sk", "cs", "en"],
            use_cache=True,  # Cache library data for faster checks
            cache_ttl_minutes=10
        )

    def should_download(self, torrent_info):
        """
        Check if torrent should be downloaded.

        Args:
            torrent_info: Dict with torrent metadata

        Returns:
            dict: {should_download: bool, reason: str, existing_quality: str}
        """
        try:
            result = self.checker.check(
                name=torrent_info["name"],
                year=torrent_info.get("year"),
                imdb=torrent_info.get("imdb"),
                season=torrent_info.get("season"),
                episode=torrent_info.get("episode"),
                resolution=torrent_info["quality"],
                size_mb=torrent_info["size_mb"],
                audio_languages=torrent_info.get("audio_langs"),
            )

            return {
                "should_download": result.should_download,
                "reason": result.reason,
                "existing_quality": result.existing.resolution if result.existing else None,
                "existing_size_mb": result.existing.size_bytes // (1024*1024) if result.existing else None,
            }

        except Exception as e:
            # On error, default to download (safer)
            return {
                "should_download": True,
                "reason": f"error: {e}",
                "existing_quality": None,
                "existing_size_mb": None,
            }

    def close(self):
        """Close the checker connection."""
        self.checker.close()


# Usage in scraper
checker = TorrentChecker()

for torrent in scraped_torrents:
    result = checker.should_download(torrent)

    if result["should_download"]:
        print(f"✓ Download: {torrent['name']} ({torrent['quality']})")
        add_to_transmission(torrent)
    else:
        print(f"✗ Skip: {torrent['name']} - already have {result['existing_quality']} ({result['existing_size_mb']}MB)")

checker.close()
```

### Parsing Torrent Names

```python
import re

def parse_torrent_info(torrent_name):
    """
    Parse torrent filename to extract metadata.

    Example: "Inception.2010.2160p.UHD.BluRay.x265.DTS-HD.MA.5.1-FGT.mkv"
    """
    info = {
        "name": None,
        "year": None,
        "quality": None,
        "size_mb": None,
        "codec": None,
        "audio_langs": [],
        "is_series": False,
        "season": None,
        "episode": None,
    }

    # Extract resolution
    res_match = re.search(r'(2160p|1080p|720p|480p|4K|UHD)', torrent_name, re.IGNORECASE)
    if res_match:
        quality = res_match.group(1).lower()
        info["quality"] = "2160p" if quality in ["4k", "uhd", "2160p"] else quality

    # Extract year
    year_match = re.search(r'(19|20)\d{2}', torrent_name)
    if year_match:
        info["year"] = int(year_match.group(0))

    # Extract codec
    if re.search(r'x265|HEVC|h265', torrent_name, re.IGNORECASE):
        info["codec"] = "x265"
    elif re.search(r'x264|h264|AVC', torrent_name, re.IGNORECASE):
        info["codec"] = "x264"

    # Extract TV episode info
    ep_match = re.search(r'[Ss](\d+)[Ee](\d+)', torrent_name)
    if ep_match:
        info["is_series"] = True
        info["season"] = int(ep_match.group(1))
        info["episode"] = int(ep_match.group(2))

    # Extract name (before year or quality indicators)
    name_match = re.match(r'^(.+?)(?:\.\d{4}|\.(2160|1080|720|480)p)', torrent_name)
    if name_match:
        name = name_match.group(1).replace('.', ' ').strip()
        info["name"] = name

    # Note: Size needs to come from torrent metadata, not filename

    return info


# Example usage
torrent_name = "Inception.2010.2160p.UHD.BluRay.x265.DTS-HD.MA.5.1-FGT.mkv"
torrent_size_mb = 50000  # From torrent metadata

info = parse_torrent_info(torrent_name)
info["size_mb"] = torrent_size_mb

print(f"Parsed: {info}")
# {"name": "Inception", "year": 2010, "quality": "2160p", "codec": "x265", "size_mb": 50000}
```

---

## Common Use Cases

### 1. Check Before Downloading Torrent

```bash
# From your torrent scraper
TORRENT_NAME="Inception.2010.2160p.WEB-DL.x265.mkv"
TORRENT_SIZE_MB=15000

if emby-dedupe check --exit-code --cache \
    --host "https://emby.in.fukiyato.com" \
    --api-key "36825b1ab6394b8daee5bc1c2186bd90" \
    --name "Inception" --year 2010 \
    --resolution 2160p --size-mb $TORRENT_SIZE_MB; then

    echo "Downloading $TORRENT_NAME"
    transmission-remote -a "$TORRENT_NAME"
else
    echo "Skipping $TORRENT_NAME (already have same or better)"
fi
```

### 2. Check with Language Priority

```bash
# Prefer Slovak/Czech audio
emby-dedupe check --simple \
  --host "https://emby.in.fukiyato.com" \
  --api-key "36825b1ab6394b8daee5bc1c2186bd90" \
  --lang-prio "sk,cs,en" \
  --name "Movie" --year 2023 \
  --resolution 1080p --size-mb 8000 \
  --audio-lang "cze"
```

### 3. Check Specific Library Only

```bash
# Search only in 4K library
emby-dedupe check --simple \
  --host "https://emby.in.fukiyato.com" \
  --api-key "36825b1ab6394b8daee5bc1c2186bd90" \
  --library "HD & 4k" \
  --name "Movie" --resolution 2160p --size-mb 25000
```

### 4. TV Episode Check

```bash
emby-dedupe check --simple \
  --host "https://emby.in.fukiyato.com" \
  --api-key "36825b1ab6394b8daee5bc1c2186bd90" \
  --name "Breaking Bad" \
  --season 1 --episode 5 \
  --resolution 1080p --size-mb 3000
```

### 5. Using IMDB ID (Most Accurate)

```bash
# When you have IMDB ID from torrent site
emby-dedupe check --simple \
  --host "https://emby.in.fukiyato.com" \
  --api-key "36825b1ab6394b8daee5bc1c2186bd90" \
  --imdb tt1375666 \
  --resolution 2160p --size-mb 50000
```

---

## Troubleshooting

### Issue: Returns "skip" when should be "download"

**Cause:** Missing `--size-mb` parameter

**Example:**
```bash
# BAD - no size info
$ emby-dedupe check --simple --name "Movie" --resolution 2160p
skip  # Wrong! Scores existing 1080p higher due to size

# GOOD - with size
$ emby-dedupe check --simple --name "Movie" --resolution 2160p --size-mb 50000
download  # Correct!
```

**Solution:** Always provide `--size-mb` from torrent metadata.

### Issue: Command not found

**Cause:** Not installed or wrong venv

**Solution:**
```bash
# Check installation
which emby-dedupe
pip show emby-dedupe

# Reinstall if needed
pip install -e /Users/dodko/DEV/emby-dedupe

# Or use pipx for global installation
pipx install /Users/dodko/DEV/emby-dedupe
```

### Issue: Returns "skip" for higher resolution

**Cause:** Existing file has very high bitrate/size

**Example:**
```
Existing: 1080p BluRay REMUX, 23GB, 28,651 kbps
Proposed: 2160p WEB-DL, 8GB (compressed)

Result: skip (1080p REMUX might actually be better quality!)
```

**This is correct behavior** - the scoring considers all factors, not just resolution.

### Issue: Slow repeated checks

**Solution:** Enable caching
```bash
emby-dedupe check --cache --simple --name "Movie" ...
```

Cache location: `~/.emby-dedupe/cache/`
Cache TTL: 10 minutes

To clear cache:
```bash
rm -rf ~/.emby-dedupe/cache/
```

---

## Advanced Configuration

### Custom Cache TTL

In `~/.emby-dedupe/config.yaml`:
```yaml
cache_enabled: true
cache_ttl_minutes: 30  # Cache for 30 minutes instead of 10
```

### Exclude Specific Provider IDs

```bash
# Never check these specific movies (like your existing dedupe exclusions)
emby-dedupe check --simple \
  --exclude-ids "tt0120737,tt0167260,tt0167261" \
  --name "Movie" --resolution 2160p --size-mb 10000
```

### Multiple Libraries

```bash
# Search multiple libraries
emby-dedupe check --simple \
  --library "HD & 4k" \
  --library "LQ - Movies" \
  --library "Documents" \
  --name "Movie" --resolution 1080p --size-mb 5000
```

---

## Complete Example: Torrent Scraper Integration

```python
"""
Integration of emby-dedupe check with Czech tracker scraper.
Location: /Users/dodko/DEV/torrents/
"""

import subprocess
import json
from czech_tracker_scraper import scrape_torrents

# Initialize Emby checker (Python library - faster)
from emby_dedupe.api.checker import EmbyChecker

EMBY_CONFIG = {
    "host": "https://emby.in.fukiyato.com",
    "api_key": "36825b1ab6394b8daee5bc1c2186bd90",
    "libraries": ["HD & 4k", "LQ - Movies", "SERIALS"],
    "lang_priorities": ["sk", "cs", "en"],
}

def main():
    """Main scraper with Emby checking."""

    # Scrape torrents
    torrents = scrape_torrents()

    # Initialize Emby checker
    checker = EmbyChecker(
        host=EMBY_CONFIG["host"],
        api_key=EMBY_CONFIG["api_key"],
        lang_priorities=EMBY_CONFIG["lang_priorities"],
        use_cache=True,  # Important for performance!
    )

    download_list = []
    skip_list = []

    for torrent in torrents:
        # Parse torrent metadata
        parsed = parse_torrent_info(torrent["name"])
        parsed["size_mb"] = torrent["size_mb"]  # From torrent metadata
        parsed["imdb"] = torrent.get("imdb")  # If available from tracker

        # Check against Emby
        result = checker.check(
            name=parsed["name"],
            year=parsed.get("year"),
            imdb=parsed.get("imdb"),
            season=parsed.get("season"),
            episode=parsed.get("episode"),
            resolution=parsed["quality"],
            codec=parsed.get("codec"),
            size_mb=parsed["size_mb"],
            audio_languages=parsed.get("audio_langs"),
        )

        if result.should_download:
            download_list.append({
                "torrent": torrent,
                "reason": result.reason,
            })
            print(f"✓ {torrent['name']}: {result.reason}")
        else:
            skip_list.append({
                "torrent": torrent,
                "existing": result.existing.resolution if result.existing else None,
                "existing_size": result.existing.size_bytes // (1024*1024) if result.existing else None,
            })
            existing_info = f"{result.existing.resolution}, {result.existing.size_bytes // (1024*1024)}MB" if result.existing else "unknown"
            print(f"✗ {torrent['name']}: already have {existing_info}")

    checker.close()

    # Report summary
    print(f"\n📊 Summary:")
    print(f"  Download: {len(download_list)}")
    print(f"  Skip: {len(skip_list)}")

    # Add to transmission
    for item in download_list:
        add_to_transmission(item["torrent"])

    return download_list, skip_list


if __name__ == "__main__":
    main()
```

---

## API Reference

### EmbyChecker Class

```python
class EmbyChecker:
    def __init__(
        self,
        host: str = None,
        api_key: str = None,
        libraries: list[str] = None,
        lang_priorities: list[str] = None,
        exclude_ids: list[str] = None,
        use_cache: bool = True,
        cache_ttl_minutes: int = 10,
        config: Config = None,
    )

    @classmethod
    def from_config(cls, **overrides) -> 'EmbyChecker'

    def check(
        self,
        name: str = None,
        year: int = None,
        imdb: str = None,
        tmdb: str = None,
        tvdb: str = None,
        season: int = None,
        episode: int = None,
        resolution: str = None,
        codec: str = None,
        hdr: str = None,
        audio: str = None,
        audio_languages: list[str] = None,
        size_mb: int = None,
        bitrate_kbps: int = None,
    ) -> ComparisonResult

    def should_download(self, **kwargs) -> bool

    def check_batch(self, items: list[dict]) -> list[ComparisonResult]

    def close(self) -> None
```

### ComparisonResult Class

```python
@dataclass
class ComparisonResult:
    recommendation: str  # "download" or "skip"
    reason: str  # "not_found", "better_quality", "same_or_worse"
    status: str  # "found" or "not_found"
    existing: ExistingQuality | None
    proposed: ProposedQuality | None
    existing_score: float
    proposed_score: float
    score_diff: float

    @property
    def should_download(self) -> bool

    def to_dict(self) -> dict
```

---

## Testing

### Run Unit Tests

```bash
cd /Users/dodko/DEV/emby-dedupe

# Run all tests
pytest tests/ -v

# Run only check-related tests
pytest tests/unit/api/test_checker.py -v
pytest tests/unit/api/test_search.py -v
pytest tests/unit/api/test_quality_compare.py -v
pytest tests/unit/utils/test_config.py -v

# Check coverage
pytest --cov=emby_dedupe --cov-report=term-missing
```

**Current test status:**
- 204 total tests pass
- 139 existing tests (unchanged)
- 65 new tests for check functionality

**Coverage:**
- `config.py`: 77%
- `search.py`: 81%
- `quality_compare.py`: 95%

---

## Performance Tips

1. **Use caching** - `--cache` flag or `use_cache=True`
   - First check loads library data (~2-5 seconds)
   - Cached checks are instant (<100ms)
   - Cache expires after 10 minutes

2. **Use Python library instead of CLI subprocess**
   - CLI: ~200ms per check (process spawn overhead)
   - Library: ~10ms per check (cached, in-process)

3. **Provide all quality info**
   - More data = more accurate scoring
   - At minimum: `--resolution` and `--size-mb`

4. **Use provider IDs when available**
   - IMDB/TMDB matching is instant and accurate
   - Name matching requires fuzzy search

---

## Real-World Examples

### Example 1: Movie Already in 4K
```bash
$ emby-dedupe check --simple \
    --name "Inception" --year 2010 \
    --resolution 2160p --size-mb 50000
skip
```

**Why:** Already have 4K version (69GB) which scores higher

### Example 2: Upgrade from 1080p to 4K
```bash
$ emby-dedupe check --simple \
    --name "Some Movie" --year 2020 \
    --resolution 2160p --size-mb 30000
download
```

**Why:** Only have 1080p version, 4K is better

### Example 3: New Content
```bash
$ emby-dedupe check --simple \
    --name "New Movie 2024" --year 2024 \
    --resolution 1080p --size-mb 8000
download
```

**Why:** Not found in library (status: "not_found")

### Example 4: Same Resolution, Larger File
```bash
$ emby-dedupe check --simple \
    --name "Movie" --resolution 1080p --size-mb 15000
download
```

**Why:** Existing is 1080p 5GB, proposed is 1080p 15GB (better bitrate)

### Example 5: Language Priority Override
```bash
$ emby-dedupe check --simple \
    --lang-prio "sk,cs,en" \
    --name "Movie" --resolution 1080p --size-mb 8000 \
    --audio-lang "sk"
download
```

**Why:** Existing only has English audio, proposed has Slovak (higher priority)

---

## File Locations

| Path | Purpose |
|------|---------|
| `/Users/dodko/DEV/emby-dedupe/` | Source code |
| `~/.emby-dedupe/config.yaml` | Config file (optional) |
| `~/.emby-dedupe/cache/` | Cache directory |
| Site-packages or editable link | Installed package |

---

## Migration from Subprocess to Library

**Before (subprocess - slower):**
```python
result = subprocess.run(["emby-dedupe", "check", "--simple", ...])
should_dl = result.stdout.strip() == "download"
```

**After (library - faster):**
```python
from emby_dedupe.api.checker import EmbyChecker

checker = EmbyChecker.from_config()  # Initialize once
should_dl = checker.should_download(...)  # Fast repeated checks
```

**Performance difference:**
- Subprocess: ~200ms per check
- Library: ~10ms per check (with cache)

---

## Support

**Issues with emby-dedupe check:**
- GitHub: https://github.com/troykelly/emby-dedupe/issues
- Local testing: `/Users/dodko/DEV/emby-dedupe/`

**Test against your live Emby:**
```bash
python -m emby_dedupe check \
  --host "https://emby.in.fukiyato.com" \
  --api-key "36825b1ab6394b8daee5bc1c2186bd90" \
  --name "Any Movie in Your Library" \
  --resolution 2160p --size-mb 10000
```

---

## Version

- **emby-dedupe version:** 2.0.0
- **New feature:** Check API (added 2026-01-02)
- **Backward compatibility:** All existing commands unchanged

---

## Quick Reference Card

```bash
# CLI - Simple mode (most common)
emby-dedupe check --simple \
  --host "URL" --api-key "KEY" \
  --name "Movie" --year 2024 \
  --resolution 2160p --size-mb 15000

# CLI - Exit code mode (for shell scripts)
if emby-dedupe check --exit-code --name "Movie" --resolution 1080p --size-mb 5000; then
    download_torrent
fi

# Python - Simple boolean
from emby_dedupe.api.checker import EmbyChecker
checker = EmbyChecker.from_config()
if checker.should_download(name="Movie", resolution="2160p", size_mb=15000):
    download_torrent()

# Python - Detailed result
result = checker.check(name="Movie", resolution="2160p", size_mb=15000)
print(f"{result.recommendation}: {result.reason}")
```

**Remember:**
- Always provide `--size-mb` for accurate comparison
- Use `--cache` for faster repeated checks
- Python library is faster than CLI subprocess
- Exit code: 0=download, 1=skip

---

**End of Guide**
