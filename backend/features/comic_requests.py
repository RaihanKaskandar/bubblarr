# -*- coding: utf-8 -*-

"""
Everything an external request app (like Seerr is for Sonarr/Radarr) needs:
looking up volumes with their status in the library, and requesting volumes
or specific issues, which adds/monitors them and starts a search.
"""

from __future__ import annotations

from asyncio import run
from datetime import date
from time import time
from typing import (TYPE_CHECKING, Any, Dict, Iterable, List,
                    Literal, Sequence, Tuple, Union)

from backend.base.custom_exceptions import (InvalidKeyValue,
                                            RootFolderNotFound)
from backend.base.definitions import MonitorScheme, VolumeMetadata
from backend.base.logging import LOGGER
from backend.implementations.comicvine import ComicVine
from backend.implementations.root_folders import RootFolders
from backend.implementations.volumes import Library, Volume
from backend.internals.db import commit, get_db

if TYPE_CHECKING:
    from backend.base.definitions import IssueData

IssueStatus = Literal[
    'downloaded', 'downloading', 'missing', 'unmonitored', 'upcoming'
]
VolumeStatus = Literal[
    'not_added', 'available', 'partially_available', 'downloading',
    'wanted', 'upcoming', 'unmonitored'
]
RequestedIssues = Union[Literal['all', 'missing', 'future'], List[int]]


# region Status
def _downloading_issues(volume_id: int) -> Dict[int, bool]:
    """Find which issues of a volume are in the download queue.

    Returns:
        Dict[int, bool]: Maps issue ID to True for downloading issues. Has the
            key `0` when a download covers the whole volume.
    """
    from backend.features.download_queue import DownloadHandler

    result: Dict[int, bool] = {}
    volume = Volume(volume_id)
    for download in DownloadHandler().queue:
        if download.volume_id != volume_id:
            continue

        if download.issue_id is not None:
            result[download.issue_id] = True
            continue

        covered = download.covered_issues
        if covered is None:
            result[0] = True
        elif isinstance(covered, tuple):
            for issue in volume.get_issues(_skip_files=True):
                if covered[0] <= issue.calculated_issue_number <= covered[1]:
                    result[issue.id] = True
        else:
            try:
                result[volume.get_issue_from_number(covered).id] = True
            except Exception:
                pass
    return result


def issue_status(
    issue: IssueData,
    volume_monitored: bool,
    downloading: Dict[int, bool],
    today: str
) -> IssueStatus:
    if issue.files:
        return 'downloaded'
    if issue.id in downloading or (0 in downloading and issue.monitored):
        return 'downloading'
    if not issue.date or issue.date > today:
        return 'upcoming'
    if issue.monitored and volume_monitored:
        return 'missing'
    return 'unmonitored'


def volume_status(
    volume_id: int,
    include_issues: bool = False
) -> Dict[str, Any]:
    """Get the availability of a volume in the library.

    Args:
        volume_id (int): The ID of the volume.
        include_issues (bool, optional): Include the status of each issue.
            Defaults to False.

    Returns:
        Dict[str, Any]: The status. See the API docs for the format.
    """
    volume = Volume(volume_id)
    data = volume.get_data()
    issues = volume.get_issues()
    downloading = _downloading_issues(volume_id)
    today = date.today().isoformat()

    counts: Dict[str, int] = {
        'total': len(issues),
        'downloaded': 0,
        'downloading': 0,
        'missing': 0,
        'upcoming': 0,
        'unmonitored': 0
    }
    issue_results = []
    for issue in issues:
        status = issue_status(issue, data.monitored, downloading, today)
        counts[status] += 1
        if include_issues:
            issue_results.append({
                'id': issue.id,
                'comicvine_id': issue.comicvine_id,
                'issue_number': issue.issue_number,
                'calculated_issue_number': issue.calculated_issue_number,
                'title': issue.title,
                'date': issue.date,
                'monitored': issue.monitored,
                'status': status
            })

    status: VolumeStatus
    if counts['downloading']:
        status = 'downloading'
    elif counts['missing']:
        status = (
            'partially_available'
            if counts['downloaded'] else
            'wanted'
        )
    elif counts['downloaded']:
        status = 'available'
    elif data.monitored and counts['upcoming']:
        status = 'upcoming'
    else:
        status = 'unmonitored'

    result: Dict[str, Any] = {
        'volume_id': volume_id,
        'comicvine_id': data.comicvine_id,
        'title': data.title,
        'year': data.year,
        'volume_number': data.volume_number,
        'publisher': data.publisher,
        'monitored': data.monitored,
        'monitor_new_issues': data.monitor_new_issues,
        'status': status,
        'issues': counts
    }
    if include_issues:
        result['issue_list'] = issue_results
    return result


