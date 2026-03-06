import marimo

__generated_with = "0.20.2"
app = marimo.App(width="full", app_title="Emby Missing Content Dashboard")


@app.cell
def imports():
    import marimo as mo
    import pandas as pd
    import plotly.graph_objects as go
    import httpx
    import time
    import json as jsonlib
    import os
    import threading
    from datetime import datetime, timedelta
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path
    return Path, ThreadPoolExecutor, datetime, go, httpx, jsonlib, mo, os, pd, threading, time, timedelta


@app.cell
def cache_helpers(Path, datetime, jsonlib, os, timedelta):
    CACHE_DIR = Path.home() / ".cache" / "emby-dashboards"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_MAX_AGE_HOURS = 2

    def cache_load(name):
        """Load from cache if fresh enough. Returns (data, is_cached)."""
        _path = CACHE_DIR / f"{name}.json"
        try:
            _age = datetime.now().timestamp() - _path.stat().st_mtime
            if _age < CACHE_MAX_AGE_HOURS * 3600:
                _mins = int(_age / 60)
                return jsonlib.loads(_path.read_text()), f"cached ({_mins}m ago)"
        except (FileNotFoundError, OSError):
            pass
        return None, None

    def cache_save(name, data):
        """Save data to cache."""
        _path = CACHE_DIR / f"{name}.json"
        _path.write_text(jsonlib.dumps(data, default=str))

    def cache_clear():
        """Clear all cache files."""
        for _f in CACHE_DIR.glob("*.json"):
            _f.unlink()

    return CACHE_DIR, cache_clear, cache_load, cache_save


@app.cell
def cache_control(mo):
    refresh_btn = mo.ui.button(label="Force Refresh (clear cache)", kind="warn")
    return (refresh_btn,)


@app.cell
def cache_on_refresh(cache_clear, refresh_btn):
    if refresh_btn.value:
        cache_clear()
    return ()


@app.cell
def config(mo, refresh_btn):
    mo.hstack([mo.md("# Emby Missing Content Dashboard"), refresh_btn], justify="space-between")
    EMBY_HOST = "https://emby.in.fukiyato.com"
    EMBY_API_KEY = "36825b1ab6394b8daee5bc1c2186bd90"
    EMBY_SERVER_ID = "ea8f5299fd0649a6867beb6368c873a1"
    TMDB_TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiI2YjNiZGJkYzdjNTA5OTgyNTU3ZWQxYzVhM2MyZGJkYiIsIm5iZiI6MTc0NTUzNTA4MC4xOSwic3ViIjoiNjgwYWMwNjgyNzFlY2IzYWUwOGEzNjRiIiwic2NvcGVzIjpbImFwaV9yZWFkIl0sInZlcnNpb24iOjF9.YB4PZpQ5U2ftjEul5XI6GY7MAmd8F95Nt5B4HJJRwD8"
    return EMBY_API_KEY, EMBY_HOST, EMBY_SERVER_ID, TMDB_TOKEN


@app.cell
def api_clients(EMBY_API_KEY, EMBY_HOST, TMDB_TOKEN, httpx, mo):
    _emby = httpx.Client(timeout=120, verify=False)
    _tmdb = httpx.Client(timeout=30, base_url="https://api.themoviedb.org/3",
                         headers={"Authorization": f"Bearer {TMDB_TOKEN}"})

    def emby_get(path, **params):
        params["api_key"] = EMBY_API_KEY
        return _emby.get(f"{EMBY_HOST}/emby/{path}", params=params).json()

    def emby_fetch_all(item_type, fields="", extra=None):
        _all, _off = [], 0
        while True:
            _p = {"Recursive": "true", "IncludeItemTypes": item_type,
                  "StartIndex": str(_off), "Limit": "500"}
            if fields:
                _p["Fields"] = fields
            if extra:
                _p.update(extra)
            _d = emby_get("Items", **_p)
            _items = _d.get("Items", [])
            if not _items:
                break
            _all.extend(_items)
            if _off + len(_items) >= _d.get("TotalRecordCount", 0):
                break
            _off += 500
        return _all

    def tmdb_get(path, **params):
        params.setdefault("language", "en-US")
        return _tmdb.get(path, params=params).json()

    with mo.status.spinner("Connecting to Emby & TMDB..."):
        _test = emby_get("System/Info")
    mo.callout(f"Connected — Emby: {_test.get('ServerName', 'OK')}", kind="success")
    return emby_fetch_all, emby_get, tmdb_get


