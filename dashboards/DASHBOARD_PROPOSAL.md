# Marimo Dashboard Stack - Proposal & Reference Guide

> **Purpose**: Help decide if this stack fits your next project. Covers what we use, why, what works well, what hurts, and all the docs you need.

---

## TL;DR Decision Matrix

| If your app needs...              | This stack? | Why                                              |
|-----------------------------------|-------------|--------------------------------------------------|
| Internal data dashboards          | **YES**     | Fast to build, reactive, no frontend code needed |
| Live API data + interactive charts| **YES**     | httpx + plotly + marimo tabs = done in hours     |
| Public-facing web app             | **NO**      | Marimo is not designed for multi-user production |
| Complex forms / CRUD              | **NO**      | Limited input widgets, no form validation        |
| Mobile-friendly UI                | **NO**      | Desktop-first layout, no responsive design       |
| Team of 5+ editing same dashboard | **MAYBE**   | Pure Python files, git-mergeable, but no IDE support |
| Jupyter replacement               | **YES**     | Reactive > Jupyter's stale-state problem         |

---

## What We Built

3 interactive dashboards for Emby media server analytics (**2,025 lines total**):

| Dashboard                 | Lines | Tabs | Purpose                                      |
|---------------------------|-------|------|----------------------------------------------|
| `emby_unplayed.py`        | 623   | 6    | Unplayed content analysis across 63 users    |
| `emby_missing.py`         | 960   | 6    | Missing episodes & franchise gaps + JSON export |
| `emby_yearly_analytics.py`| 442   | 6    | Year-over-year watch patterns & trends       |

All connect live to Emby API, process data in pandas, render with plotly charts + marimo widgets.

---

## The Stack

### Core Libraries (4 packages - that's it)

| Library     | Version | Role                    | Docs                                                     |
|-------------|---------|-------------------------|----------------------------------------------------------|
| **marimo**  | 0.20.2  | Reactive notebook/app   | https://docs.marimo.io                                   |
| **plotly**  | 6.5.2   | Interactive charts       | https://plotly.com/python/                                |
| **pandas**  | 3.0.1   | Data manipulation        | https://pandas.pydata.org/docs/                          |
| **httpx**   | 0.28.1  | HTTP client (sync+async) | https://www.python-httpx.org                             |

### Auto-installed Dependencies (26 packages)

| Package            | Version  | Pulled by  | Purpose                        |
|--------------------|----------|------------|---------------------------------|
| anyio              | 4.12.1   | httpx      | Async runtime                   |
| certifi            | 2026.2.25| httpx      | SSL certificates                |
| click              | 8.3.1    | marimo     | CLI framework                   |
| docutils           | 0.22.4   | marimo     | RST processing                  |
| h11                | 0.16.0   | httpcore   | HTTP/1.1 parser                 |
| httpcore           | 1.0.9    | httpx      | HTTP transport                  |
| idna               | 3.11     | httpx      | Domain encoding                 |
| itsdangerous       | 2.2.0    | marimo     | Signing/security                |
| jedi               | 0.19.2   | marimo     | Code completion (edit mode)     |
| loro               | 1.10.3   | marimo     | CRDT (collaborative editing)    |
| markdown           | 3.10.2   | marimo     | Markdown rendering              |
| msgspec            | 0.20.0   | marimo     | Fast serialization              |
| narwhals           | 2.17.0   | marimo     | DataFrame compatibility layer   |
| numpy              | 2.4.2    | pandas     | Numeric computing               |
| packaging          | 26.0     | marimo     | Version parsing                 |
| parso              | 0.8.6    | jedi       | Python parser                   |
| psutil             | 7.2.2    | marimo     | System monitoring               |
| pyarrow            | 23.0.1   | pandas     | Columnar data + parquet         |
| pygments           | 2.19.2   | marimo     | Syntax highlighting             |
| pymdown-extensions | 10.21    | marimo     | Extended markdown               |
| python-dateutil    | 2.9.0    | pandas     | Date parsing                    |
| pyyaml             | 6.0.3    | marimo     | YAML config                     |
| six                | 1.17.0   | dateutil   | Py2/3 compat (legacy dep)       |
| starlette          | 0.52.1   | marimo     | ASGI framework (serves the app) |
| tomlkit            | 0.14.0   | marimo     | TOML parsing                    |
| uvicorn            | 0.41.0   | marimo     | ASGI server                     |
| websockets         | 16.0     | marimo     | Real-time cell updates          |