def _volume_ids_for_cv_ids(cv_ids: Iterable[int]) -> Dict[int, int]:
    cv_ids = list(cv_ids)
    if not cv_ids:
        return {}
    return dict(get_db().execute(
        f"""
        SELECT comicvine_id, id
        FROM volumes
        WHERE comicvine_id IN ({','.join('?' * len(cv_ids))});
        """,
        cv_ids
    ).fetchall())


def status_by_comicvine_ids(
    cv_ids: Sequence[int]
) -> Dict[int, Dict[str, Any]]:
    """Get the library status of multiple volumes by their ComicVine ID.

    Returns:
        Dict[int, Dict[str, Any]]: Maps every given CV ID to its status. Volumes
            that are not in the library get `{"status": "not_added"}`.
    """
    volume_ids = _volume_ids_for_cv_ids(cv_ids)
    return {
        cv_id: (
            volume_status(volume_ids[cv_id])
            if cv_id in volume_ids else
            {'comicvine_id': cv_id, 'volume_id': None, 'status': 'not_added'}
        )
        for cv_id in cv_ids
    }


# region Lookup
def _public_metadata(volume: VolumeMetadata) -> Dict[str, Any]:
    result: Dict[str, Any] = dict(volume)
    result.pop('cover', None)
    result.pop('already_added', None)
    return result


def lookup_search(query: str) -> List[Dict[str, Any]]:
    """Search ComicVine for volumes, with their status in the library.

    Returns:
        List[Dict[str, Any]]: The search results, each with a `library` key
            holding the status of the volume in the library.
    """
    results = [
        _public_metadata(r)
        for r in run(ComicVine().search_volumes(query))
    ]
    statuses = status_by_comicvine_ids([r['comicvine_id'] for r in results])
    for r in results:
        r['library'] = statuses[r['comicvine_id']]
    return results


def lookup_volume(cv_id: int) -> Dict[str, Any]:
    """Get the metadata of a volume and its issues from ComicVine, with their
    status in the library.

    Raises:
        VolumeNotMatched: The ID doesn't map to any volume.

    Returns:
        Dict[str, Any]: The volume, with a `library` key and for every issue a
            `library` key (`None` when the volume is not in the library).
    """
    volume = _public_metadata(run(ComicVine().fetch_volume(cv_id)))
    volume_id = _volume_ids_for_cv_ids((cv_id,)).get(cv_id)

    if volume_id is None:
        volume['library'] = {
            'comicvine_id': cv_id, 'volume_id': None, 'status': 'not_added'
        }
        for issue in volume['issues'] or []:
            issue['library'] = None
        return volume

    status = volume_status(volume_id, include_issues=True)
    by_cv_id = {i['comicvine_id']: i for i in status.pop('issue_list')}
    volume['library'] = status
    for issue in volume['issues'] or []:
        library_issue = by_cv_id.get(issue['comicvine_id'])
        issue['library'] = library_issue and {
            'issue_id': library_issue['id'],
            'monitored': library_issue['monitored'],
            'status': library_issue['status']
        }
    return volume


_releases_cache: Dict[
    Tuple[str, str, int], Tuple[float, List[Dict[str, Any]]]
] = {}
RELEASES_CACHE_TIME = 6 * 60 * 60


def lookup_releases(
    start: str,
    end: str,
    limit: int = 500
) -> List[Dict[str, Any]]:
    """Get the comics with issues in stores between two dates, according to
    ComicVine, with their status in the library. Foreign editions are left
    out. Cached for 6 hours, to spare the ComicVine rate limit.

    Args:
        start (str): The first date, as `YYYY-MM-DD`.
        end (str): The last date, as `YYYY-MM-DD`.
        limit (int, optional): The maximum amount of issues to fetch from
            ComicVine (100 per call).
            Defaults to 500.

    Returns:
        List[Dict[str, Any]]: The volumes, the ones with most issues first.
    """
    key = (start, end, limit)
    cached = _releases_cache.get(key)
    if cached and time() - cached[0] < RELEASES_CACHE_TIME:
        volumes = cached[1]
    else:
        volumes = []
        for volume, issues in run(ComicVine().fetch_releases(start, end, limit)):
            if volume['translated']:
                continue
            volumes.append({
                'comicvine_id': volume['comicvine_id'],
                'title': volume['title'],
                'year': volume['year'],
                'volume_number': volume['volume_number'],
                'publisher': volume['publisher'],
                'cover_link': volume['cover_link'] or None,
                'issue_count': volume['issue_count'],
                'issues': sorted(issues, key=lambda i: i['date'] or ''),
            })
        volumes.sort(key=lambda v: (-len(v['issues']), v['title']))
        _releases_cache[key] = (time(), volumes)

    statuses = status_by_comicvine_ids([v['comicvine_id'] for v in volumes])
    return [
        {**v, 'library': statuses[v['comicvine_id']]}
        for v in volumes
    ]