@app.cell
def load_emby_data(cache_load, cache_save, emby_fetch_all, mo, pd, refresh_btn):
    _cached, _status = cache_load("emby_library")
    if _cached and not refresh_btn.value:
        raw_movies = _cached["movies"]
        raw_series = _cached["series"]
        raw_episodes = _cached["episodes"]
        mo.callout(f"Library data loaded from cache ({_status}) — {len(raw_movies)} movies, {len(raw_series)} series, {len(raw_episodes)} episodes", kind="info")
    else:
        with mo.status.spinner("Loading Emby library metadata (fresh)..."):
            raw_movies = emby_fetch_all("Movie", fields="ProviderIds,ProductionYear,Genres")
            raw_series = emby_fetch_all("Series", fields="ProviderIds,ProductionYear,Genres,OriginalTitle,SortName")
            raw_episodes = emby_fetch_all("Episode", fields="SeriesId,SeriesName,ParentIndexNumber,IndexNumber,DateCreated")
        cache_save("emby_library", {"movies": raw_movies, "series": raw_series, "episodes": raw_episodes})
        mo.callout(f"Loaded fresh: {len(raw_movies)} movies, {len(raw_series)} series, {len(raw_episodes)} episodes", kind="success")

    # Build TMDB ID lookup sets
    emby_tmdb_movie_ids = set()
    movie_by_tmdb = {}
    for _m in raw_movies:
        _pids = _m.get("ProviderIds", {})
        _tid = _pids.get("Tmdb") or _pids.get("tmdb")
        if _tid:
            emby_tmdb_movie_ids.add(str(_tid))
            movie_by_tmdb[str(_tid)] = _m.get("Name", "?")

    emby_tmdb_series_ids = set()
    for _s in raw_series:
        _pids = _s.get("ProviderIds", {})
        _tid = _pids.get("Tmdb") or _pids.get("tmdb")
        if _tid:
            emby_tmdb_series_ids.add(str(_tid))

    return (emby_tmdb_movie_ids, emby_tmdb_series_ids,
            movie_by_tmdb, raw_episodes, raw_movies, raw_series)


@app.cell
def analyze_missing_episodes(EMBY_API_KEY, EMBY_HOST, ThreadPoolExecutor, cache_load, cache_save, emby_get, httpx, mo, pd, raw_episodes, raw_series, refresh_btn, threading):
    """Uses Emby's native /Shows/Missing endpoint — the proper way."""
    # Count existing episodes per series + track last added date
    ep_count_by_series = {}
    _last_added = {}
    for _ep in raw_episodes:
        _sid = _ep.get("SeriesId", "")
        ep_count_by_series[_sid] = ep_count_by_series.get(_sid, 0) + 1
        _dc = (_ep.get("DateCreated") or "")[:10]
        if _dc and (_sid not in _last_added or _dc > _last_added[_sid]):
            _last_added[_sid] = _dc

    _cached, _status = cache_load("missing_episodes")
    if _cached and not refresh_btn.value:
        _results = [(r[0], r[1], r[2]) for r in _cached]
        mo.callout(f"Missing episodes loaded from cache ({_status})", kind="info")
    else:
        with mo.status.spinner(f"Fetching missing episodes for {len(raw_series)} series via /Shows/Missing (15 threads)..."):
            _host = EMBY_HOST
            _key = EMBY_API_KEY
            _tls = threading.local()

            def _fetch_missing(series):
                _sid = series.get("Id", "")
                try:
                    if not hasattr(_tls, "client"):
                        _tls.client = httpx.Client(timeout=30, verify=False)
                    _r = _tls.client.get(f"{_host}/emby/Shows/Missing", params={
                        "api_key": _key,
                        "ParentId": _sid,
                        "IncludeSpecials": "false",
                        "IncludeUnaired": "false",
                        "Fields": "ParentIndexNumber,IndexNumber",
                    })
                    _data = _r.json()
                    if isinstance(_data, dict):
                        return (_sid, _data.get("Items", []), _data.get("TotalRecordCount", 0))
                    elif isinstance(_data, list):
                        return (_sid, _data, len(_data))
                    return (_sid, [], 0)
                except Exception:
                    return (_sid, [], 0)

            with ThreadPoolExecutor(max_workers=15) as _pool:
                _results = list(_pool.map(_fetch_missing, raw_series))

        cache_save("missing_episodes", _results)
        mo.callout(f"Fetched fresh missing episodes data", kind="success")

    _rows = {}  # Keyed by TMDB ID to deduplicate
    _series_map = {_s["Id"]: _s for _s in raw_series}

    for _sid, _missing_items, _n_missing in _results:
        if _n_missing <= 0:
            continue

        _s = _series_map.get(_sid, {})
        _pids = _s.get("ProviderIds", {})
        _tmdb_id = _pids.get("Tmdb") or _pids.get("tmdb") or _sid  # fallback to emby ID

        # Skip if we already have this show (deduplicate by TMDB ID)
        if _tmdb_id in _rows:
            continue

        _n_have = ep_count_by_series.get(_sid, 0)
        _n_total = _n_have + _n_missing
        _pct = round(_n_have / _n_total * 100, 1) if _n_total > 0 else 0

        # Group missing by season
        _season_miss = {}
        for _ep in _missing_items:
            _sn = _ep.get("ParentIndexNumber")
            if _sn is not None:
                _season_miss[_sn] = _season_miss.get(_sn, 0) + 1

        _yr = _s.get("ProductionYear", 0)
        _raw_name = _s.get("Name", "?")
        _display_name = _raw_name if (not _yr or str(_yr) in _raw_name) else f"{_raw_name} ({_yr})"
        _last = _last_added.get(_sid, "")
        _rows[_tmdb_id] = {
            "Name": _display_name,
            "Year": _yr,
            "Have Episodes": _n_have,
            "Missing Episodes": _n_missing,
            "Total Episodes": _n_total,
            "% Complete": _pct,
            "Last Added": _last,
            "Missing Seasons": "",
            "Gaps": "; ".join(f"S{sn}: {cnt} missing" for sn, cnt in sorted(_season_miss.items())[:5]),
            "Emby ID": _sid,
        }

    df_missing_episodes = pd.DataFrame(list(_rows.values())).sort_values(
        "Missing Episodes", ascending=False
    ) if _rows else pd.DataFrame()

    # Keep raw results for export cell
    missing_raw_results = _results

    return df_missing_episodes, missing_raw_results, ep_count_by_series


