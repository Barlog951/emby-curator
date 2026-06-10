# /// script
# [tool.marimo.display]
# theme = "dark"
# ///

import marimo

__generated_with = "0.20.2"
app = marimo.App(width="full", app_title="Emby Unplayed Content Dashboard")


@app.cell
def imports():
    import sys
    from datetime import datetime, timedelta

    import marimo as mo
    import pandas as pd
    import plotly.graph_objects as go

    sys.path.insert(0, str(mo.notebook_dir()))
    import shared
    return datetime, go, mo, pd, shared, timedelta


@app.cell
def config(mo, shared):
    EMBY_HOST, EMBY_API_KEY = shared.get_emby_config()
    mo.stop(
        not EMBY_HOST or not EMBY_API_KEY,
        mo.callout(
            mo.md(
                "**Emby credentials missing.** Set `EMBY_HOST` and `EMBY_API_KEY` as "
                "environment variables, or copy `dashboards/.env.example` to "
                "`dashboards/.env` and fill in your values."
            ),
            kind="danger",
        ),
    )
    mo.md("""# Emby Unplayed Content Dashboard""")
    return EMBY_API_KEY, EMBY_HOST


@app.cell
def api_helpers(EMBY_API_KEY, EMBY_HOST, mo, shared):
    _client, api_get = shared.make_emby_client(EMBY_HOST, EMBY_API_KEY)

    def fetch_all_items(path, item_type, fields="", extra_params=None, user_id=None):
        """Page through all items of a given type."""
        _all = []
        _offset = 0
        _batch = 500
        while True:
            _params = {
                "Recursive": "true",
                "IncludeItemTypes": item_type,
                "StartIndex": str(_offset),
                "Limit": str(_batch),
            }
            if fields:
                _params["Fields"] = fields
            if extra_params:
                _params.update(extra_params)
            if user_id:
                _data = api_get(f"Users/{user_id}/Items", **_params)
            else:
                _data = api_get(path, **_params)
            _items = _data.get("Items", [])
            if not _items:
                break
            _all.extend(_items)
            if _offset + len(_items) >= _data.get("TotalRecordCount", 0):
                break
            _offset += _batch
        return _all

    try:
        with mo.status.spinner("Connecting to Emby API..."):
            users = api_get("Users")
    except Exception as _e:
        mo.stop(True, mo.callout(f"Cannot reach Emby at {EMBY_HOST}: {_e}", kind="danger"))
    mo.callout(f"Connected to Emby — {len(users)} users found", kind="success")
    return api_get, fetch_all_items, users


@app.cell
def load_played_data(fetch_all_items, mo, users):
    played_movie_ids = set()
    played_series_ids = set()
    played_episode_ids = set()
    series_watchers = {}

    with mo.status.progress_bar(
        total=len(users),
        title="Loading played data",
        subtitle=f"0/{len(users)} users scanned",
        completion_title="Played data loaded",
        completion_subtitle=f"{len(users)} users scanned",
        show_eta=True,
        show_rate=False,
    ) as _bar:
        for _idx, _u in enumerate(users, start=1):
            _uid = _u["Id"]
            _uname = _u["Name"]

            _movies = fetch_all_items("Items", "Movie", extra_params={"Filters": "IsPlayed"}, user_id=_uid)
            for _m in _movies:
                played_movie_ids.add(_m["Id"])

            _episodes = fetch_all_items(
                "Items", "Episode", fields="SeriesId",
                extra_params={"Filters": "IsPlayed"}, user_id=_uid,
            )
            for _ep in _episodes:
                played_episode_ids.add(_ep["Id"])
                _sid = _ep.get("SeriesId")
                if _sid:
                    played_series_ids.add(_sid)
                    if _sid not in series_watchers:
                        series_watchers[_sid] = set()
                    series_watchers[_sid].add(_uname)

            _bar.update(subtitle=f"{_idx}/{len(users)} users scanned ({_uname})")

    return played_episode_ids, played_movie_ids, played_series_ids, series_watchers


