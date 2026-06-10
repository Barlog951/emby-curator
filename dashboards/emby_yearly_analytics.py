# /// script
# [tool.marimo.display]
# theme = "dark"
# ///

import marimo

__generated_with = "0.20.2"
app = marimo.App(width="full", app_title="Emby Yearly Analytics")


@app.cell
def imports():
    import re
    import sys
    from collections import defaultdict
    from datetime import datetime

    import marimo as mo
    import pandas as pd
    import plotly.graph_objects as go

    sys.path.insert(0, str(mo.notebook_dir()))
    import shared
    return datetime, defaultdict, go, mo, pd, re, shared


@app.cell
def cache_helpers(shared):
    def cache_load(name):
        return shared.cache_load(name, max_age_hours=4)

    cache_save = shared.cache_save

    def cache_clear():
        shared.cache_clear("yearly_analytics*.json")

    return cache_clear, cache_load, cache_save


@app.cell
def cache_control(mo):
    refresh_btn = mo.ui.button(label="Force Refresh", kind="warn")
    return (refresh_btn,)


@app.cell
def cache_on_refresh(cache_clear, refresh_btn):
    if refresh_btn.value:
        cache_clear()
    return ()


@app.cell
def config(mo, refresh_btn, shared):
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
    mo.hstack([mo.md("# Emby Yearly Analytics"), refresh_btn], justify="space-between")
    return EMBY_API_KEY, EMBY_HOST


@app.cell
def api_client(EMBY_API_KEY, EMBY_HOST, shared):
    _client, emby_get = shared.make_emby_client(EMBY_HOST, EMBY_API_KEY)
    return (emby_get,)


@app.cell
def fetch_users(EMBY_HOST, emby_get, mo):
    try:
        with mo.status.spinner("Connecting to Emby..."):
            _raw = emby_get("Users")
    except Exception as _e:
        mo.stop(True, mo.callout(f"Cannot reach Emby at {EMBY_HOST}: {_e}", kind="danger"))
    user_map = {u["Id"]: u["Name"] for u in _raw}
    return (user_map,)


@app.cell
def fetch_activity(EMBY_HOST, cache_load, cache_save, emby_get, mo):
    _cached, _status = cache_load("yearly_analytics_playback")
    if _cached:
        all_playback = _cached
        mo.callout(f"Loaded from cache ({_status}) — {len(all_playback):,} events", kind="info")
    else:
        all_playback = []
        _offset = 0
        try:
            _total = emby_get("System/ActivityLog/Entries", StartIndex=0, Limit=1)["TotalRecordCount"]
            with mo.status.spinner(f"Fetching {_total:,} activity log entries..."):
                while _offset < _total:
                    _items = emby_get("System/ActivityLog/Entries", StartIndex=_offset, Limit=2000).get("Items", [])
                    if not _items:
                        break
                    for _e in _items:
                        if _e.get("Type") in ("playback.start", "playback.stop"):
                            all_playback.append({
                                "type": _e["Type"],
                                "date": _e.get("Date", ""),
                                "user_id": str(_e.get("UserId", "")),
                                "item_id": str(_e.get("ItemId", "")),
                                "name": _e.get("Name", ""),
                            })
                    _offset += len(_items)
        except Exception as _exc:
            mo.stop(True, mo.callout(
                f"Failed to fetch activity log from Emby at {EMBY_HOST}: {_exc}", kind="danger",
            ))
        cache_save("yearly_analytics_playback", all_playback)
        mo.callout(f"Fetched {len(all_playback):,} playback events", kind="success")
    return (all_playback,)