@app.cell
def analyze_missing_franchise(
    TMDB_TOKEN, ThreadPoolExecutor, cache_load, cache_save, emby_tmdb_movie_ids,
    httpx, mo, movie_by_tmdb, pd, refresh_btn, threading,
):
    _tmdb_ids = list(emby_tmdb_movie_ids)

    _cached, _cache_status = cache_load("franchise_coll_results")
    if _cached and not refresh_btn.value:
        _coll_results = _cached
        mo.callout(f"Franchise data from cache ({_cache_status})", kind="info")
    else:
        _tok = TMDB_TOKEN
        _tls_tmdb = threading.local()

        def _fetch_movie_coll(tid):
            try:
                if not hasattr(_tls_tmdb, "client"):
                    _tls_tmdb.client = httpx.Client(timeout=10, base_url="https://api.themoviedb.org/3",
                                                    headers={"Authorization": f"Bearer {_tok}"})
                _r = _tls_tmdb.client.get(f"/movie/{tid}", params={"language": "en-US"})
                if _r.status_code != 200:
                    return None
                _coll = _r.json().get("belongs_to_collection")
                return (_coll["id"], _coll["name"]) if _coll else None
            except Exception:
                return None

        with mo.status.spinner(f"Fetching collection info for {len(_tmdb_ids)} movies (15 threads)..."):
            with ThreadPoolExecutor(max_workers=15) as _pool:
                _movie_results = list(_pool.map(_fetch_movie_coll, _tmdb_ids))

        _coll_ids = {}
        for _r in _movie_results:
            if _r:
                _coll_ids[_r[0]] = _r[1]
        _unique_colls = list(_coll_ids.keys())
        _tls_coll = threading.local()

        def _fetch_collection(cid):
            try:
                if not hasattr(_tls_coll, "client"):
                    _tls_coll.client = httpx.Client(timeout=10, base_url="https://api.themoviedb.org/3",
                                                    headers={"Authorization": f"Bearer {_tok}"})
                return _tls_coll.client.get(f"/collection/{cid}", params={"language": "en-US"}).json()
            except Exception:
                return None

        with mo.status.spinner(f"Checking {len(_unique_colls)} collections for gaps (10 threads)..."):
            with ThreadPoolExecutor(max_workers=10) as _pool:
                _coll_results = list(_pool.map(_fetch_collection, _unique_colls))

        cache_save("franchise_coll_results", _coll_results)
        mo.callout("Fetched fresh franchise data", kind="success")

    _franchise_gaps = []
    for _coll_data in _coll_results:
        if not _coll_data:
            continue
        _parts = _coll_data.get("parts", [])
        if len(_parts) <= 1:
            continue

        _have, _missing = [], []
        for _p in sorted(_parts, key=lambda x: x.get("release_date", "") or "9999"):
            if str(_p["id"]) in emby_tmdb_movie_ids:
                _have.append(_p)
            else:
                _yr = (_p.get("release_date") or "")[:4]
                if _yr and _yr.isdigit() and int(_yr) <= 2026:
                    _missing.append(_p)

        if _missing and _have:
            _franchise_gaps.append({
                "Collection": _coll_data.get("name", "?"),
                "TMDB Collection ID": _coll_data.get("id", ""),
                "Total Parts": len(_parts),
                "Have": len(_have),
                "Missing": len(_missing),
                "% Complete": round(len(_have) / len(_parts) * 100),
                "Missing Titles": ", ".join(
                    f"{p['title']} ({(p.get('release_date') or '?')[:4]})" for p in _missing[:5]
                ),
                "Have Titles": ", ".join(
                    movie_by_tmdb.get(str(p["id"]), p["title"]) for p in _have[:5]
                ),
            })

    df_franchise_gaps = pd.DataFrame(_franchise_gaps).sort_values(
        "Missing", ascending=False
    ) if _franchise_gaps else pd.DataFrame()

    return (df_franchise_gaps,)


