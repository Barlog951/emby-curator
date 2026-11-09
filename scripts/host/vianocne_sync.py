"""Keep the Emby genre "Vianočné" on the films of the emby-advisor Christmas list.

usage: vianocne_sync.py [--doit] [--remove] [--force]

Reads ``genre_ids`` from the advisor's season.json (root-only file, read with ``sudo -n cat``).
Every listed movie/series without the genre gets it, with Genres locked (the ``genres origin``
default). With ``--remove``, the genre is taken off ids this job tagged that have left the list;
a hand-added "Vianočné" is never touched. Removals are skipped when the list is stale or one run
would drop more than ``MAX_DROP`` of the tagged ids. Adds are refused when one run would add more than
``max(MIN_ADD_CAP, MAX_ADD_SHARE x current holders)`` (a broken list), unless ``--force`` is given.

State (ids this job tagged): ~/.emby-dedupe/vianocne-sync.json. Needs the cli.env variables.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
import unicodedata
from collections import Counter

import httpx

from emby_dedupe.api.genres import fetch_full_item, update_item_genres
from emby_dedupe.utils.exceptions import EmbyServerConnectionError

GENRE = unicodedata.normalize("NFC", "Vianočné")
SEASON = "/var/lib/docker/volumes/emby-advisor_emby_advisor_data/_data/emby_rows/season.json"
STATE = os.path.expanduser("~/.emby-dedupe/vianocne-sync.json")
TYPES = ("Movie", "Series")
MAX_DROP = 0.10
MIN_ADD_CAP = 50
MAX_ADD_SHARE = 0.25
MAX_AGE_DAYS = 8  # weekly rebuild
MAX_AGE_DAYS_SEASON = 2  # nightly rebuild Dec 1 - Jan 6


def has_genre(genres: list[str]) -> bool:
    return any(unicodedata.normalize("NFC", g) == GENRE for g in genres)


def load_list() -> dict:
    r = subprocess.run(["sudo", "-n", "cat", SEASON], capture_output=True, text=True, timeout=30)
    if r.returncode:
        sys.exit(f"cannot read the advisor list: {r.stderr.strip()[:200]}")
    data = json.loads(r.stdout)
    ids = data.get("genre_ids")
    if not isinstance(ids, list) or not ids:
        sys.exit("genre_ids is missing or empty: nothing done")
    return data


def list_age_problem(built_at: str | None, today: datetime.date) -> str | None:
    try:
        built = datetime.date.fromisoformat(str(built_at))
    except ValueError:
        return f"built_at {built_at!r} unreadable"
    in_season = (today.month == 12 and today.day >= 3) or (today.month == 1 and today.day <= 7)
    limit = MAX_AGE_DAYS_SEASON if in_season else MAX_AGE_DAYS
    age = (today - built).days
    return f"list is {age} days old (limit {limit})" if age > limit else None


def load_state() -> set[str] | None:
    """Ids this job tagged; None when the state file is missing or unreadable (it is moved aside)."""
    try:
        with open(STATE) as fh:
            return set(json.load(fh)["ids"])
    except FileNotFoundError:
        return None
    except (ValueError, KeyError, TypeError) as e:
        aside = f"{STATE}.corrupt-{datetime.datetime.now():%Y%m%d-%H%M%S}"
        os.replace(STATE, aside)
        print(f"state file unreadable ({e}); moved to {aside}")
        return None


def add_cap(holders: int) -> int:
    """Most ids one run may tag without --force."""
    return max(MIN_ADD_CAP, int(MAX_ADD_SHARE * holders))


def save_state(tagged: set[str]) -> None:
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = f"{STATE}.tmp"
    with open(tmp, "w") as fh:
        json.dump({"genre": GENRE, "ids": sorted(tagged), "updated": datetime.datetime.now().isoformat(timespec="seconds")}, fh)
    os.replace(tmp, STATE)


def fetch(client: httpx.Client, base: str, uid: str, iid: str) -> dict | None:
    """Full item, or None when Emby no longer has it."""
    try:
        return fetch_full_item(client, base, uid, iid)
    except EmbyServerConnectionError as e:
        if "HTTP 404" in str(e) or "HTTP 400" in str(e):
            return None
        raise


def label(item: dict) -> str:
    return f"{item.get('Type')} {item.get('Id')} {item.get('Name')} ({item.get('ProductionYear')})"


def scan_list(client: httpx.Client, base: str, uid: str, listed: list[str],
              counts: Counter[str]) -> tuple[list[dict], list[dict]]:
    """Read every listed id: (items that need the genre, items that already have it)."""
    to_add: list[dict] = []
    holders: list[dict] = []
    for iid in listed:
        try:
            item = fetch(client, base, uid, iid)
        except EmbyServerConnectionError as e:
            counts["errors"] += 1
            print(f"error {iid}: {e}")
            continue
        if item is None:
            counts["missing"] += 1
            print(f"missing {iid} (not in Emby)")
            continue
        if item.get("Type") not in TYPES:
            counts["wrong_type"] += 1
            print(f"skipped {label(item)}: not a movie or series")
            continue
        if has_genre(item.get("Genres") or []):
            counts["already"] += 1
            holders.append(item)
        else:
            to_add.append(item)
    return to_add, holders


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--doit", action="store_true", help="write to Emby (default: dry run)")
    ap.add_argument("--remove", action="store_true", help="also remove the genre from ids that left the list")
    ap.add_argument("--force", action="store_true", help="add even when the list grew past the add cap")
    args = ap.parse_args()

    data = load_list()
    listed = [str(i) for i in data["genre_ids"]]
    host = os.environ["DEDUPE_EMBY_HOST"]
    base = host if host.startswith("http") else f"http://{host}"
    client = httpx.Client(headers={"X-Emby-Token": os.environ["DEDUPE_EMBY_API_KEY"]}, timeout=60)
    uid = next(u["Id"] for u in client.get(f"{base}/Users").raise_for_status().json() if u["Policy"].get("IsAdministrator"))

    state = load_state()
    counts: Counter[str] = Counter()
    to_add, holders = scan_list(client, base, uid, listed, counts)
    if state is None:
        # No usable record: adopt the listed titles that already carry the genre, so ids that
        # later leave the list can still be removed.
        state = {item["Id"] for item in holders}
        print(f"state rebuilt from {len(state)} listed holder(s)")
        if args.doit:
            save_state(state)
    tagged = state
    verb = "added" if args.doit else "would add"
    cap = add_cap(len(holders))
    if len(to_add) > cap and not args.force:
        counts["add refused"] = len(to_add)
        print(f"{len(to_add)} id(s) would get the genre, more than the cap of {cap} "
              f"({len(holders)} current holders): nothing added; check the list, or re-run with --force")
        to_add = []
    for item in to_add:
        iid = item["Id"]
        genres = item.get("Genres") or []
        if args.doit and not update_item_genres(client, base, iid, item, genres + [GENRE], lock=True):
            counts["errors"] += 1
            continue
        counts[verb] += 1
        print(f"{verb}: {label(item)}")
        if args.doit:
            tagged.add(iid)
            save_state(tagged)

    gone = sorted(tagged - set(listed))
    to_remove: list[dict] = []
    for iid in gone:
        try:
            item = fetch(client, base, uid, iid)
        except EmbyServerConnectionError as e:
            counts["errors"] += 1
            print(f"error {iid}: {e}")
            continue
        if item is None:
            tagged.discard(iid)  # deleted from Emby: nothing to remove
            counts["forgotten (deleted)"] += 1
        elif has_genre(item.get("Genres") or []):
            to_remove.append(item)
        else:
            tagged.discard(iid)  # genre already gone
    if args.doit:
        save_state(tagged)

    if to_remove:
        blocked = []
        if not args.remove:
            blocked.append("--remove not given")
        age = list_age_problem(data.get("built_at"), datetime.date.today())
        if age:
            blocked.append(age)
        if len(to_remove) > MAX_DROP * max(len(tagged), 1):
            blocked.append(f"{len(to_remove)} of {len(tagged)} tagged ids would lose it (limit {MAX_DROP:.0%})")
        if blocked:
            counts["removal skipped"] = len(to_remove)
            print(f"{len(to_remove)} tagged id(s) left the list; removal skipped: {'; '.join(blocked)}")
            for item in to_remove:
                print(f"  left: {label(item)}")
        else:
            rverb = "removed" if args.doit else "would remove"
            for item in to_remove:
                keep = [g for g in item.get("Genres") or [] if unicodedata.normalize("NFC", g) != GENRE]
                if args.doit:
                    if not update_item_genres(client, base, item["Id"], item, keep, lock=True):
                        counts["errors"] += 1
                        continue
                    tagged.discard(item["Id"])
                    save_state(tagged)
                counts[rverb] += 1
                print(f"{rverb}: {label(item)}")

    print(f"{datetime.datetime.now():%Y-%m-%d %H:%M} '{GENRE}': list {len(listed)} (built {data.get('built_at')}), "
          f"tagged by this job {len(tagged)} | " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    if counts["errors"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