@app.cell
def compute_sessions(all_playback, datetime, defaultdict, mo, pd, re, user_map):
    """Pair start/stop events into sessions with duration and content type."""
    _EP_RE = re.compile(r"S\d+,?\s*Ep?\d+", re.IGNORECASE)

    # Parse all events into sorted list per (user, item)
    _events = defaultdict(list)  # (user_id, item_id) -> [(dt, type, name)]
    for _e in all_playback:
        try:
            _dt = datetime.fromisoformat(_e["date"].replace("Z", "+00:00"))
        except (ValueError, KeyError):
            continue
        _key = (_e["user_id"], _e["item_id"])
        _events[_key].append((_dt, _e["type"], _e["name"]))

    # Pair starts with nearest following stop
    _sessions = []
    for (_uid, _iid), _evts in _events.items():
        _evts.sort(key=lambda x: x[0])
        _i = 0
        while _i < len(_evts):
            _dt, _typ, _name = _evts[_i]
            if _typ == "playback.start":
                # Find next stop for same user+item
                _dur_min = None
                for _j in range(_i + 1, len(_evts)):
                    if _evts[_j][1] == "playback.stop":
                        _dur_sec = (_evts[_j][0] - _dt).total_seconds()
                        # Skip nonsensical durations (negative or > 8 hours)
                        if 10 <= _dur_sec <= 8 * 3600:
                            _dur_min = round(_dur_sec / 60, 1)
                        _i = _j  # skip to after stop
                        break
                else:
                    pass  # no matching stop found

                _is_episode = bool(_EP_RE.search(_name))
                _user = user_map.get(_uid, "Unknown")
                _sessions.append({
                    "user": _user,
                    "year": _dt.year,
                    "month": _dt.month,
                    "hour": _dt.hour,
                    "weekday": _dt.strftime("%A"),
                    "date": _dt.strftime("%Y-%m-%d"),
                    "content_type": "Episode" if _is_episode else "Movie",
                    "duration_min": _dur_min,
                    "item_id": _iid,
                    "title": _name,
                })
            _i += 1

    df_sessions = pd.DataFrame(_sessions)
    # Sessions with valid duration (matched start+stop, 10s–8h)
    df_watched = df_sessions[df_sessions["duration_min"].notna()].copy()

    mo.callout(
        f"{len(df_sessions):,} total sessions, {len(df_watched):,} with measurable watch time "
        f"({len(df_sessions) - len(df_watched):,} unmatched/skipped)",
        kind="info",
    )
    return df_sessions, df_watched


@app.cell
def tab_overview(datetime, df_sessions, df_watched, mo, pd):
    _now = datetime.now()
    _period = f"Jan–{_now.strftime('%b')} {_now.day}"
    _years = sorted(df_sessions["year"].unique())

    _rows = []
    for _y in _years:
        _ys = df_sessions[df_sessions["year"] == _y]
        _yw = df_watched[df_watched["year"] == _y]
        # Same-period filter for fair comparison
        _ys_ytd = _ys[_ys["date"] <= f"{_y}-{_now.month:02d}-{_now.day:02d}"]
        _yw_ytd = _yw[_yw["date"] <= f"{_y}-{_now.month:02d}-{_now.day:02d}"]

        _total_hrs = round(_yw["duration_min"].sum() / 60, 1)
        _ytd_hrs = round(_yw_ytd["duration_min"].sum() / 60, 1)
        _n_sessions = len(_ys)
        _n_ytd = len(_ys_ytd)
        _unique_titles = _ys["item_id"].nunique()
        _unique_ytd = _ys_ytd["item_id"].nunique()
        _users = _ys["user"].nunique()
        _users_ytd = _ys_ytd["user"].nunique()
        _avg_session = round(_yw["duration_min"].mean(), 1) if len(_yw) > 0 else 0
        _movies_ytd = len(_ys_ytd[_ys_ytd["content_type"] == "Movie"])
        _episodes_ytd = len(_ys_ytd[_ys_ytd["content_type"] == "Episode"])

        _rows.append({
            "Year": _y,
            f"Sessions ({_period})": f"{_n_ytd:,}",
            f"Hours ({_period})": f"{_ytd_hrs:,}",
            "Full Year Sessions": f"{_n_sessions:,}" if _y < _now.year else "—",
            "Full Year Hours": f"{_total_hrs:,}" if _y < _now.year else "—",
            f"Unique Titles ({_period})": _unique_ytd,
            f"Active Users ({_period})": _users_ytd,
            "Avg Session (min)": _avg_session,
            f"Movies ({_period})": f"{_movies_ytd:,}",
            f"Episodes ({_period})": f"{_episodes_ytd:,}",
        })

    _df = pd.DataFrame(_rows)

    # Headline stats: 2026 vs 2025
    _c = _rows[-1] if _rows else {}
    _p = _rows[-2] if len(_rows) > 1 else {}
    _c_hrs = float(_c.get(f"Hours ({_period})", "0").replace(",", ""))
    _p_hrs = float(_p.get(f"Hours ({_period})", "0").replace(",", ""))
    _hr_growth = round((_c_hrs - _p_hrs) / _p_hrs * 100, 1) if _p_hrs else 0

    overview_tab = mo.vstack([
        mo.md(f"**Comparing same period: {_period} across all years**"),
        mo.hstack([
            mo.stat(value=f"{_c_hrs:,.0f}h", label=f"{_now.year} Watch Time"),
            mo.stat(value=f"{_p_hrs:,.0f}h", label=f"{_now.year-1} Watch Time"),
            mo.stat(value=f"{'+' if _hr_growth > 0 else ''}{_hr_growth}%", label="Hours YoY"),
            mo.stat(value=_c.get(f"Sessions ({_period})", "0"), label=f"{_now.year} Sessions"),
        ]),
        mo.ui.table(_df, pagination=True, page_size=25, label="Year-over-Year Comparison"),
    ])
    return (overview_tab,)