# region Request
def _parse_requested_issues(issues: Any) -> RequestedIssues:
    if issues in ('all', 'missing', 'future'):
        return issues
    if (
        isinstance(issues, list)
        and issues
        and all(
            isinstance(i, int) and not isinstance(i, bool)
            for i in issues
        )
    ):
        return issues
    raise InvalidKeyValue('issues', issues)


def request_volume(
    comicvine_id: int,
    issues: Any = 'all',
    root_folder_id: Union[int, None] = None,
    monitor_new_issues: Union[bool, None] = None,
    search: bool = True
) -> Dict[str, Any]:
    """Request a volume: add it to the library if needed, monitor the
    requested issues and start a search for them.

    Args:
        comicvine_id (int): The CV ID of the volume.

        issues (Any, optional): Which issues to monitor. `"all"`, `"missing"`
            (the issues that are not downloaded yet), `"future"` (only new
            issues) or a list of CV IDs of issues.
            Defaults to 'all'.

        root_folder_id (Union[int, None], optional): The root folder to add the
            volume to. `None` to use the first root folder. Not used when the
            volume is already in the library.
            Defaults to None.

        monitor_new_issues (Union[bool, None], optional): Whether to monitor
            issues that are released later. `None` to keep the current value
            or, for a new volume, to monitor new issues unless specific
            issues are requested.
            Defaults to None.

        search (bool, optional): Start a search for the monitored issues that
            are missing.
            Defaults to True.

    Raises:
        InvalidKeyValue: A value is invalid.
        RootFolderNotFound: There is no (such) root folder.
        VolumeNotMatched: The CV ID doesn't map to any volume.

    Returns:
        Dict[str, Any]: The status of the volume after the request, with the
            extra keys `added` (bool) and `searching` (bool).
    """
    requested = _parse_requested_issues(issues)
    if monitor_new_issues is not None and not isinstance(
        monitor_new_issues, bool
    ):
        raise InvalidKeyValue('monitor_new_issues', monitor_new_issues)

    volume_id = _volume_ids_for_cv_ids((comicvine_id,)).get(comicvine_id)
    added = volume_id is None

    if added:
        if root_folder_id is None:
            root_folders = RootFolders().get_all()
            if not root_folders:
                raise RootFolderNotFound(0)
            root_folder_id = root_folders[0].id

        volume_id = Library.add(
            comicvine_id,
            root_folder_id,
            monitored=True,
            monitor_scheme=(
                MonitorScheme.ALL
                if requested in ('all', 'missing') else
                MonitorScheme.NONE
            ),
            monitor_new_issues=(
                monitor_new_issues
                if monitor_new_issues is not None else
                not isinstance(requested, list)
            ),
            auto_search=False
        )

    volume = Volume(volume_id)
    volume.update({'monitored': True})
    if monitor_new_issues is not None:
        volume.update({'monitor_new_issues': monitor_new_issues})
    elif requested == 'future':
        volume.update({'monitor_new_issues': True})

    if not (added and requested in ('all', 'missing', 'future')):
        library_issues = volume.get_issues()
        if requested == 'all':
            to_monitor = [i.id for i in library_issues]
        elif requested == 'missing':
            to_monitor = [i.id for i in library_issues if not i.files]
        elif requested == 'future':
            to_monitor = []
        else:
            by_cv_id = {i.comicvine_id: i.id for i in library_issues}
            unknown = [i for i in requested if i not in by_cv_id]
            if unknown:
                raise InvalidKeyValue('issues', unknown)
            to_monitor = [by_cv_id[i] for i in requested]

        if to_monitor:
            get_db().executemany(
                "UPDATE issues SET monitored = 1 WHERE id = ?;",
                [(i,) for i in to_monitor]
            )

    commit()

    LOGGER.info(
        'Request for volume %d (CV ID %d), issues: %s, added: %s',
        volume_id, comicvine_id, requested, added
    )

    searching = False
    if search and volume.get_open_issues():
        from backend.features.tasks import AutoSearchVolume, TaskHandler
        TaskHandler().add(AutoSearchVolume(volume_id))
        searching = True

    result = volume_status(volume_id)
    result['added'] = added
    result['searching'] = searching
    return result
