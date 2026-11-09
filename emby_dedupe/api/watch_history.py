"""Keep a duplicate whose watch history would be lost by deleting it.

A watched copy's history is tied to its title through the provider ids: Emby carries a
movie's watched state to another copy with the same ids, and play statistics match copies
by id. When the kept copy shares no IMDb, TMDb or TVDB id with the copy to delete, that
history can no longer be tied to the kept copy. Such a delete is held until the two
copies carry the same id.
"""

from __future__ import annotations

import httpx

from emby_dedupe.utils.http import make_http_request
from emby_dedupe.utils.logging import logger
from emby_dedupe.utils.providers import shares_provider_id

WATCHED_COPY = "watched-copy"
UNREADABLE = "(watched state unreadable)"


def watched_copy_reason(item: dict, keeper: dict) -> str | None:
    """Why deleting ``item`` would lose watch history, or None.

    Needs ``item["played_by"]`` from :func:`annotate_watched_copies`; without it the
    delete is not held.
    """
    users = item.get("played_by")
    if not users or shares_provider_id(keeper.get("provider_ids"), item.get("provider_ids")):
        return None
    return (f"{WATCHED_COPY}: {', '.join(users)} watched the copy to delete and the kept copy shares no "
            "provider id with it, so the watched state would be lost; give both copies the same id first")


def _played_by(client: httpx.Client, base_url: str, users: list[dict], item_id: str) -> list[str]:
    """Names of the users who have the item marked played, read one user at a time.

    A bulk ``/Users/{id}/Items?Ids=`` query can report played items as unplayed, so each
    user's state is read per item.
    """
    names = []
    for user in users:
        data = make_http_request(client, "GET", f"{base_url}/Users/{user['Id']}/Items/{item_id}").json()
        if (data.get("UserData") or {}).get("Played"):
            names.append(user.get("Name") or user["Id"])
    return names


def annotate_watched_copies(client: httpx.Client, base_url: str, decisions: list) -> int:
    """Set ``played_by`` on every copy to delete whose kept copy shares no provider id with it.

    Pairs that share an id are skipped, so most runs make no extra requests. A copy whose
    watched state cannot be read is treated as watched, which holds the delete.

    Returns:
        The number of copies whose watched state was read.
    """
    targets = [
        item
        for decision in decisions
        for item in decision.get("delete", [])
        if not shares_provider_id((decision.get("keep") or {}).get("provider_ids"), item.get("provider_ids"))
    ]
    if not targets:
        return 0
    try:
        users = make_http_request(client, "GET", f"{base_url}/Users").json()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning(f"Could not list Emby users to check watched copies ({e}); holding {len(targets)} delete(s)")
        for item in targets:
            item["played_by"] = [UNREADABLE]
        return 0
    for item in targets:
        try:
            item["played_by"] = _played_by(client, base_url, users, item["id"])
        except (httpx.HTTPError, ValueError) as e:
            logger.warning(f"Could not read the watched state of id={item['id']} ({e}); holding its delete")
            item["played_by"] = [UNREADABLE]
    return len(targets)