@app.cell
def analyze_popular_missing(cache_load, cache_save, emby_tmdb_movie_ids, emby_tmdb_series_ids, mo, pd, refresh_btn, time, tmdb_get):
    _cached_pop, _pop_status = cache_load("popular_missing")
    if _cached_pop and not refresh_btn.value:
        _pop_movies = _cached_pop["movies"]
        _pop_series = _cached_pop["series"]
        mo.callout(f"Popular missing data from cache ({_pop_status})", kind="info")
    else:
        _pop_movies, _pop_series = [], []
        _seen_m, _seen_s = set(), set()

        for _src, _path, _pages in [
            ("Top Rated", "/movie/top_rated", 10),
            ("Popular Now", "/movie/popular", 5),
            ("Trending", "/trending/movie/week", 2),
        ]:
            for _pg in range(1, _pages + 1):
                for _m in tmdb_get(_path, page=_pg).get("results", []):
                    _tid = str(_m["id"])
                    if _tid not in emby_tmdb_movie_ids and _tid not in _seen_m:
                        _seen_m.add(_tid)
                        _pop_movies.append({
                            "Title": _m.get("title", _m.get("name", "?")),
                            "Year": (_m.get("release_date") or "")[:4],
                            "Rating": _m.get("vote_average", 0),
                            "Votes": _m.get("vote_count", 0),
                            "TMDB ID": _m["id"],
                            "Source": _src,
                        })
                time.sleep(0.1)

        for _src, _path, _pages in [
            ("Top Rated", "/tv/top_rated", 5),
            ("Popular Now", "/tv/popular", 3),
        ]:
            for _pg in range(1, _pages + 1):
                for _s in tmdb_get(_path, page=_pg).get("results", []):
                    _tid = str(_s["id"])
                    if _tid not in emby_tmdb_series_ids and _tid not in _seen_s:
                        _seen_s.add(_tid)
                        _pop_series.append({
                            "Title": _s["name"],
                            "Year": (_s.get("first_air_date") or "")[:4],
                            "Rating": _s.get("vote_average", 0),
                            "Votes": _s.get("vote_count", 0),
                            "TMDB ID": _s["id"],
                            "Source": _src,
                        })
                time.sleep(0.1)

        cache_save("popular_missing", {"movies": _pop_movies, "series": _pop_series})

    df_pop_movies = pd.DataFrame(_pop_movies).sort_values("Rating", ascending=False) if _pop_movies else pd.DataFrame()
    df_pop_series = pd.DataFrame(_pop_series).sort_values("Rating", ascending=False) if _pop_series else pd.DataFrame()
    return df_pop_movies, df_pop_series


@app.cell
def tab_overview_missing(df_franchise_gaps, df_missing_episodes, df_pop_movies, df_pop_series, go, mo, raw_movies, raw_series):
    _n_miss_eps = int(df_missing_episodes["Missing Episodes"].sum()) if not df_missing_episodes.empty else 0
    _n_series_gaps = len(df_missing_episodes) if not df_missing_episodes.empty else 0
    _n_fran_gaps = len(df_franchise_gaps) if not df_franchise_gaps.empty else 0
    _n_fran_miss = int(df_franchise_gaps["Missing"].sum()) if not df_franchise_gaps.empty else 0

    _stats = mo.hstack([
        mo.stat(value=f"{len(raw_movies):,}", label="Movies in Library"),
        mo.stat(value=f"{len(raw_series):,}", label="Series in Library"),
        mo.stat(value=f"{_n_miss_eps:,}", label="Missing Episodes",
                caption=f"across {_n_series_gaps} series", direction="decrease"),
        mo.stat(value=f"{_n_fran_miss:,}", label="Missing Franchise Movies",
                caption=f"across {_n_fran_gaps} collections", direction="decrease"),
        mo.stat(value=f"{len(df_pop_movies):,}", label="Popular Movies Missing",
                caption="TMDB top rated + trending", direction="decrease"),
    ], justify="space-around", gap="1rem")

    _fig = go.Figure()
    if not df_missing_episodes.empty:
        _fig.add_trace(go.Pie(labels=["Have", "Missing"],
                              values=[int(df_missing_episodes["Have Episodes"].sum()), _n_miss_eps],
                              name="Episodes", domain={"x": [0, 0.3]},
                              marker_colors=["#2ecc71", "#e74c3c"], hole=0.5))
    if not df_franchise_gaps.empty:
        _fig.add_trace(go.Pie(labels=["Have", "Missing"],
                              values=[int(df_franchise_gaps["Have"].sum()), _n_fran_miss],
                              name="Franchises", domain={"x": [0.35, 0.65]},
                              marker_colors=["#3498db", "#e67e22"], hole=0.5))
    _fig.add_trace(go.Pie(labels=["In Library", "Missing"],
                          values=[len(raw_movies), len(df_pop_movies) if not df_pop_movies.empty else 0],
                          name="Popular", domain={"x": [0.7, 1]},
                          marker_colors=["#9b59b6", "#f39c12"], hole=0.5))
    _fig.update_layout(title_text="Content Completeness", height=350, template="plotly_white",
                       annotations=[
                           {"text": "Episodes", "x": 0.13, "y": 0.5, "font_size": 12, "showarrow": False},
                           {"text": "Franchises", "x": 0.50, "y": 0.5, "font_size": 12, "showarrow": False},
                           {"text": "Popular", "x": 0.87, "y": 0.5, "font_size": 12, "showarrow": False},
                       ])
    overview_missing_tab = mo.vstack([_stats, _fig])
    return (overview_missing_tab,)