@app.cell
def tab_monthly(df_watched, go, mo):
    _months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    # Hours watched per month per year
    _monthly = df_watched.groupby(["year", "month"])["duration_min"].sum().reset_index()
    _monthly["hours"] = (_monthly["duration_min"] / 60).round(1)
    _pivot_hrs = _monthly.pivot(index="month", columns="year", values="hours").fillna(0).round(1)
    _pivot_hrs.index = [_months[m - 1] for m in _pivot_hrs.index]
    _pivot_hrs.index.name = "Month"
    _pivot_hrs = _pivot_hrs.reset_index().rename(columns=str)

    # Session count per month per year
    _monthly_cnt = df_watched.groupby(["year", "month"]).size().reset_index(name="sessions")
    _pivot_cnt = _monthly_cnt.pivot(index="month", columns="year", values="sessions").fillna(0).astype(int)
    _pivot_cnt.index = [_months[m - 1] for m in _pivot_cnt.index]
    _pivot_cnt.index.name = "Month"
    _pivot_cnt = _pivot_cnt.reset_index().rename(columns=str)

    # Year-over-year line chart of watch hours per month
    _fig_monthly = go.Figure()
    for _y in [c for c in _pivot_hrs.columns if c != "Month"]:
        _fig_monthly.add_trace(go.Scatter(
            x=_pivot_hrs["Month"], y=_pivot_hrs[_y],
            mode="lines+markers", name=str(_y),
            hovertemplate="<b>%{x} " + str(_y) + "</b><br>%{y:,.1f} hours watched<extra></extra>",
        ))
    _fig_monthly.update_layout(
        title="Watch Hours per Month — Year over Year",
        xaxis_title="Month", yaxis_title="Hours Watched",
        height=400, template="plotly_dark",
        legend_title_text="Year",
        margin={"t": 60, "b": 40},
    )

    monthly_tab = mo.vstack([
        _fig_monthly,
        mo.md("### Hours Watched per Month"),
        mo.ui.table(_pivot_hrs, pagination=True, page_size=25),
        mo.md("### Session Count per Month"),
        mo.ui.table(_pivot_cnt, pagination=True, page_size=25),
    ])
    return (monthly_tab,)