@app.cell
def load_library_data(fetch_all_items, mo):
    with mo.status.spinner("Loading movie & series metadata..."):
        all_movies = fetch_all_items(
            "Items", "Movie",
            fields="Genres,ProductionYear,Path,MediaSources,DateCreated,MediaStreams",
        )
        all_series = fetch_all_items(
            "Items", "Series",
            fields="Genres,ProductionYear,Path,DateCreated,ChildCount",
        )
        all_episodes = fetch_all_items(
            "Items", "Episode",
            fields="SeriesId,SeriesName,DateCreated",
        )
    return all_episodes, all_movies, all_series


@app.cell
def library_helper():
    def guess_library(path):
        """Guess the library name from the file path."""
        _p = path or ""
        if "/Movies/4K/" in _p or "/Movies/HD/" in _p:
            return "HD & 4K"
        elif "/Movies/DiViX/" in _p or "/Vianocne Rozpravky/" in _p:
            return "LQ - Movies"
        elif "/Movies/Serials/" in _p:
            return "SERIALS"
        elif "/Movies/Dokumenty/" in _p:
            return "Documents"
        return "Other"
    return (guess_library,)


@app.cell
def build_dataframes(
    all_episodes, all_movies, all_series, datetime, guess_library, pd,
    played_episode_ids, played_movie_ids, played_series_ids, series_watchers,
):
    _movie_rows = []
    for _m in all_movies:
        _size_bytes = sum(_ms.get("Size", 0) for _ms in _m.get("MediaSources", []))
        _audio_lang = "Unknown"
        for _ms in _m.get("MediaSources", []):
            for _stream in _ms.get("MediaStreams", []):
                if _stream.get("Type") == "Audio":
                    _audio_lang = _stream.get("DisplayLanguage") or _stream.get("Language") or "Unknown"
                    break
            if _audio_lang != "Unknown":
                break

        _date_created = _m.get("DateCreated", "")[:10]
        try:
            _days = (datetime.now() - datetime.fromisoformat(_date_created)).days if _date_created else 0
        except Exception:
            _days = 0

        _genres = _m.get("Genres", [])
        _year = _m.get("ProductionYear")
        _decade = f"{(_year // 10) * 10}s" if _year else "Unknown"

        _movie_rows.append({
            "Id": _m["Id"],
            "Name": _m.get("Name", "Unknown"),
            "Year": _year or 0,
            "Decade": _decade,
            "Genres": ", ".join(_genres) if _genres else "(No genre)",
            "PrimaryGenre": _genres[0] if _genres else "(No genre)",
            "AudioLanguage": _audio_lang,
            "SizeGB": round(_size_bytes / (1024**3), 2),
            "SizeBytes": _size_bytes,
            "Path": _m.get("Path", ""),
            "DateAdded": _date_created,
            "DaysInLibrary": _days,
            "Played": _m["Id"] in played_movie_ids,
            "Library": guess_library(_m.get("Path", "")),
        })

    df_movies = pd.DataFrame(_movie_rows)

    # Count played episodes per series
    episode_counts = {}
    for _ep in all_episodes:
        _sid = _ep.get("SeriesId", "")
        if _sid:
            if _sid not in episode_counts:
                episode_counts[_sid] = {"total": 0, "played": 0}
            episode_counts[_sid]["total"] += 1
            if _ep["Id"] in played_episode_ids:
                episode_counts[_sid]["played"] += 1

    _series_rows = []
    for _s in all_series:
        _sid = _s["Id"]
        _genres = _s.get("Genres", [])
        _year = _s.get("ProductionYear")
        _date_created = _s.get("DateCreated", "")[:10]
        try:
            _days = (datetime.now() - datetime.fromisoformat(_date_created)).days if _date_created else 0
        except Exception:
            _days = 0

        _ec = episode_counts.get(_sid, {"total": 0, "played": 0})
        _total_eps = _ec["total"]
        _played_eps = _ec["played"]
        _pct = round(_played_eps / _total_eps * 100, 1) if _total_eps > 0 else 0

        _watchers = series_watchers.get(_sid, set())

        _series_rows.append({
            "Id": _sid,
            "Name": _s.get("Name", "Unknown"),
            "Year": _year or 0,
            "Genres": ", ".join(_genres) if _genres else "(No genre)",
            "PrimaryGenre": _genres[0] if _genres else "(No genre)",
            "TotalEpisodes": _total_eps,
            "PlayedEpisodes": _played_eps,
            "PctWatched": _pct,
            "Played": _sid in played_series_ids,
            "DateAdded": _date_created,
            "DaysInLibrary": _days,
            "Watchers": ", ".join(sorted(_watchers)[:5]) if _watchers else "",
            "WatcherCount": len(_watchers),
            "Library": guess_library(_s.get("Path", "")),
        })

    df_series = pd.DataFrame(_series_rows)

    return df_movies, df_series, episode_counts