@app.cell
def tab_episodes_filters(mo):
    missing_range = mo.ui.range_slider(start=1, stop=500, value=[5, 500], step=1, label="Missing episodes range")
    active_direction = mo.ui.dropdown(
        options={"Added within": "within", "NOT added within": "not_within"},
        value="Added within",
        label="Direction",
    )
    active_period = mo.ui.dropdown(
        options={
            "All time": "All",
            "Last 24 hours": "1",
            "Last 3 days": "3",
            "Last 7 days": "7",
            "Last 14 days": "14",
            "Last 30 days": "30",
            "Last 60 days": "60",
            "Last 90 days": "90",
            "Last 6 months": "180",
            "Last year": "365",
            "Last 2 years": "730",
        },
        value="All time",
        label="Time period",
    )
    return active_direction, active_period, missing_range


@app.cell
def tab_episodes(EMBY_HOST, EMBY_SERVER_ID, active_direction, active_period, datetime, df_missing_episodes, go, missing_range, mo, timedelta):
    if df_missing_episodes.empty:
        episodes_tab = mo.callout("No series with missing episodes found.", kind="info")
    else:
        _min, _max = missing_range.value
        _filtered = df_missing_episodes[
            (df_missing_episodes["Missing Episodes"] >= _min) &
            (df_missing_episodes["Missing Episodes"] <= _max)
        ]

        # Apply time period filter
        if active_period.value != "All":
            _days = int(active_period.value)
            _cutoff = (datetime.now() - timedelta(days=_days)).strftime("%Y-%m-%d")
            if active_direction.value == "within":
                _filtered = _filtered[_filtered["Last Added"] >= _cutoff]
            else:
                _filtered = _filtered[(_filtered["Last Added"] < _cutoff) | (_filtered["Last Added"] == "")]

        _top = _filtered.head(25)
        _fig = go.Figure()
        _fig.add_trace(go.Bar(y=_top["Name"], x=_top["Have Episodes"], name="Have",
                              orientation="h", marker_color="#2ecc71"))
        _fig.add_trace(go.Bar(y=_top["Name"], x=_top["Missing Episodes"], name="Missing",
                              orientation="h", marker_color="#e74c3c"))
        _fig.update_layout(barmode="stack", title="Series with Most Missing Episodes",
                           xaxis_title="Episode Count", height=max(400, len(_top) * 30),
                           template="plotly_white", yaxis={"autorange": "reversed"})

        _fig2 = go.Figure(go.Histogram(x=_filtered["% Complete"], nbinsx=20, marker_color="#3498db"))
        _fig2.update_layout(title="Series Completeness Distribution", xaxis_title="% Complete",
                            yaxis_title="Number of Series", height=300, template="plotly_white")

        _records = []
        for _, _r in _filtered.iterrows():
            _eid = _r.get("Emby ID", "")
            _link = f"{EMBY_HOST}/web/index.html#!/item?id={_eid}&serverId={EMBY_SERVER_ID}"
            _records.append({
                "Name": mo.Html(f'<a href="{_link}" target="_blank" style="position:relative;z-index:10">{_r["Name"]}</a>'),
                "Year": _r["Year"],
                "Have": _r["Have Episodes"],
                "Missing": _r["Missing Episodes"],
                "Total": _r["Total Episodes"],
                "% Complete": _r["% Complete"],
                "Last Added": _r["Last Added"],
                "Gaps": _r["Gaps"],
            })
        _total_miss = int(_filtered["Missing Episodes"].sum())
        episodes_tab = mo.vstack([
            mo.md("## Missing Episodes"),
            mo.hstack([missing_range, active_direction, active_period], gap="1rem"),
            mo.callout(f"{len(_filtered)} series with {_min}-{_max} missing — "
                       f"{_total_miss:,} total missing episodes", kind="warn"),
            _fig, _fig2,
            mo.ui.table(_records, pagination=True, page_size=25, label="Series Gaps"),
        ])
    return (episodes_tab,)