**Total install size**: ~250 MB (pyarrow + numpy dominate)

---

## Setup (Copy-Paste Ready)

```bash
# 1. Create isolated venv (keeps dashboard deps separate from project)
uv venv .dashboard-venv --python 3.14

# 2. Install the 4 core packages
.dashboard-venv/bin/uv pip install marimo plotly pandas httpx

# 3. Run as web app
.dashboard-venv/bin/marimo run dashboards/your_dashboard.py --port 2718

# 4. Edit interactively (notebook mode)
.dashboard-venv/bin/marimo edit dashboards/your_dashboard.py
```

---

## Architecture Pattern

Every dashboard follows the same structure:

```
┌─────────────────────────────────────────────┐
│  @app.cell: imports                         │
│  (marimo, pandas, plotly, httpx)            │
├─────────────────────────────────────────────┤
│  @app.cell: config & API setup              │
│  (API keys, base URLs, cache paths)         │
├─────────────────────────────────────────────┤
│  @app.cell: data fetching + caching         │
│  (httpx calls → JSON cache → DataFrame)    │
├─────────────────────────────────────────────┤
│  @app.cell: tab 1 (charts + tables)         │
│  @app.cell: tab 2 (charts + tables)         │
│  @app.cell: tab N ...                       │
├─────────────────────────────────────────────┤
│  @app.cell: assemble tabs                   │
│  mo.ui.tabs({"Tab 1": tab1, ...})           │
└─────────────────────────────────────────────┘
```

### Skeleton Template

```python
import marimo
app = marimo.App(width="full", app_title="My Dashboard")

@app.cell
def imports():
    import marimo as mo
    import pandas as pd
    import plotly.graph_objects as go
    import httpx
    from pathlib import Path
    from datetime import datetime, timedelta
    return mo, pd, go, httpx, Path, datetime, timedelta

@app.cell
def config(mo):
    API_URL = "https://api.example.com"
    API_KEY = "your-key"
    CACHE_DIR = Path.home() / ".cache" / "my-dashboards"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_TTL_HOURS = 2
    return API_URL, API_KEY, CACHE_DIR, CACHE_TTL_HOURS

@app.cell
def fetch_data(mo, httpx, API_URL, API_KEY, CACHE_DIR, CACHE_TTL_HOURS, pd, datetime):
    cache_file = CACHE_DIR / "data.json"
    # ... caching + API fetch logic ...
    with mo.status.spinner("Fetching data..."):
        resp = httpx.get(f"{API_URL}/items", headers={"X-Api-Key": API_KEY})
        df = pd.DataFrame(resp.json())
    return df,

@app.cell
def tab_overview(df, mo, go):
    # Stats row
    stats = mo.hstack([
        mo.stat(value=len(df), label="Total Items"),
        mo.stat(value=f"{df['size'].sum() / 1e9:.1f} GB", label="Total Size"),
    ], gap=1, justify="center")

    # Chart
    fig = go.Figure(go.Bar(x=df["category"], y=df["count"]))
    fig.update_layout(template="plotly_white", height=400)

    tab = mo.vstack([stats, fig])
    return (tab,)

@app.cell
def dashboard(tab_overview, mo):
    mo.ui.tabs({"Overview": tab_overview})

if __name__ == "__main__":
    app.run()
```

---

## UI Components We Use (with examples)