@app.cell
def tab_overview(df_movies, df_series, go, mo, pd):
    _total_movies = len(df_movies)
    _unplayed_movies = len(df_movies[~df_movies["Played"]])
    _total_series = len(df_series)
    _unplayed_series = len(df_series[~df_series["Played"]])
    _total_eps = df_series["TotalEpisodes"].sum()
    _unplayed_eps = _total_eps - df_series["PlayedEpisodes"].sum()
    _wasted_tb = df_movies[~df_movies["Played"]]["SizeGB"].sum() / 1024

    _stats_row = mo.hstack([
        mo.stat(value=f"{_total_movies:,}", label="Total Movies"),
        mo.stat(
            value=f"{_unplayed_movies:,}",
            label="Unplayed Movies",
            caption=f"{_unplayed_movies/_total_movies*100:.1f}% of collection",
            direction="decrease",
        ),
        mo.stat(value=f"{_total_series:,}", label="Total Series"),
        mo.stat(
            value=f"{_unplayed_series:,}",
            label="Unwatched Series",
            caption=f"{_unplayed_series/_total_series*100:.1f}% of collection",
            direction="decrease",
        ),
        mo.stat(
            value=f"{_wasted_tb:.1f} TB",
            label="Unplayed Movie Storage",
            caption="Reclaimable disk space",
            direction="decrease",
        ),
    ], justify="space-around", gap="1rem")

    _fig_pie = go.Figure()
    _fig_pie.add_trace(go.Pie(
        labels=["Played", "Unplayed"],
        values=[_total_movies - _unplayed_movies, _unplayed_movies],
        name="Movies",
        domain={"x": [0, 0.45]},
        marker_colors=["#2ecc71", "#e74c3c"],
        hole=0.4,
        hovertemplate="<b>%{label}</b><br>%{value:,} movies (%{percent})<extra></extra>",
    ))
    _fig_pie.add_trace(go.Pie(
        labels=["Watched", "Unwatched"],
        values=[_total_series - _unplayed_series, _unplayed_series],
        name="Series",
        domain={"x": [0.55, 1]},
        marker_colors=["#3498db", "#e67e22"],
        hole=0.4,
        hovertemplate="<b>%{label}</b><br>%{value:,} series (%{percent})<extra></extra>",
    ))
    _fig_pie.update_layout(
        title_text="Content Utilization",
        annotations=[
            {"text": "Movies", "x": 0.20, "y": 0.5, "font_size": 14, "showarrow": False},
            {"text": "Series", "x": 0.80, "y": 0.5, "font_size": 14, "showarrow": False},
        ],
        height=350,
        template="plotly_dark",
    )

    _bar_data = pd.DataFrame({
        "Type": ["Movies", "Series", "Episodes"],
        "Unplayed %": [
            round(_unplayed_movies / _total_movies * 100, 1),
            round(_unplayed_series / _total_series * 100, 1),
            round(_unplayed_eps / _total_eps * 100, 1) if _total_eps > 0 else 0,
        ],
        "Unplayed": [_unplayed_movies, _unplayed_series, int(_unplayed_eps)],
        "Total": [_total_movies, _total_series, int(_total_eps)],
    })
    _fig_bar = go.Figure(go.Bar(
        x=_bar_data["Type"],
        y=_bar_data["Unplayed %"],
        text=[f"{v}% ({u:,}/{t:,})" for v, u, t in zip(_bar_data["Unplayed %"], _bar_data["Unplayed"], _bar_data["Total"])],
        textposition="outside",
        marker_color=["#e74c3c", "#e67e22", "#f39c12"],
        customdata=list(zip(_bar_data["Unplayed"], _bar_data["Total"])),
        hovertemplate=(
            "<b>%{x}</b><br>%{y:.1f}% unplayed"
            "<br>%{customdata[0]:,} of %{customdata[1]:,}<extra></extra>"
        ),
    ))
    _fig_bar.update_layout(
        title="Unplayed Content by Type",
        yaxis_title="% Unplayed",
        yaxis_range=[0, 70],
        height=350,
        template="plotly_dark",
    )

    overview_tab = mo.vstack([
        _stats_row,
        mo.hstack([_fig_pie, _fig_bar], widths="equal"),
    ])
    return (overview_tab,)