@app.cell
def tab_franchises(df_franchise_gaps, go, mo):
    if df_franchise_gaps.empty:
        franchise_tab = mo.callout("No franchise gaps found.", kind="info")
    else:
        _top = df_franchise_gaps.head(25)
        _fig = go.Figure()
        _fig.add_trace(go.Bar(y=_top["Collection"], x=_top["Have"], name="Have",
                              orientation="h", marker_color="#2ecc71"))
        _fig.add_trace(go.Bar(y=_top["Collection"], x=_top["Missing"], name="Missing",
                              orientation="h", marker_color="#e74c3c"))
        _fig.update_layout(barmode="stack", title="Incomplete Movie Franchises",
                           xaxis_title="Movies in Collection", height=max(400, len(_top) * 30),
                           template="plotly_white", yaxis={"autorange": "reversed"})

        _records = [
            {"Collection": mo.Html(f'<a href="https://www.themoviedb.org/collection/{r["TMDB Collection ID"]}" target="_blank" style="position:relative;z-index:10">{r["Collection"]}</a>'),
             "Total Parts": r["Total Parts"], "Have": r["Have"], "Missing": r["Missing"],
             "% Complete": r["% Complete"], "Missing Titles": r["Missing Titles"]}
            for r in df_franchise_gaps[["Collection", "TMDB Collection ID", "Total Parts", "Have", "Missing",
                                        "% Complete", "Missing Titles"]].to_dict("records")
        ]
        franchise_tab = mo.vstack([
            mo.md("## Incomplete Franchises"),
            mo.callout(f"{len(df_franchise_gaps)} incomplete collections — "
                       f"{int(df_franchise_gaps['Missing'].sum())} movies missing", kind="warn"),
            _fig,
            mo.ui.table(_records, pagination=True, page_size=25, label="Franchise Gaps"),
        ])
    return (franchise_tab,)


@app.cell
def tab_popular_filters(mo):
    rating_slider = mo.ui.slider(start=5, stop=10, value=7, step=0.5, label="Minimum TMDB rating")
    year_slider = mo.ui.slider(start=1990, stop=2026, value=2010, step=1, label="Movies/Series from year")
    source_dropdown = mo.ui.dropdown(
        options={"All": "All", "Top Rated": "Top Rated", "Popular Now": "Popular Now", "Trending": "Trending"},
        value="All", label="Source")
    return rating_slider, source_dropdown, year_slider