@app.cell
def tab_users(datetime, df_sessions, df_watched, go, mo, pd):
    _now = datetime.now()
    _period = f"Jan–{_now.strftime('%b')} {_now.day}"
    _cutoff_curr = f"{_now.year}-{_now.month:02d}-{_now.day:02d}"
    _cutoff_prev = f"{_now.year-1}-{_now.month:02d}-{_now.day:02d}"

    # Current year YTD
    _cw = df_watched[(df_watched["year"] == _now.year) & (df_watched["date"] <= _cutoff_curr)]
    _cs = df_sessions[(df_sessions["year"] == _now.year) & (df_sessions["date"] <= _cutoff_curr)]
    # Previous year same period
    _pw = df_watched[(df_watched["year"] == _now.year - 1) & (df_watched["date"] <= _cutoff_prev)]
    _ps = df_sessions[(df_sessions["year"] == _now.year - 1) & (df_sessions["date"] <= _cutoff_prev)]

    _c_hrs = _cw.groupby("user")["duration_min"].sum() / 60
    _c_sessions = _cs.groupby("user").size()
    _c_titles = _cs.groupby("user")["item_id"].nunique()
    _p_hrs = _pw.groupby("user")["duration_min"].sum() / 60
    _p_sessions = _ps.groupby("user").size()

    _all_users = set(_c_hrs.index) | set(_p_hrs.index)
    _rows = []
    for _u in _all_users:
        _ch = round(_c_hrs.get(_u, 0), 1)
        _ph = round(_p_hrs.get(_u, 0), 1)
        _diff = round(_ch - _ph, 1)
        _rows.append({
            "User": _u,
            f"{_now.year} Hours": _ch,
            f"{_now.year} Sessions": int(_c_sessions.get(_u, 0)),
            f"{_now.year} Titles": int(_c_titles.get(_u, 0)),
            f"{_now.year-1} Hours": _ph,
            f"{_now.year-1} Sessions": int(_p_sessions.get(_u, 0)),
            "Hours Diff": f"{'+' if _diff > 0 else ''}{_diff}",
        })
    _df = pd.DataFrame(_rows).sort_values(f"{_now.year} Hours", ascending=False)

    # Top users by watch hours — current vs previous year
    _top_users = _df.head(15)
    _fig_users = go.Figure()
    _fig_users.add_trace(go.Bar(
        y=_top_users["User"], x=_top_users[f"{_now.year} Hours"],
        name=str(_now.year), orientation="h", marker_color="#3498db",
        hovertemplate="<b>%{y}</b><br>" + str(_now.year) + ": %{x:,.1f} hours<extra></extra>",
    ))
    _fig_users.add_trace(go.Bar(
        y=_top_users["User"], x=_top_users[f"{_now.year-1} Hours"],
        name=str(_now.year - 1), orientation="h", marker_color="#95a5a6",
        hovertemplate="<b>%{y}</b><br>" + str(_now.year - 1) + ": %{x:,.1f} hours<extra></extra>",
    ))
    _fig_users.update_layout(
        barmode="group",
        title=f"Top Users by Watch Hours ({_period})",
        xaxis_title="Hours Watched", yaxis_title="",
        height=max(400, len(_top_users) * 34),
        template="plotly_dark",
        yaxis={"autorange": "reversed"},
        legend_title_text="Year",
    )

    users_tab = mo.vstack([
        mo.md(f"### User Watch Time — {_now.year} vs {_now.year-1} ({_period})"),
        _fig_users,
        mo.ui.table(_df, pagination=True, page_size=25),
    ])
    return (users_tab,)


@app.cell
def tab_content(datetime, df_sessions, df_watched, mo, pd):
    _now = datetime.now()
    _period = f"Jan–{_now.strftime('%b')} {_now.day}"
    _years = sorted(df_sessions["year"].unique())

    # Content type split per year (same period)
    _rows = []
    for _y in _years:
        _ys = df_sessions[(df_sessions["year"] == _y) &
                          (df_sessions["date"] <= f"{_y}-{_now.month:02d}-{_now.day:02d}")]
        _yw = df_watched[(df_watched["year"] == _y) &
                         (df_watched["date"] <= f"{_y}-{_now.month:02d}-{_now.day:02d}")]
        _mov_s = len(_ys[_ys["content_type"] == "Movie"])
        _ep_s = len(_ys[_ys["content_type"] == "Episode"])
        _mov_h = round(_yw[_yw["content_type"] == "Movie"]["duration_min"].sum() / 60, 1)
        _ep_h = round(_yw[_yw["content_type"] == "Episode"]["duration_min"].sum() / 60, 1)
        _total_h = _mov_h + _ep_h
        _mov_pct = round(_mov_h / _total_h * 100) if _total_h else 0
        _rows.append({
            "Year": _y,
            "Movie Sessions": f"{_mov_s:,}",
            "Episode Sessions": f"{_ep_s:,}",
            "Movie Hours": _mov_h,
            "Episode Hours": _ep_h,
            "Total Hours": _total_h,
            "Movie %": f"{_mov_pct}%",
        })

    _df = pd.DataFrame(_rows)

    content_tab = mo.vstack([
        mo.md(f"### Movies vs Episodes — Same Period ({_period})"),
        mo.ui.table(_df, pagination=True, page_size=25),
    ])
    return (content_tab,)