@app.cell
def tab_by_library(df_movies, df_series, go, mo, pd):
    # Aggregate movie stats by library using groupby+agg
    _lib_movie_agg = df_movies.groupby("Library").agg({
        "Id": "count",
        "Played": lambda x: (~x).sum(),
        "SizeGB": "sum",
    }).reset_index()
    _lib_movie_agg.columns = ["Library", "Total Movies", "Unplayed", "Total Size (GB)"]
    _lib_movie_agg["Unplayed Size (GB)"] = df_movies[~df_movies["Played"]].groupby("Library")["SizeGB"].sum().values
    _lib_movie_agg["% Unplayed"] = (
        _lib_movie_agg["Unplayed"] / _lib_movie_agg["Total Movies"] * 100
    ).round(1)
    _lib_movie_agg["Total Size (GB)"] = _lib_movie_agg["Total Size (GB)"].round(1)
    _lib_movie_agg["Unplayed Size (GB)"] = _lib_movie_agg["Unplayed Size (GB)"].round(1)
    _df_lib_movies = _lib_movie_agg.sort_values("Library")

    _lib_series_stats = []
    for _lib in sorted(df_series["Library"].unique()):
        _lib_df = df_series[df_series["Library"] == _lib]
        _t = len(_lib_df)
        _u = len(_lib_df[~_lib_df["Played"]])
        _lib_series_stats.append({
            "Library": _lib,
            "Total Series": _t,
            "Unwatched": _u,
            "% Unwatched": round(_u / _t * 100, 1) if _t > 0 else 0,
        })
    _df_lib_series = pd.DataFrame(_lib_series_stats)

    _fig = go.Figure()
    _fig.add_trace(go.Bar(
        name="Played", x=_df_lib_movies["Library"],
        y=_df_lib_movies["Total Movies"] - _df_lib_movies["Unplayed"],
        marker_color="#2ecc71",
        hovertemplate="<b>%{x}</b><br>%{y:,} played movies<extra></extra>",
    ))
    _fig.add_trace(go.Bar(
        name="Unplayed", x=_df_lib_movies["Library"],
        y=_df_lib_movies["Unplayed"],
        marker_color="#e74c3c",
        hovertemplate="<b>%{x}</b><br>%{y:,} unplayed movies<extra></extra>",
    ))
    _fig.update_layout(
        barmode="stack", title="Movies by Library: Played vs Unplayed",
        height=400, template="plotly_dark",
    )

    library_tab = mo.vstack([
        mo.md("## Movies by Library"),
        _fig,
        mo.ui.table(_df_lib_movies, label="Movie Libraries"),
        mo.md("## Series by Library"),
        mo.ui.table(_df_lib_series, label="Series Libraries"),
    ])
    return (library_tab,)