@app.cell
def tab_popular(df_pop_movies, df_pop_series, go, mo, pd, rating_slider, source_dropdown, year_slider):
    _movies = df_pop_movies.copy() if not df_pop_movies.empty else pd.DataFrame()
    if not _movies.empty:
        _movies["_yr"] = pd.to_numeric(_movies["Year"], errors="coerce").fillna(0).astype(int)
        _movies = _movies[(_movies["Rating"] >= rating_slider.value) & (_movies["_yr"] >= year_slider.value)]
        if source_dropdown.value != "All":
            _movies = _movies[_movies["Source"] == source_dropdown.value]
        _movies = _movies.drop(columns=["_yr"])

    _series = df_pop_series.copy() if not df_pop_series.empty else pd.DataFrame()
    if not _series.empty:
        _series["_yr"] = pd.to_numeric(_series["Year"], errors="coerce").fillna(0).astype(int)
        _series = _series[(_series["Rating"] >= rating_slider.value) & (_series["_yr"] >= year_slider.value)]
        if source_dropdown.value != "All":
            _series = _series[_series["Source"] == source_dropdown.value]
        _series = _series.drop(columns=["_yr"])

    _fig_m = go.Figure()
    if not _movies.empty:
        _top_m = _movies.head(20)
        _fig_m.add_trace(go.Bar(
            y=_top_m["Title"] + " (" + _top_m["Year"].astype(str) + ")",
            x=_top_m["Rating"], orientation="h",
            marker_color=["#e74c3c" if r >= 8 else "#e67e22" if r >= 7 else "#f39c12" for r in _top_m["Rating"]],
            text=[f"{r:.1f}" for r in _top_m["Rating"]], textposition="outside"))
        _fig_m.update_layout(title="Top Missing Movies", xaxis_title="TMDB Rating", xaxis_range=[5, 10],
                             height=max(400, len(_top_m) * 28), template="plotly_white",
                             yaxis={"autorange": "reversed"})

    _fig_s = go.Figure()
    if not _series.empty:
        _top_s = _series.head(20)
        _fig_s.add_trace(go.Bar(
            y=_top_s["Title"] + " (" + _top_s["Year"].astype(str) + ")",
            x=_top_s["Rating"], orientation="h",
            marker_color=["#e74c3c" if r >= 8 else "#e67e22" if r >= 7 else "#f39c12" for r in _top_s["Rating"]],
            text=[f"{r:.1f}" for r in _top_s["Rating"]], textposition="outside"))
        _fig_s.update_layout(title="Top Missing TV Series", xaxis_title="TMDB Rating", xaxis_range=[5, 10],
                             height=max(400, len(_top_s) * 28), template="plotly_white",
                             yaxis={"autorange": "reversed"})

    popular_tab = mo.vstack([
        mo.md("## Popular & Top Rated Content You're Missing"),
        mo.hstack([rating_slider, year_slider, source_dropdown], gap="2rem"),
        mo.hstack([
            mo.stat(value=f"{len(_movies):,}", label="Missing Movies"),
            mo.stat(value=f"{len(_series):,}", label="Missing Series"),
        ], justify="start", gap="2rem"),
        mo.md("### Movies"),
        _fig_m if not _movies.empty else mo.callout("No missing movies matching filters", kind="info"),
        mo.ui.table(
            [{"Title": mo.Html(f'<a href="https://www.themoviedb.org/movie/{r["TMDB ID"]}" target="_blank" style="position:relative;z-index:10">{r["Title"]}</a>'),
              "Year": r["Year"], "Rating": r["Rating"], "Votes": r["Votes"], "Source": r["Source"]}
             for r in _movies[["Title", "Year", "Rating", "Votes", "Source", "TMDB ID"]].to_dict("records")],
            pagination=True, page_size=25, label="Missing Popular Movies") if not _movies.empty else mo.md(""),
        mo.md("### TV Series"),
        _fig_s if not _series.empty else mo.callout("No missing series matching filters", kind="info"),
        mo.ui.table(
            [{"Title": mo.Html(f'<a href="https://www.themoviedb.org/tv/{r["TMDB ID"]}" target="_blank" style="position:relative;z-index:10">{r["Title"]}</a>'),
              "Year": r["Year"], "Rating": r["Rating"], "Votes": r["Votes"], "Source": r["Source"]}
             for r in _series[["Title", "Year", "Rating", "Votes", "Source", "TMDB ID"]].to_dict("records")],
            pagination=True, page_size=25, label="Missing Popular Series") if not _series.empty else mo.md(""),
    ])
    return (popular_tab,)


@app.cell
def export_filters(mo):
    export_max_shows = mo.ui.slider(start=0, stop=500, value=0, step=5, label="Max shows to export (0 = all)")
    export_missing_range = mo.ui.range_slider(start=1, stop=100, value=[1, 20], step=1, label="Missing episodes range")
    export_min_complete = mo.ui.slider(start=0, stop=100, value=50, step=5, label="Min % complete")
    export_btn = mo.ui.run_button(label="Export for Czech Tracker Scraper")
    return export_btn, export_max_shows, export_min_complete, export_missing_range


@app.cell
def tab_export_data(
    df_missing_episodes, export_max_shows, export_min_complete,
    export_missing_range, missing_raw_results, raw_series, ep_count_by_series,
):
    """Build export data reactively from filters."""
    _series_map = {_s["Id"]: _s for _s in raw_series}
    _min_miss, _max_miss = export_missing_range.value

    _export_data = []
    _exported_tmdb = set()
    for _sid, _missing_items, _n_missing in missing_raw_results:
        if _n_missing < _min_miss or _n_missing > _max_miss:
            continue
        _s = _series_map.get(_sid, {})
        _pids = _s.get("ProviderIds", {})
        _tmdb_id = _pids.get("Tmdb") or _pids.get("tmdb") or _sid
        if _tmdb_id in _exported_tmdb:
            continue

        _n_have = ep_count_by_series.get(_sid, 0)
        _n_total = _n_have + _n_missing
        _pct = round(_n_have / _n_total * 100, 1) if _n_total > 0 else 0
        if _pct < export_min_complete.value:
            continue

        _show_name = _s.get("OriginalTitle") or _s.get("SortName") or _s.get("Name", "Unknown")
        _episodes = []
        for _ep in _missing_items:
            _sn = _ep.get("ParentIndexNumber")
            _en = _ep.get("IndexNumber")
            if _sn is not None and _en is not None and _sn > 0:
                _episodes.append({"season": _sn, "episode": _en})

        if not _episodes:
            continue

        _entry = {
            "show": _show_name,
            "emby_id": _sid,
            "episodes": sorted(_episodes, key=lambda e: (e["season"], e["episode"])),
        }
        _imdb = _pids.get("Imdb") or _pids.get("IMDB") or _pids.get("imdb")
        if _imdb:
            _entry["imdb_id"] = _imdb
        _year = _s.get("ProductionYear")
        if _year:
            _entry["year"] = _year

        _export_data.append(_entry)
        _exported_tmdb.add(_tmdb_id)

    _export_data.sort(key=lambda e: len(e["episodes"]))
    _max = export_max_shows.value
    export_data_ready = _export_data if _max == 0 else _export_data[:_max]
    return (export_data_ready,)