@app.cell
def tab_peak(datetime, df_watched, mo, pd):
    _now = datetime.now()

    # Top 20 biggest watch days in 2026
    _curr = df_watched[df_watched["year"] == _now.year]
    _daily = _curr.groupby("date").agg(
        sessions=("duration_min", "count"),
        hours=("duration_min", "sum"),
        users=("user", "nunique"),
        titles=("item_id", "nunique"),
    ).reset_index()
    _daily["hours"] = (_daily["hours"] / 60).round(1)
    _daily = _daily.sort_values("hours", ascending=False).head(20)
    _daily = _daily.rename(columns={"date": "Date", "sessions": "Sessions", "hours": "Hours", "users": "Users", "titles": "Titles"})

    # Avg by weekday (2026)
    _dow_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    _curr_dow = df_watched[df_watched["year"] == _now.year].copy()
    _dow_agg = _curr_dow.groupby("weekday").agg(
        total_hours=("duration_min", "sum"),
        sessions=("duration_min", "count"),
    ).reindex(_dow_order)
    # Count number of each weekday in the year so far
    _start = datetime(_now.year, 1, 1)
    _dow_counts = {}
    for _d in range((_now - _start).days + 1):
        _wd = (_start + pd.Timedelta(days=_d)).strftime("%A")
        _dow_counts[_wd] = _dow_counts.get(_wd, 0) + 1

    _dow_rows = []
    for _d in _dow_order:
        _th = _dow_agg.loc[_d, "total_hours"] / 60 if _d in _dow_agg.index else 0
        _ns = int(_dow_agg.loc[_d, "sessions"]) if _d in _dow_agg.index else 0
        _nd = _dow_counts.get(_d, 1)
        _dow_rows.append({
            "Day": _d,
            "Total Hours": round(_th, 1),
            "Avg Hours/day": round(_th / _nd, 1),
            "Sessions": _ns,
            "Avg Sessions/day": round(_ns / _nd, 1),
        })
    _df_dow = pd.DataFrame(_dow_rows)

    peak_tab = mo.vstack([
        mo.md(f"### Top 20 Watch Days in {_now.year}"),
        mo.ui.table(_daily, pagination=True, page_size=25),
        mo.md(f"### Average by Day of Week ({_now.year})"),
        mo.ui.table(_df_dow, pagination=True, page_size=25),
    ])
    return (peak_tab,)


@app.cell
def tab_hourly(datetime, df_watched, go, mo, pd):
    _now = datetime.now()
    _years = sorted(df_watched["year"].unique())
    _period = f"Jan–{_now.strftime('%b')} {_now.day}"

    # Hours watched per hour-of-day, same period, per year
    _rows = []
    for _h in range(24):
        _row = {"Hour": f"{_h:02d}:00"}
        for _y in _years:
            _yw = df_watched[(df_watched["year"] == _y) &
                             (df_watched["date"] <= f"{_y}-{_now.month:02d}-{_now.day:02d}") &
                             (df_watched["hour"] == _h)]
            _row[str(_y)] = round(_yw["duration_min"].sum() / 60, 1)
        _rows.append(_row)

    _df = pd.DataFrame(_rows)

    # Hourly distribution — grouped bars per year
    _fig_hourly = go.Figure()
    for _y in _years:
        _fig_hourly.add_trace(go.Bar(
            x=_df["Hour"], y=_df[str(_y)], name=str(_y),
            hovertemplate="<b>%{x}</b> in " + str(_y) + "<br>%{y:,.1f} hours watched<extra></extra>",
        ))
    _fig_hourly.update_layout(
        barmode="group",
        title=f"Watch Hours by Time of Day ({_period}, all years)",
        xaxis_title="Hour of Day", yaxis_title="Hours Watched",
        height=400, template="plotly_dark",
        legend_title_text="Year",
    )

    hourly_tab = mo.vstack([
        mo.md(f"### Watch Hours by Time of Day (same period, {_period})"),
        _fig_hourly,
        mo.ui.table(_df, pagination=True, page_size=25),
    ])
    return (hourly_tab,)


@app.cell
def dashboard(mo, overview_tab, monthly_tab, users_tab, content_tab, peak_tab, hourly_tab):
    _tabs = mo.ui.tabs({
        "Overview": overview_tab,
        "Monthly": monthly_tab,
        "Users": users_tab,
        "Movies vs Episodes": content_tab,
        "Peak Days & Weekdays": peak_tab,
        "By Hour": hourly_tab,
    })
    mo.output.replace(_tabs)
    return ()


if __name__ == "__main__":
    app.run()