@app.cell
def tab_cleanup_filters(df_movies, mo):
    _all_genres = sorted(df_movies["PrimaryGenre"].unique().tolist())
    _all_decades = sorted(df_movies["Decade"].unique().tolist())

    size_slider = mo.ui.slider(
        start=0, stop=100, value=0, step=1,
        label="Minimum file size (GB)",
    )
    genre_dropdown = mo.ui.dropdown(
        options={"All": "All", **{g: g for g in _all_genres}},
        value="All",
        label="Filter by genre",
    )
    decade_dropdown = mo.ui.dropdown(
        options={"All": "All", **{d: d for d in _all_decades}},
        value="All",
        label="Filter by decade",
    )
    return decade_dropdown, genre_dropdown, size_slider


@app.cell
def tab_cleanup(decade_dropdown, df_movies, genre_dropdown, go, mo, size_slider):
    _filtered = df_movies[~df_movies["Played"]].copy()
    _filtered = _filtered[_filtered["SizeGB"] >= size_slider.value]
    if genre_dropdown.value != "All":
        _filtered = _filtered[_filtered["PrimaryGenre"] == genre_dropdown.value]
    if decade_dropdown.value != "All":
        _filtered = _filtered[_filtered["Decade"] == decade_dropdown.value]

    _filtered = _filtered.sort_values("SizeGB", ascending=False)

    _total_reclaimable = _filtered["SizeGB"].sum()
    _count = len(_filtered)

    _display_df = _filtered[["Name", "Year", "PrimaryGenre", "Decade", "AudioLanguage", "SizeGB", "Path"]].copy()
    _display_df.columns = ["Name", "Year", "Genre", "Decade", "Language", "Size (GB)", "Path"]

    _top20 = _filtered.head(20)
    _fig = go.Figure(go.Bar(
        y=_top20["Name"],
        x=_top20["SizeGB"],
        orientation="h",
        marker_color="#e74c3c",
        text=[f"{s:.1f} GB" for s in _top20["SizeGB"]],
        textposition="outside",
        customdata=list(zip(_top20["Year"], _top20["PrimaryGenre"])),
        hovertemplate=(
            "<b>%{y}</b> (%{customdata[0]})<br>"
            "Size: %{x:.1f} GB<br>Genre: %{customdata[1]}<extra></extra>"
        ),
    ))
    _fig.update_layout(
        title="Biggest Unplayed Movies",
        xaxis_title="Size (GB)", yaxis_title="",
        height=max(400, len(_top20) * 28),
        template="plotly_dark",
        yaxis={"autorange": "reversed"},
    )

    cleanup_tab = mo.vstack([
        mo.md("## Cleanup: Unplayed Movies by Size"),
        mo.hstack([size_slider, genre_dropdown, decade_dropdown], gap="2rem"),
        mo.hstack([
            mo.stat(value=f"{_count:,}", label="Matching Movies"),
            mo.stat(
                value=f"{_total_reclaimable:.1f} GB" if _total_reclaimable < 1024 else f"{_total_reclaimable/1024:.2f} TB",
                label="Reclaimable Space",
            ),
        ], justify="start", gap="2rem"),
        _fig,
        mo.ui.table(_display_df.to_dict("records"), pagination=True, page_size=25, label="Unplayed Movies"),
    ])
    return (cleanup_tab,)


