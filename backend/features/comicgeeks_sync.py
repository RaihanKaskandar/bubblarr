# -*- coding: utf-8 -*-

"""
Syncing with League of Comic Geeks (LoCG): matching volumes and issues to
LoCG, marking downloaded issues as collected and getting the pull list.
"""

from datetime import date, timedelta
from threading import Lock, Thread
from time import time
from typing import Any, Callable, Dict, List, Tuple, Union

from backend.base.custom_exceptions import (ClientNotWorking,
                                            CredentialInvalid,
                                            KapowarrException)
from backend.base.logging import LOGGER
from backend.implementations.comicgeeks import (ComicGeeks,
                                                normalise_issue_number,
                                                normalise_title, pick_series)
from backend.internals.db import commit, get_db
from backend.internals.settings import Settings

RETRY_UNMATCHED_AFTER = 7 * 24 * 60 * 60
"Seconds before searching again for a volume that wasn't found on LoCG"

PULL_LIST_CACHE_TIME = 60 * 60
_pull_list_cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
_sync_lock = Lock()


def is_configured() -> bool:
    sv = Settings().sv
    return bool(sv.comicgeeks_username and sv.comicgeeks_password)


# region Matching
def match_volume(volume_id: int, force: bool = False) -> Union[int, None]:
    """Find the LoCG series of a volume and map its issues.

    Args:
        volume_id (int): The ID of the volume.
        force (bool, optional): Search again, even if a match (or no match)
            was found before.
            Defaults to False.

    Returns:
        Union[int, None]: The ID of the LoCG series, or None if not found.
    """
    cursor = get_db()
    existing = cursor.execute(
        "SELECT series_id, matched_at FROM comicgeeks_volumes WHERE volume_id = ?;",
        (volume_id,)
    ).fetchone()
    if existing and not force:
        if existing["series_id"] is not None:
            return existing["series_id"]
        if time() - existing["matched_at"] < RETRY_UNMATCHED_AFTER:
            return None

    volume = cursor.execute(
        "SELECT title, alt_title, year, publisher FROM volumes WHERE id = ?;",
        (volume_id,)
    ).fetchone()
    if not volume:
        return None

    series = None
    for title in (volume["title"], volume["alt_title"]):
        if not title:
            continue
        series = pick_series(
            ComicGeeks.search_series(title),
            title, volume["year"], volume["publisher"]
        )
        if series:
            break

    series_id = series["id"] if series else None
    cursor.execute(
        """
        INSERT INTO comicgeeks_volumes(volume_id, series_id, matched_at)
        VALUES (?, ?, ?)
        ON CONFLICT(volume_id) DO UPDATE SET
            series_id = excluded.series_id,
            matched_at = excluded.matched_at;
        """,
        (volume_id, series_id, round(time()))
    )

    if series_id is None:
        LOGGER.info(
            "Couldn't find volume %s (%s) on League of Comic Geeks",
            volume["title"], volume["year"]
        )
        return None

    LOGGER.info(
        "Matched volume %s (%s) to League of Comic Geeks series %d",
        volume["title"], volume["year"], series_id
    )
    map_issues(volume_id, series_id)
    return series_id


def map_issues(volume_id: int, series_id: int) -> int:
    """Map the issues of a volume to the issues of a LoCG series, based on
    the issue number.

    Args:
        volume_id (int): The ID of the volume.
        series_id (int): The ID of the LoCG series.

    Returns:
        int: The amount of issues that were mapped.
    """
    locg_issues = {
        normalise_issue_number(i["issue_number"]): i["comic_id"]
        for i in ComicGeeks.get_series_issues(series_id)
        if i["issue_number"]
    }

    cursor = get_db()
    mapped = 0
    for issue in cursor.execute(
        "SELECT id, issue_number FROM issues WHERE volume_id = ?;",
        (volume_id,)
    ).fetchall():
        comic_id = locg_issues.get(normalise_issue_number(issue["issue_number"]))
        if comic_id is None:
            continue
        cursor.execute(
            """
            INSERT INTO comicgeeks_issues(issue_id, comic_id)
            VALUES (?, ?)
            ON CONFLICT(issue_id) DO UPDATE SET
                comic_id = excluded.comic_id,
                collected = CASE
                    WHEN comicgeeks_issues.comic_id = excluded.comic_id
                    THEN comicgeeks_issues.collected
                    ELSE 0
                END;
            """,
            (issue["id"], comic_id)
        )
        mapped += 1

    return mapped