### Interactive Controls

```python
# Slider - filter by size
size_filter = mo.ui.slider(start=0, stop=100, value=5, step=1, label="Min size (GB)")

# Dropdown - select category
category = mo.ui.dropdown({"All": "all", "Movies": "movie", "Series": "series"}, value="All")

# Switch - toggle filter
hide_recent = mo.ui.switch(value=False, label="Hide items from 2026")

# Range slider - episode count range
ep_range = mo.ui.range_slider(start=1, stop=500, value=[1, 100], label="Missing episodes")

# Button - force refresh cache
refresh_btn = mo.ui.button(label="Force Refresh", kind="warn")

# Run button - trigger export (use for side effects!)
export_btn = mo.ui.run_button(label="Export to JSON")
```

### Display Components

```python
# Stat cards
mo.hstack([
    mo.stat(value="6,828", label="Movies", caption="79 TB total"),
    mo.stat(value="33.2%", label="Unplayed", direction="decrease", caption="vs last month"),
], gap=1)

# Callout boxes
mo.callout("Data loaded from cache (30m ago)", kind="info")
mo.callout("Export complete: /path/to/file.json", kind="success")

# Markdown with formatting
mo.md("### Section Title\n\nSome **bold** explanation")

# Clickable links in tables (HTML)
mo.Html(f'<a href="{url}" target="_blank" style="position:relative;z-index:10">{text}</a>')

# Tables with pagination
mo.ui.table(df.to_dict("records"), pagination=True, page_size=25)

# Loading spinner
with mo.status.spinner("Scanning 63 users..."):
    data = expensive_operation()
```

### Charts (Plotly)

```python
# Bar chart
fig = go.Figure(go.Bar(
    x=values, y=labels, orientation="h",
    marker_color=["#2ecc71" if v > threshold else "#e74c3c" for v in values],
    text=[f"{v:.1f}" for v in values], textposition="outside"
))
fig.update_layout(template="plotly_white", height=max(400, len(labels) * 28))

# Donut chart
fig = go.Figure(go.Pie(
    labels=categories, values=counts, hole=0.4,
    marker_colors=["#3498db", "#2ecc71", "#e74c3c"]
))
```

---

## Caching Pattern

```python
CACHE_DIR = Path.home() / ".cache" / "my-dashboards"
CACHE_MAX_AGE_HOURS = 2

def load_or_fetch(cache_file, fetch_fn):
    """Load from cache if fresh, otherwise fetch and save."""
    if cache_file.exists():
        age = datetime.now() - datetime.fromtimestamp(cache_file.stat().st_mtime)
        if age < timedelta(hours=CACHE_MAX_AGE_HOURS):
            return json.loads(cache_file.read_text()), age
    data = fetch_fn()
    cache_file.write_text(json.dumps(data, default=str))
    return data, timedelta(0)

# Force refresh button clears cache
if refresh_btn.value:
    for f in CACHE_DIR.glob("*.json"):
        f.unlink()
```

---

## Gotchas & Lessons Learned (Save Yourself Hours)

### Marimo-Specific

| Gotcha | Problem | Solution |
|--------|---------|----------|
| **Unique variable names** | Marimo enforces globally unique names across ALL cells | Use `_` prefix for cell-local vars: `_filtered = df[...]` |
| **Can't read value in same cell** | `slider = mo.ui.slider(); print(slider.value)` won't work | Create widget in one cell, read `.value` in another |
| **`mo.stop()` kills output** | Downstream cells never see return values | Use `if/else` instead of `mo.stop()` |
| **`run_button` vs `button`** | `button` is for UI state, NOT side effects | Use `mo.ui.run_button()` for exports, data refresh, etc. |
| **`run_button.value`** | Initial value is `None`, not `0` | Check with `if btn.value:` not `if btn.value > 0` |
| **Dropdown default** | `value=` must match a **key** (label), not the dict value | `mo.ui.dropdown({"All": "all"}, value="All")` not `value="all"` |
| **Thread safety** | Shared `httpx.Client` across threads = race conditions | Create per-thread clients in `ThreadPoolExecutor` |