@app.cell
def tab_language(df_movies, go, mo, pd):
    _total_by_lang = df_movies.groupby("AudioLanguage").size().rename("Total")
    _played_by_lang = df_movies[df_movies["Played"]].groupby("AudioLanguage").size().rename("Played")
    _df_lang = pd.concat([_total_by_lang, _played_by_lang], axis=1).fillna(0).astype(int)
    _df_lang["Unplayed"] = _df_lang["Total"] - _df_lang["Played"]
    _df_lang["% Unplayed"] = (_df_lang["Unplayed"] / _df_lang["Total"] * 100).round(1)
    _df_lang = _df_lang.reset_index().rename(columns={"AudioLanguage": "Language"}).sort_values("Total", ascending=False)

    _top_langs = _df_lang.head(15)
    _fig1 = go.Figure()
    _fig1.add_trace(go.Bar(
        name="Played", x=_top_langs["Language"], y=_top_langs["Played"],
        marker_color="#2ecc71",
        hovertemplate="<b>%{x}</b><br>%{y:,} played movies<extra></extra>",
    ))
    _fig1.add_trace(go.Bar(
        name="Unplayed", x=_top_langs["Language"], y=_top_langs["Unplayed"],
        marker_color="#e74c3c",
        hovertemplate="<b>%{x}</b><br>%{y:,} unplayed movies<extra></extra>",
    ))
    _fig1.update_layout(
        barmode="stack",
        title="Movies by Audio Language",
        height=450,
        template="plotly_dark",
    )

    _fig2 = go.Figure(go.Bar(
        x=_top_langs["Language"],
        y=_top_langs["% Unplayed"],
        marker_color=["#e74c3c" if v > 50 else "#f39c12" if v > 30 else "#2ecc71" for v in _top_langs["% Unplayed"]],
        text=[f"{v}%" for v in _top_langs["% Unplayed"]],
        textposition="outside",
        hovertemplate="<b>%{x}</b><br>%{y:.1f}% unplayed<extra></extra>",
    ))
    _fig2.update_layout(
        title="Unplayed Rate by Language",
        yaxis_title="% Unplayed", yaxis_range=[0, 100],
        height=400,
        template="plotly_dark",
    )

    language_tab = mo.vstack([
        mo.md("## Content by Audio Language"),
        _fig1,
        _fig2,
        mo.ui.table(_df_lang.to_dict("records"), pagination=True, page_size=20, label="Language Breakdown"),
    ])
    return (language_tab,)


@app.cell
def tab_forgotten(df_movies, df_series, go, mo):
    _forgotten_movies = df_movies[~df_movies["Played"]].sort_values("DaysInLibrary", ascending=False)
    _fm_display = _forgotten_movies[["Name", "Year", "PrimaryGenre", "DateAdded", "DaysInLibrary", "SizeGB"]].copy()
    _fm_display.columns = ["Name", "Year", "Genre", "Date Added", "Days in Library", "Size (GB)"]

    _fig_hist = go.Figure(go.Histogram(
        x=_forgotten_movies["DaysInLibrary"],
        nbinsx=30,
        marker_color="#8e44ad",
        hovertemplate="%{x} days in library<br>%{y:,} movies<extra></extra>",
    ))
    _fig_hist.update_layout(
        title="How Long Have Unplayed Movies Been in the Library?",
        xaxis_title="Days in Library",
        yaxis_title="Number of Movies",
        height=350,
        template="plotly_dark",
    )

    _avg_days = _forgotten_movies["DaysInLibrary"].mean()
    _max_days = _forgotten_movies["DaysInLibrary"].max()
    _over_1y = len(_forgotten_movies[_forgotten_movies["DaysInLibrary"] > 365])
    _over_2y = len(_forgotten_movies[_forgotten_movies["DaysInLibrary"] > 730])

    _forgotten_series = df_series[~df_series["Played"]].sort_values("DaysInLibrary", ascending=False)
    _fs_display = _forgotten_series[["Name", "Year", "PrimaryGenre", "DateAdded", "DaysInLibrary"]].copy()
    _fs_display.columns = ["Name", "Year", "Genre", "Date Added", "Days in Library"]

    forgotten_tab = mo.vstack([
        mo.md("## Added but Forgotten"),
        mo.hstack([
            mo.stat(value=f"{_avg_days:.0f}", label="Avg Days Unwatched"),
            mo.stat(value=f"{_max_days:,}", label="Oldest Unwatched (days)"),
            mo.stat(value=f"{_over_1y:,}", label="Unplayed >1 Year"),
            mo.stat(value=f"{_over_2y:,}", label="Unplayed >2 Years"),
        ], justify="space-around", gap="1rem"),
        _fig_hist,
        mo.md("### Oldest Unplayed Movies"),
        mo.ui.table(_fm_display.head(200).to_dict("records"), pagination=True, page_size=25, label="Forgotten Movies"),
        mo.md("### Oldest Unwatched Series"),
        mo.ui.table(_fs_display.head(200).to_dict("records"), pagination=True, page_size=25, label="Forgotten Series"),
    ])
    return (forgotten_tab,)


