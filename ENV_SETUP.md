# Environment Configuration

## Quick Setup

1. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` with your Emby server details:
   ```bash
   nano .env
   ```

## Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `DEDUPE_EMBY_HOST` | Your Emby server URL | `https://emby.example.com` |
| `DEDUPE_EMBY_API_KEY` | Your Emby API key | `abc123def456...` |
| `DEDUPE_EMBY_USERNAME` | Emby username (for missing episodes) | `your_username` |
| `DEDUPE_EMBY_PASSWORD` | Emby password (for missing episodes) | `your_password` |
| `DEDUPE_EMBY_LIBRARY` | Default libraries (comma-separated) | `Movies,TV Shows` |
| `DEDUPE_LOGGING` | Log level | `INFO` or `DEBUG` |
| `DEDUPE_LANG_PRIO` | Language preferences | `slo,cze,eng` |
| `DEDUPE_EXCLUDE_IDS` | Provider IDs to exclude | `tt1234567,123456` |
| `DEDUPE_HTML_REPORT` | Generate HTML reports | `true` or `false` |
| `DEDUPE_HTML_ONLY` | HTML-only output | `true` or `false` |
| `DEDUPE_DOIT` | Actually delete duplicates | `false` (safety) |

## Usage Examples

With environment file configured, you can use shorter commands:

### Deduplication
```bash
# Basic deduplication scan
python -m emby_dedupe

# Actually delete duplicates (be careful!)
python -m emby_dedupe --doit

# Specific library
python -m emby_dedupe --library "Movies"
```

### Missing Episodes
```bash
# Find missing episodes
python -m emby_dedupe --missing-episodes

# Multiple libraries
python -m emby_dedupe --missing-episodes --library "SERIALS" --library "Anime"

# JSON output
python -m emby_dedupe --missing-episodes --format json
```

## Security Notes

- The `.env` file contains sensitive credentials and is excluded from git
- Never commit the real `.env` file to version control
- Use `.env.example` as a template for other users