### API & Data

| Gotcha | Problem | Solution |
|--------|---------|----------|
| **Self-signed SSL** | `httpx.get()` fails on internal APIs with custom certs | `httpx.Client(verify=False)` |
| **Large datasets** | 60s+ load time scanning 63 users | Cache aggressively (2-4h TTL) + loading spinners |
| **Paginated APIs** | Missing data if you don't paginate | Loop with `StartIndex` + `Limit` until `TotalRecordCount` reached |

---

## When to Use This vs Alternatives

| Tool              | Best For                                  | Docs                              |
|-------------------|-------------------------------------------|-----------------------------------|
| **Marimo**        | Internal data dashboards, Python-heavy    | https://docs.marimo.io            |
| **Streamlit**     | Quick prototypes, more widgets, auth      | https://docs.streamlit.io         |
| **Gradio**        | ML model demos, file upload/download      | https://gradio.app/docs           |
| **Dash (Plotly)** | Production dashboards, enterprise         | https://dash.plotly.com           |
| **Panel**         | Complex scientific dashboards             | https://panel.holoviz.org         |
| **Jupyter + Voila** | Existing notebook → app conversion      | https://voila.readthedocs.io      |

### Why We Chose Marimo

1. **Reactive by design** - no stale cell state (Jupyter's biggest problem)
2. **Pure Python files** - git-friendly, no JSON notebook format
3. **Zero frontend code** - no HTML/CSS/JS needed for 90% of cases
4. **Lightweight** - 4 pip packages, runs anywhere Python runs
5. **Edit + Run modes** - develop interactively, deploy as web app

### Where Marimo Falls Short

1. **No built-in auth** - anyone with the URL can access
2. **Single-user** - no concurrent session isolation
3. **Limited widgets** - no date picker, no file upload, no rich text input
4. **No database connectors** - you write raw SQL or use pandas
5. **Young ecosystem** - fewer examples, smaller community than Streamlit

---

## Documentation Links (Bookmark These)

### Primary
- **Marimo docs**: https://docs.marimo.io
- **Marimo API reference**: https://docs.marimo.io/api/
- **Marimo UI components**: https://docs.marimo.io/api/inputs/
- **Marimo layouts**: https://docs.marimo.io/api/layouts/
- **Plotly Python**: https://plotly.com/python/
- **Plotly graph_objects**: https://plotly.com/python/graph-objects/
- **Pandas**: https://pandas.pydata.org/docs/reference/
- **httpx**: https://www.python-httpx.org/quickstart/

### Useful References
- **Marimo examples gallery**: https://marimo.io/gallery
- **Marimo GitHub**: https://github.com/marimo-team/marimo
- **Plotly color scales**: https://plotly.com/python/builtin-colorscales/
- **Plotly templates**: https://plotly.com/python/templates/

---

## Cost & Performance

| Metric                    | Our Experience               |
|---------------------------|------------------------------|
| **Time to first dashboard** | ~3 hours (emby_unplayed)   |
| **Time for additional**     | ~2 hours each              |
| **Lines per dashboard**     | 400-960 (pure Python)      |
| **Load time (cold)**        | ~60s (scanning 63 users)   |
| **Load time (cached)**      | <2s                        |
| **Memory usage**            | ~150 MB per dashboard      |
| **Install size**            | ~250 MB (venv)             |
| **Dependencies**            | 4 direct, 26 transitive    |
| **Python version**          | 3.14 (works on 3.10+)     |

---

## Verdict

**Use this stack when**: You need internal data dashboards built fast by Python developers, with interactive charts and live API data. No frontend skills required.

**Don't use when**: You need auth, multi-user production apps, mobile support, or complex form inputs. Use Streamlit or Dash instead.