@app.cell
def tab_abandoned(df_series, go, mo):
    _abandoned = df_series[
        (df_series["PlayedEpisodes"] > 0) &
        (df_series["PctWatched"] < 50) &
        (df_series["TotalEpisodes"] >= 5)
    ].sort_values("PctWatched", ascending=True).copy()

    _ab_display = _abandoned[["Name", "Year", "TotalEpisodes", "PlayedEpisodes", "PctWatched", "Watchers"]].copy()
    _ab_display.columns = ["Name", "Year", "Total Eps", "Watched Eps", "% Watched", "Watchers"]

    _top_abandoned = _abandoned.head(25)
    _fig = go.Figure(go.Bar(
        y=_top_abandoned["Name"],
        x=_top_abandoned["PctWatched"],
        orientation="h",
        marker_color=[
            "#e74c3c" if v < 10 else "#e67e22" if v < 25 else "#f39c12"
            for v in _top_abandoned["PctWatched"]
        ],
        text=[f"{v:.0f}% ({w}/{t})" for v, w, t in zip(
            _top_abandoned["PctWatched"], _top_abandoned["PlayedEpisodes"], _top_abandoned["TotalEpisodes"]
        )],
        textposition="outside",
        customdata=list(zip(_top_abandoned["PlayedEpisodes"], _top_abandoned["TotalEpisodes"])),
        hovertemplate=(
            "<b>%{y}</b><br>%{x:.0f}% watched"
            "<br>%{customdata[0]:,} of %{customdata[1]:,} episodes<extra></extra>"
        ),
    ))
    _fig.update_layout(
        title="Most Abandoned Series (started but <50% watched)",
        xaxis_title="% Watched", xaxis_range=[0, 55],
        height=max(400, len(_top_abandoned) * 28),
        template="plotly_dark",
        yaxis={"autorange": "reversed"},
    )

    _total_abandoned = len(_abandoned)
    _total_started = len(df_series[df_series["PlayedEpisodes"] > 0])

    abandoned_tab = mo.vstack([
        mo.md("## Abandoned Series"),
        mo.callout(
            "Shows where someone started watching but gave up (<50% completed, min 5 episodes)",
            kind="warn",
        ),
        mo.hstack([
            mo.stat(value=f"{_total_abandoned:,}", label="Abandoned Shows"),
            mo.stat(value=f"{_total_started:,}", label="Total Started Shows"),
            mo.stat(
                value=f"{_total_abandoned/_total_started*100:.1f}%" if _total_started > 0 else "0%",
                label="Abandonment Rate",
            ),
        ], justify="start", gap="2rem"),
        _fig,
        mo.ui.table(_ab_display.to_dict("records"), pagination=True, page_size=25, label="Abandoned Series"),
    ])
    return (abandoned_tab,)


@app.cell
def dashboard(
    abandoned_tab, cleanup_tab, forgotten_tab, language_tab, library_tab, mo, overview_tab,
):
    tabs = mo.ui.tabs({
        "Overview": overview_tab,
        "By Library": library_tab,
        "Cleanup by Size": cleanup_tab,
        "By Language": language_tab,
        "Added but Forgotten": forgotten_tab,
        "Abandoned Series": abandoned_tab,
    })
    tabs
    return (tabs,)


if __name__ == "__main__":
    app.run()
