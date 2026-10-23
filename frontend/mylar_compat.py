# -*- coding: utf-8 -*-

"""
A minimal implementation of the Mylar API, for the endpoints that Prowlarr
uses to sync its indexers to Mylar. This makes it possible to add Bubblarr in
Prowlarr as a "Mylar" application, after which Prowlarr automatically adds,
updates and removes its indexers in Bubblarr (like it does for Sonarr and
Radarr).

Prowlarr calls `GET {base_url}/api?cmd=...&apikey=...`.
"""

from typing import Any, Dict, List, Tuple, Union

from flask import Blueprint, request

from backend.base.custom_exceptions import (InvalidKeyValue,
                                            KapowarrException, KeyNotFound)
from backend.base.definitions import DownloadType, IndexerClient
from backend.base.logging import LOGGER
from backend.implementations.indexer_client_manager import IndexerClients
from backend.internals.settings import Settings

mylar_compat = Blueprint('mylar_compat', __name__)

MylarResponse = Tuple[Dict[str, Any], int]

TORZNAB_CLIENT = 'Torznab'


def _success(data: Union[Dict[str, Any], None] = None) -> MylarResponse:
    result: Dict[str, Any] = {'success': True}
    if data is not None:
        result['data'] = data
    return result, 200


def _error(code: int, message: str) -> MylarResponse:
    return {
        'success': False,
        'error': {'code': code, 'message': message}
    }, 200


def _torznab_indexers() -> List[IndexerClient]:
    return [
        client
        for client in IndexerClients.get_all_clients()
        if client.download_type == DownloadType.TORRENT
        and client.client_type == TORZNAB_CLIENT
    ]


def _get_indexer_by_name(name: str) -> Union[IndexerClient, None]:
    for client in _torznab_indexers():
        if client.title == name:
            return client
    return None


def _parse_bool(value: Union[str, None]) -> bool:
    return (value or 'true').lower() in ('true', '1', 'yes')


def _list_providers() -> MylarResponse:
    torznabs = []
    for client in _torznab_indexers():
        data = client.get_indexer_data()
        torznabs.append({
            'name': data['title'],
            'host': data['url'],
            'apikey': data['api_key'] or '',
            'categories': ','.join(map(str, data['categories'] or [])),
            'enabled': data['enabled'],
            'altername': data['title']
        })

    return _success({'torznabs': torznabs, 'newznabs': []})


def _check_provider_type() -> Union[MylarResponse, None]:
    provider_type = (request.args.get('providertype') or '').lower()
    if provider_type != 'torznab':
        return _error(
            400,
            f'Provider type not supported: {provider_type}. '
            'Bubblarr only supports torrent (torznab) indexers.'
        )
    return None


def _indexer_data_from_args(title: str) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        'title': title,
        'enabled': _parse_bool(request.args.get('enabled')),
        'url': request.args.get('host') or '',
        'api_key': request.args.get('prov_apikey') or ''
    }
    if request.args.get('categories'):
        data['categories'] = request.args['categories']
    return data


def _add_provider() -> MylarResponse:
    error = _check_provider_type()
    if error:
        return error

    name = request.args.get('name')
    if not name:
        return _error(400, 'Missing name')

    if _get_indexer_by_name(name) is not None:
        return _error(409, f'Provider already exists: {name}')

    IndexerClients.add(
        download_type=DownloadType.TORRENT,
        client_type=TORZNAB_CLIENT,
        prowlarr_synced=True,
        run_test=False,
        **_indexer_data_from_args(name)
    )
    return _success()


def _change_provider() -> MylarResponse:
    error = _check_provider_type()
    if error:
        return error

    name = request.args.get('name') or ''
    client = _get_indexer_by_name(name)
    if client is None:
        return _error(404, f'Provider not found: {name}')

    current = client.get_indexer_data()
    new_data = _indexer_data_from_args(
        request.args.get('altername') or name
    )
    client.update_indexer(
        {
            # Keep the settings that Prowlarr doesn't manage
            'min_seeders': current['min_seeders'],
            'priority': current['priority'],
            'categories': current['categories'],
            **new_data
        },
        run_test=False
    )
    # Prowlarr can also take over an indexer that was added manually but
    # points to Prowlarr, so it's managed by Prowlarr from now on.
    IndexerClients.set_prowlarr_synced(client.id, True)
    return _success()


def _delete_provider() -> MylarResponse:
    name = request.args.get('name') or ''
    client = _get_indexer_by_name(name)
    if client is None:
        return _error(404, f'Provider not found: {name}')

    client.delete_indexer()
    return _success()


def _describe(e: KapowarrException) -> str:
    response = e.api_response
    return f"{response['error']}: {response['result']}"


COMMANDS = {
    'getVersion': lambda: _success(),
    'listProviders': _list_providers,
    'addProvider': _add_provider,
    'changeProvider': _change_provider,
    'delProvider': _delete_provider
}


@mylar_compat.route('/api', methods=['GET'])
def mylar_api():
    api_key = request.args.get('apikey')
    if not api_key or api_key != Settings().sv.api_key:
        ip = request.environ.get('HTTP_X_FORWARDED_FOR', request.remote_addr)
        LOGGER.warning(f'Unauthorised Mylar API request from {ip}')
        return _error(401, 'Invalid API key')

    command = request.args.get('cmd') or ''
    handler = COMMANDS.get(command)
    if handler is None:
        return _error(400, f'Command not supported: {command}')

    LOGGER.debug(f'Mylar API request: {command}')
    try:
        return handler()

    except (KeyNotFound, InvalidKeyValue) as e:
        return _error(400, _describe(e))

    except KapowarrException as e:
        LOGGER.exception('Mylar API command failed: ')
        return _error(500, _describe(e))