# region Collection
def sync_volume_collection(volume_id: int) -> int:
    """Mark the downloaded issues of a volume as collected on LoCG.

    Args:
        volume_id (int): The ID of the volume.

    Returns:
        int: The amount of issues that were newly marked as collected.
    """
    series_id = match_volume(volume_id)
    if series_id is None:
        return 0

    cursor = get_db()
    unmapped = cursor.execute(
        """
        SELECT 1
        FROM issues i
        WHERE
            i.volume_id = ?
            AND EXISTS(SELECT 1 FROM issues_files f WHERE f.issue_id = i.id)
            AND NOT EXISTS(
                SELECT 1 FROM comicgeeks_issues ci WHERE ci.issue_id = i.id
            )
        LIMIT 1;
        """,
        (volume_id,)
    ).fetchone()
    if unmapped:
        # Issues that were added to the volume after it was matched
        map_issues(volume_id, series_id)

    to_collect = cursor.execute(
        """
        SELECT ci.issue_id, ci.comic_id
        FROM comicgeeks_issues ci
        INNER JOIN issues i
        ON ci.issue_id = i.id
        WHERE
            i.volume_id = ?
            AND ci.collected = 0
            AND EXISTS(
                SELECT 1 FROM issues_files f WHERE f.issue_id = i.id
            );
        """,
        (volume_id,)
    ).fetchall()

    marked = 0
    for issue_id, comic_id in to_collect:
        ComicGeeks.set_collected(comic_id, True)
        cursor.execute(
            "UPDATE comicgeeks_issues SET collected = 1 WHERE issue_id = ?;",
            (issue_id,)
        )
        commit()
        marked += 1

    if marked:
        LOGGER.info(
            "Marked %d issues of volume %d as collected on League of Comic Geeks",
            marked, volume_id
        )
    return marked


def sync_all_collections(
    progress: Union[Callable[[int, int], None], None] = None
) -> Dict[str, int]:
    """Mark all downloaded issues in the library as collected on LoCG.

    Args:
        progress (Union[Callable[[int, int], None], None], optional): Called
            with the current volume number and total amount of volumes.
            Defaults to None.

    Returns:
        Dict[str, int]: Statistics: `volumes`, `matched` and `marked`.
    """
    volume_ids = [
        r[0] for r in get_db().execute("""
            SELECT DISTINCT i.volume_id
            FROM issues i
            INNER JOIN issues_files f
            ON f.issue_id = i.id;
        """).fetchall()
    ]

    stats = {"volumes": len(volume_ids), "matched": 0, "marked": 0}
    with _sync_lock:
        for index, volume_id in enumerate(volume_ids):
            if progress:
                progress(index + 1, len(volume_ids))
            try:
                if match_volume(volume_id) is not None:
                    stats["matched"] += 1
                stats["marked"] += sync_volume_collection(volume_id)
            except CredentialInvalid:
                raise
            except KapowarrException:
                LOGGER.exception(
                    "Failed to sync volume %d to League of Comic Geeks: ",
                    volume_id
                )
            commit()

    LOGGER.info("League of Comic Geeks sync done: %s", stats)
    return stats