@app.cell
def tab_export_preview(EMBY_HOST, EMBY_SERVER_ID, export_data_ready, export_max_shows, export_min_complete, export_missing_range, export_btn, mo):
    """Show preview table and filters — always visible."""
    _preview_rows = []
    for _e in export_data_ready:
        _eid = _e.get("emby_id", "")
        _link = f"{EMBY_HOST}/web/index.html#!/item?id={_eid}&serverId={EMBY_SERVER_ID}"
        _imdb = _e.get("imdb_id", "")
        _preview_rows.append({
            "Show": mo.Html(f'<a href="{_link}" target="_blank" style="position:relative;z-index:10">{_e["show"]}</a>'),
            "Year": _e.get("year", ""),
            "IMDB": mo.Html(f'<a href="https://www.imdb.com/title/{_imdb}" target="_blank" style="position:relative;z-index:10">{_imdb}</a>') if _imdb else "",
            "Missing Eps": len(_e["episodes"]),
            "Episodes": ", ".join(f"S{ep['season']:02d}E{ep['episode']:02d}" for ep in _e["episodes"][:10]),
        })

    export_preview = mo.vstack([
        mo.md("## Export Missing Episodes for Czech Tracker"),
        mo.callout(
            "Filter shows, preview what will be exported, then click Export. "
            "Output: `/Users/dodko/DEV/torrents/data/missing_episodes.json` (symlink to latest)",
            kind="info",
        ),
        mo.hstack([export_missing_range, export_min_complete, export_max_shows], gap="1rem"),
        mo.hstack([
            mo.stat(value=f"{len(export_data_ready)}", label="Shows to Export"),
            mo.stat(value=f"{sum(len(e['episodes']) for e in export_data_ready)}", label="Total Episodes"),
        ], justify="start", gap="2rem"),
        mo.ui.table(_preview_rows, pagination=True, page_size=15, label="Export Preview") if _preview_rows else mo.md("No shows match filters"),
        export_btn,
    ])
    return (export_preview,)


@app.cell
def tab_export_do(Path, datetime, export_btn, export_data_ready, export_preview, jsonlib, mo, os):
    """Execute export when run_button is clicked."""
    if export_btn.value and export_data_ready:
        _now = datetime.now()
        _ts = int(_now.timestamp())
        _export_dir = Path("/Users/dodko/DEV/torrents/data")
        _export_dir.mkdir(parents=True, exist_ok=True)
        _out_path = _export_dir / f"missing_episodes_{_ts}.json"
        _link_path = _export_dir / "missing_episodes.json"
        _total_eps = sum(len(e["episodes"]) for e in export_data_ready)
        _export_wrapper = {
            "exported_at": _now.isoformat(),
            "exported_at_unix": _ts,
            "total_shows": len(export_data_ready),
            "total_episodes": _total_eps,
            "shows": export_data_ready,
        }
        _out_path.write_text(jsonlib.dumps(_export_wrapper, indent=2, ensure_ascii=False))
        # Symlink missing_episodes.json -> latest timestamped file
        if _link_path.is_symlink() or _link_path.exists():
            _link_path.unlink()
        os.symlink(_out_path.name, _link_path)
        export_tab = mo.vstack([
            export_preview,
            mo.callout(
                f"Exported {len(export_data_ready)} shows ({_total_eps} episodes) to {_out_path}\n"
                f"Symlink: {_link_path} → {_out_path.name}",
                kind="success",
            ),
        ])
    else:
        export_tab = export_preview
    return (export_tab,)


@app.cell
def dashboard_missing(episodes_tab, export_tab, franchise_tab, mo, overview_missing_tab, popular_tab):
    tabs = mo.ui.tabs({
        "Overview": overview_missing_tab,
        "Missing Episodes": episodes_tab,
        "Incomplete Franchises": franchise_tab,
        "Popular Missing": popular_tab,
        "Export for Scraper": export_tab,
    })
    tabs
    return (tabs,)


if __name__ == "__main__":
    app.run()
