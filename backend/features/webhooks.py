# -*- coding: utf-8 -*-

"""
Webhooks: POST a JSON event to the configured URLs when something happens,
so that external apps (e.g. a request app) don't have to poll.

Events:
    `volume.added`: A volume was added to the library.
    `download.imported`: Downloaded issues were imported into the library.
    `download.failed`: A download failed.
    `test`: Sent from the settings to test the webhooks.

When a secret is set, every request has the header
`X-Bubblarr-Signature: sha256=<hex HMAC-SHA256 of the body with the secret>`.
"""

from __future__ import annotations

from hashlib import sha256
from hmac import new as hmac_new
from json import dumps
from threading import Thread
from time import time
from typing import TYPE_CHECKING, Any, Dict, List, Union

from requests import post

from backend.base.logging import LOGGER
from backend.internals.settings import Settings

if TYPE_CHECKING:
    from backend.base.definitions import Download

TIMEOUT = 10
VERSION = 1


def _sign(secret: str, body: bytes) -> str:
    return 'sha256=' + hmac_new(secret.encode(), body, sha256).hexdigest()


def _send(url: str, event: str, body: bytes, secret: str) -> Union[str, None]:
    """Send one event to one URL.

    Returns:
        Union[str, None]: The error, or `None` when it was delivered.
    """
    headers = {
        'Content-Type': 'application/json',
        'User-Agent': 'Bubblarr',
        'X-Bubblarr-Event': event
    }
    if secret:
        headers['X-Bubblarr-Signature'] = _sign(secret, body)

    try:
        response = post(url, data=body, headers=headers, timeout=TIMEOUT)
        if not response.ok:
            return f'HTTP {response.status_code}'
        return None

    except Exception as e:
        return type(e).__name__


def _build_body(event: str, data: Dict[str, Any]) -> bytes:
    return dumps({
        'event': event,
        'version': VERSION,
        'timestamp': round(time()),
        **data
    }).encode()


def send_event(event: str, data: Dict[str, Any]) -> None:
    """Send an event to all webhook URLs in the background. Never raises.

    Args:
        event (str): The name of the event.
        data (Dict[str, Any]): The data of the event, merged into the body.
    """
    try:
        sv = Settings().sv
        urls = [u for u in sv.webhook_urls if u]
        if not urls:
            return
        body = _build_body(event, data)
        secret = sv.webhook_secret
    except Exception:
        LOGGER.exception('Failed to prepare webhook event %s', event)
        return

    def run() -> None:
        for url in urls:
            error = _send(url, event, body, secret)
            if error:
                LOGGER.warning(
                    'Failed to send webhook event %s: %s', event, error
                )
            else:
                LOGGER.debug('Sent webhook event %s', event)

    Thread(target=run, name='Webhook', daemon=True).start()
    return


def test_webhooks(urls: List[str], secret: str) -> List[Dict[str, Any]]:
    """Send a test event to the given URLs and wait for the result.

    Returns:
        List[Dict[str, Any]]: For every URL, whether it succeeded.
    """
    body = _build_body('test', {'message': 'Webhook test from Bubblarr'})
    result = []
    for url in urls:
        error = _send(url, 'test', body, secret)
        result.append({'url': url, 'success': error is None, 'error': error})
    return result


# region Events
def _volume_data(volume_id: int) -> Dict[str, Any]:
    from backend.features.comic_requests import volume_status
    return volume_status(volume_id)


def _covered_issues(download: Download) -> List[Dict[str, Any]]:
    from backend.implementations.volumes import Volume

    covered = download.covered_issues
    result = []
    for issue in Volume(download.volume_id).get_issues():
        if download.issue_id is not None:
            if issue.id != download.issue_id:
                continue
        elif isinstance(covered, tuple):
            if not covered[0] <= issue.calculated_issue_number <= covered[1]:
                continue
        elif covered is not None:
            if issue.calculated_issue_number != covered:
                continue

        result.append({
            'id': issue.id,
            'comicvine_id': issue.comicvine_id,
            'issue_number': issue.issue_number,
            'title': issue.title,
            'downloaded': bool(issue.files)
        })
    return result


def _download_data(download: Download) -> Dict[str, Any]:
    return {
        'title': download.title,
        'source': download.source_name,
        'size': download.size
    }


def volume_added(volume_id: int) -> None:
    try:
        send_event('volume.added', {'volume': _volume_data(volume_id)})
    except Exception:
        LOGGER.exception('Failed to send webhook for added volume')
    return


def download_imported(download: Download) -> None:
    try:
        send_event('download.imported', {
            'volume': _volume_data(download.volume_id),
            'issues': _covered_issues(download),
            'download': _download_data(download)
        })
    except Exception:
        LOGGER.exception('Failed to send webhook for imported download')
    return


def download_failed(download: Download) -> None:
    try:
        send_event('download.failed', {
            'volume': _volume_data(download.volume_id),
            'issues': _covered_issues(download),
            'download': _download_data(download)
        })
    except Exception:
        LOGGER.exception('Failed to send webhook for failed download')
    return