def sync_volume_collection_in_background(volume_id: int) -> None:
    """Mark the downloaded issues of a volume as collected on LoCG, in a
    separate thread so that it never slows down or breaks the download
    handling. Does nothing if the sync isn't enabled.

    Args:
        volume_id (int): The ID of the volume.
    """
    sv = Settings().sv
    if not (is_configured() and sv.comicgeeks_sync_collection):
        return

    def run() -> None:
        from backend.internals.server import Server

        with Server().app.app_context():
            try:
                with _sync_lock:
                    sync_volume_collection(volume_id)
                    commit()
            except (KapowarrException, Exception):
                LOGGER.exception(
                    "Failed to sync volume %d to League of Comic Geeks: ",
                    volume_id
                )

    Thread(target=run, name="ComicGeeksSync", daemon=True).start()
    return


# region Pull list
def get_pull_list(start: date, end: date) -> List[Dict[str, Any]]:
    """Get the pulled issues of the user between two dates (inclusive), linked
    to issues in the library where possible.

    Args:
        start (date): The first date.
        end (date): The last date.

    Returns:
        List[Dict[str, Any]]: The pulled issues, with the keys of
            `parse_issue_results()` plus `issue_id` and `volume_id` (None if
            not in the library).
    """
    if not (is_configured() and Settings().sv.comicgeeks_pull_list):
        return []

    # LoCG weeks start on Sunday
    week = start - timedelta(days=(start.weekday() + 1) % 7)
    pulled: Dict[int, Dict[str, Any]] = {}
    while week <= end:
        key = week.isoformat()
        cached = _pull_list_cache.get(key)
        if cached and time() - cached[0] < PULL_LIST_CACHE_TIME:
            items = cached[1]
        else:
            items = ComicGeeks.get_pull_list(week)
            _pull_list_cache[key] = (time(), items)

        for item in items:
            if item["date"] and start.isoformat() <= item["date"] <= end.isoformat():
                pulled[item["comic_id"]] = dict(item)
        week += timedelta(days=7)

    if not pulled:
        return []

    cursor = get_db()

    # Match library volumes of pulled series that weren't matched yet
    unmatched_titles = {
        normalise_title(item["series_title"])
        for item in pulled.values()
    }
    for volume_id, title in cursor.execute("""
        SELECT v.id, v.title
        FROM volumes v
        LEFT JOIN comicgeeks_volumes cv
        ON cv.volume_id = v.id
        WHERE cv.volume_id IS NULL;
    """).fetchall():
        if normalise_title(title) in unmatched_titles:
            try:
                match_volume(volume_id)
            except KapowarrException:
                LOGGER.exception(
                    "Failed to match volume %d on League of Comic Geeks: ",
                    volume_id
                )
    commit()

    placeholders = ",".join("?" * len(pulled))
    for issue_id, volume_id, comic_id in cursor.execute(
        f"""
        SELECT ci.issue_id, i.volume_id, ci.comic_id
        FROM comicgeeks_issues ci
        INNER JOIN issues i
        ON ci.issue_id = i.id
        WHERE ci.comic_id IN ({placeholders});
        """,
        tuple(pulled)
    ).fetchall():
        pulled[comic_id]["issue_id"] = issue_id
        pulled[comic_id]["volume_id"] = volume_id

    result = []
    for item in pulled.values():
        item.setdefault("issue_id", None)
        item.setdefault("volume_id", None)
        result.append(item)
    result.sort(key=lambda i: (i["date"] or "", i["title"]))
    return result


def clear_cache() -> None:
    "Clear the cached pull lists, e.g. after the credentials changed"
    _pull_list_cache.clear()
    return


def test_connection(username: str, password: str) -> Dict[str, Any]:
    """Test logging in to LoCG.

    Args:
        username (str): The username.
        password (str): The password.

    Returns:
        Dict[str, Any]: `success`, and `description` with the reason if it
            failed.
    """
    try:
        ComicGeeks.login(username, password)
        return {"success": True, "description": None}
    except CredentialInvalid:
        return {"success": False, "description": "invalid_credentials"}
    except ClientNotWorking as e:
        return {"success": False, "description": e.reason.value}
