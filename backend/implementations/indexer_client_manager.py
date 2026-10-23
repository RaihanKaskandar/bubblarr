# -*- coding: utf-8 -*-

"""
The manager of indexer clients and their base class
"""

from importlib import import_module
from os.path import basename, dirname, splitext
from typing import Any, Dict, List, Mapping, Tuple, Type, TypeVar, Union

import backend.implementations.indexer_clients as ic
from backend.base.custom_exceptions import (AddingIndexerForbidden,
                                            ClientNotWorking,
                                            CredentialInvalid, IndexerNotFound,
                                            InvalidKeyValue, KeyNotFound)
from backend.base.definitions import (ClientTestResult, DownloadType,
                                      GCDownloadService, IndexerClient,
                                      IndexerClientData, IndexerClientField)
from backend.base.files import list_files
from backend.base.helpers import CommaList, normalise_base_url
from backend.base.logging import LOGGER
from backend.internals.db import get_db

ICF = IndexerClientField


DEFAULT_PRIORITY = 25
DEFAULT_CATEGORIES = [7030]
"Newznab category for Books/Comics"
DEFAULT_MIN_SEEDERS = 1


def _validate_indexer_data(
    data: Mapping[str, Any],
    required_tokens: Tuple[IndexerClientField, ...],
    enforce_existence: bool = True
) -> Dict[str, Any]:
    """Validate and normalise indexer data.

    Args:
        data (Mapping[str, Any]): The data to validate.
        required_tokens (Tuple[IndexerClientField, ...]): The fields that the
            client uses.
        enforce_existence (bool, optional): Raise an exception if a field is
            missing, instead of defaulting it to None.
            Defaults to True.

    Raises:
        KeyNotFound: A required key was not found.
        InvalidKeyValue: One of the keys has an invalid value.

    Returns:
        Dict[str, Any]: A value for every `IndexerClientField`, in a format
            that can be stored in the database. Fields that the client doesn't
            use are set to None.
    """
    # Fields that have a sensible default, so can be left out
    defaults: Dict[IndexerClientField, Any] = {
        ICF.PRIORITY: DEFAULT_PRIORITY,
        ICF.CATEGORIES: DEFAULT_CATEGORIES,
        ICF.MIN_SEEDERS: DEFAULT_MIN_SEEDERS,
        ICF.API_KEY: ''
    }

    filtered_data: Dict[str, Any] = {
        key.value: None
        for key in ICF
    }
    filtered_data[ICF.PRIORITY.value] = DEFAULT_PRIORITY

    for key in required_tokens:
        value = data.get(key.value)
        if value is None and key in defaults:
            value = defaults[key]

        if key.value not in data and key not in defaults:
            if enforce_existence:
                raise KeyNotFound(key.value)
            continue

        if (
            key in (
                ICF.TITLE,
                ICF.ENABLED,
                ICF.URL
            )
            and value is None
        ):
            raise InvalidKeyValue(key.value, None)

        if key == ICF.URL:
            if not isinstance(value, str):
                raise InvalidKeyValue(key.value, value)
            filtered_data[key.value] = normalise_base_url(value)

        elif key == ICF.ENABLED:
            if not isinstance(value, bool):
                raise InvalidKeyValue(key.value, value)
            filtered_data[key.value] = value

        elif key == ICF.GC_SERVICE_PREFERENCE:
            if value is None:
                continue

            if not isinstance(value, list):
                raise InvalidKeyValue(key.value, value)

            value = CommaList(value)

            available = [
                s.value
                for s in GCDownloadService._member_map_.values()
            ]

            for entry in value:
                if entry not in available:
                    raise InvalidKeyValue(key.value, value)

            for entry in available:
                if entry not in value:
                    raise InvalidKeyValue(key.value, value)

            filtered_data[key.value] = str(value)

        elif key == ICF.GC_AVOID_LARGE_DOWNLOADS:
            if value is None:
                continue

            if not isinstance(value, bool):
                raise InvalidKeyValue(key.value, value)
            filtered_data[key.value] = value

        elif key == ICF.CATEGORIES:
            if isinstance(value, str):
                value = [v.strip() for v in value.split(',') if v.strip()]

            if not isinstance(value, list) or not value:
                raise InvalidKeyValue(key.value, value)

            try:
                categories = [int(v) for v in value]
            except (TypeError, ValueError):
                raise InvalidKeyValue(key.value, value)

            if any(c <= 0 for c in categories):
                raise InvalidKeyValue(key.value, value)

            filtered_data[key.value] = str(CommaList(map(str, categories)))

        elif key == ICF.MIN_SEEDERS:
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise InvalidKeyValue(key.value, value)
            filtered_data[key.value] = value

        elif key == ICF.PRIORITY:
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 1 <= value <= 50
            ):
                raise InvalidKeyValue(key.value, value)
            filtered_data[key.value] = value

        else:
            if not isinstance(value, str):
                raise InvalidKeyValue(key.value, value)
            filtered_data[key.value] = value

    return filtered_data


# region Base Indexer Client
class BaseIndexerClient(IndexerClient):
    @property
    def id(self) -> int:
        return self._id

    @property
    def title(self) -> str:
        return self._title

    def __init__(self, indexer_id: int) -> None:
        self._id = indexer_id
        data = get_db().execute("""
            SELECT
                enabled,
                title, url,
                gc_service_preference, gc_avoid_large_downloads,
                api_key, categories, min_seeders, priority,
                prowlarr_synced
            FROM indexer_clients
            WHERE id = ?
            LIMIT 1;
            """,
            (indexer_id,)
        ).fetchone()
        self._load_data(data)
        return

    def _load_data(self, data: Mapping[str, Any]) -> None:
        self._enabled: bool = bool(data['enabled'])
        self._title: str = data['title']
        self._url: str = data['url']

        if (
            data["gc_service_preference"] is not None
            and data["gc_avoid_large_downloads"] is not None
        ):
            self._gc_service_preference: Union[CommaList, None] = CommaList(
                data["gc_service_preference"]
            )
            self._gc_avoid_large_downloads: Union[bool, None] = bool(
                data["gc_avoid_large_downloads"]
            )
        else:
            self._gc_service_preference = None
            self._gc_avoid_large_downloads = None

        self._api_key: Union[str, None] = data["api_key"]
        self._categories: Union[List[int], None] = (
            [int(c) for c in CommaList(data["categories"])]
            if data["categories"] is not None
            else None
        )
        self._min_seeders: Union[int, None] = data["min_seeders"]
        self._priority: int = (
            data["priority"]
            if data["priority"] is not None
            else DEFAULT_PRIORITY
        )
        self._prowlarr_synced: bool = bool(data["prowlarr_synced"])
        return

    def get_indexer_data(self) -> IndexerClientData:
        return {
            'id': self._id,
            'enabled': self._enabled,
            'download_type': self.download_type.value,
            'client_type': self.client_type,
            'required_tokens': [rt.value for rt in self.required_tokens],
            'title': self._title,
            'url': self._url,
            'gc_service_preference': self._gc_service_preference,
            'gc_avoid_large_downloads': self._gc_avoid_large_downloads,
            'api_key': self._api_key,
            'categories': self._categories,
            'min_seeders': self._min_seeders,
            'priority': self._priority,
            'prowlarr_synced': self._prowlarr_synced
        }

    def update_indexer(
        self,
        data: Mapping[str, Any],
        run_test: bool = True
    ) -> None:
        LOGGER.info(
            f"Updating indexer {self._id}: {_redact(data)}"
        )
        filtered_data = _validate_indexer_data(data, self.required_tokens)

        if run_test:
            # Raises exception on fail
            self.test(**filtered_data)

        get_db().execute("""
            UPDATE indexer_clients
            SET
                enabled = :enabled,
                title = :title,
                url = :url,
                gc_service_preference = :gc_service_preference,
                gc_avoid_large_downloads = :gc_avoid_large_downloads,
                api_key = :api_key,
                categories = :categories,
                min_seeders = :min_seeders,
                priority = :priority
            WHERE id = :id;
            """,
            {
                **filtered_data,
                "id": self._id
            }
        )
        self._load_data({
            **filtered_data,
            "prowlarr_synced": self._prowlarr_synced
        })
        return

    def delete_indexer(self) -> None:
        LOGGER.info(f'Deleting indexer {self.id}')
        get_db().execute(
            "DELETE FROM indexer_clients WHERE id = ?;",
            (self.id,)
        )
        return


def _redact(data: Mapping[str, Any]) -> Dict[str, Any]:
    "Hide the API key in data that's going to be logged"
    return {
        k: ('<redacted>' if k == ICF.API_KEY.value and v else v)
        for k, v in data.items()
    }


# region Clients
IndexerClientType = TypeVar(
    "IndexerClientType",
    bound=IndexerClient
)


class IndexerClients:
    clients: Dict[DownloadType, Dict[str, Type[IndexerClient]]] = {
        dt: {}
        for dt in DownloadType
    }

    @classmethod
    def register_client(
        cls,
        download_type: DownloadType,
        client_type: str,
        required_tokens: Tuple[IndexerClientField, ...],
        allow_multiple_instances: bool
    ):
        """Register an indexer client.

        ```
        @IndexerClients.register_client(
            DownloadType.TORRENT, 'ProductName',
            (ICF.TITLE, ICF.ENABLED, ICF.URL),
            allow_multiple_instances=True
        )
        class ProductName(IndexerClient):
            ...
        ```

        Args:
            download_type (DownloadType): The protocol that the client supplies
                downloads for.
            client_type (str): The name of the indexer client (e.g. 'Torznab').
            required_tokens (Tuple[IndexerClientField, ...]): The fields that
                the client needs.
            allow_multiple_instances (bool): Allow this client to be added
                multiple times. For something like Torznab, you want that. For
                something like GC, you don't want to allow that.

        Raises:
            RuntimeError: An indexer client with the given client type is
                already registered for the download type.
        """
        def wrapper(
            client_class: Type[IndexerClientType]
        ) -> Type[IndexerClientType]:
            if client_type in cls.clients[download_type]:
                raise RuntimeError(
                    f"Indexer client with client type {client_type} "
                    f"(download type {download_type.name}) "
                    "registered multiple times"
                )
            cls.clients[download_type][client_type] = client_class
            client_class.download_type = download_type
            client_class.client_type = client_type
            client_class.required_tokens = required_tokens
            client_class.allow_multiple_instances = allow_multiple_instances
            return client_class
        return wrapper

    @staticmethod
    def trigger_client_registration() -> None:
        """
        Import the implementations of the indexer clients in the sub-folders,
        automatically making them register themselves.
        """
        for file in sorted(
            list_files(
                dirname(ic.__file__ or ''),
                (".py",)
            ),
            key=lambda f: f.lower()
        ):
            if file.endswith("__init__.py"):
                continue

            foldername = basename(dirname(file))
            filename = splitext(basename(file))[0]
            module_path = f"{ic.__name__}.{foldername}.{filename}"
            import_module(module_path)
        return

    @classmethod
    def test(
        cls,
        download_type: DownloadType,
        client_type: str,
        url: str,
        **extra_fields: Any
    ) -> ClientTestResult:
        """Test whether an indexer client is supported, working and available.

        Args:
            download_type (DownloadType): The protocol that the client supplies
                downloads for.

            client_type (str): The client type of the client, as supplied when
                they registered to this class.

            url (str): The url on which the indexer is available.

            extra_fields (kwargs, optional): Extra fields and their values,
                possibly used by the indexer during testing.

        Raises:
            InvalidKeyValue: One of the parameters has an invalid argument.

        Returns:
            ClientTestResult: Whether the test was successful.
        """
        try:
            type_clients = cls.clients[download_type]
        except KeyError:
            raise InvalidKeyValue('download_type', download_type)

        try:
            ClientClass = type_clients[client_type]
        except KeyError:
            raise InvalidKeyValue('client_type', client_type)

        filtered_data = _validate_indexer_data(
            {'url': url, **extra_fields},
            ClientClass.required_tokens,
            enforce_existence=False
        )

        try:
            ClientClass.test(**filtered_data)

        except ClientNotWorking as e:
            return ClientTestResult({
                'success': False,
                'description': e.reason.value
            })

        except CredentialInvalid:
            return ClientTestResult({
                'success': False,
                'description': 'invalid_credentials'
            })

        else:
            return ClientTestResult({
                'success': True,
                'description': None
            })

    @classmethod
    def add(
        cls,
        download_type: DownloadType,
        client_type: str,
        enabled: bool,
        title: str,
        url: str,
        prowlarr_synced: bool = False,
        run_test: bool = True,
        **extra_fields: Any
    ) -> IndexerClient:
        """Add an indexer client.

        Args:
            download_type (DownloadType): The protocol that the client supplies
                downloads for.

            client_type (str): The client type of the client, as supplied when
                they registered to this class.

            enabled (bool): Whether the indexer is enabled or not.

            title (str): The title to give the indexer.

            url (str): The url on which the indexer is available.

            prowlarr_synced (bool, optional): Whether the indexer is added
                by Prowlarr (app sync), instead of by the user.
                Defaults to False.

            run_test (bool, optional): Test the indexer before adding it.
                Defaults to True.

            extra_fields (kwargs, optional): Extra fields and their values,
                possibly used by the indexer.

        Raises:
            InvalidKeyValue: One of the parameters has an invalid argument.
            AddingIndexerForbidden: Not allowed to add another instance of this
                indexer client.
            ClientNotWorking: Can't connect to client.
            CredentialInvalid: Credentials are invalid.

        Returns:
            IndexerClient: The new client.
        """
        try:
            type_clients = cls.clients[download_type]
        except KeyError:
            raise InvalidKeyValue('download_type', download_type)

        try:
            ClientClass = type_clients[client_type]
        except KeyError:
            raise InvalidKeyValue('client_type', client_type)

        cursor = get_db()
        if not ClientClass.allow_multiple_instances:
            cursor.execute("""
                SELECT 1
                FROM indexer_clients
                WHERE download_type = ?
                    AND client_type = ?
                LIMIT 1;
                """,
                (download_type, client_type)
            )
            if cursor.exists():
                raise AddingIndexerForbidden(download_type, client_type)

        data = {
            'enabled': enabled,
            'title': title,
            'url': url,
            **extra_fields
        }
        LOGGER.info(
            "Adding indexer: download_type=%s, client_type=%s, data=%s",
            download_type, client_type, _redact(data)
        )
        filtered_data = _validate_indexer_data(
            data,
            ClientClass.required_tokens
        )

        if run_test:
            # Raises exception on fail
            ClientClass.test(**filtered_data)

        filtered_data.update({
            'download_type': ClientClass.download_type.value,
            'client_type': client_type,
            'prowlarr_synced': prowlarr_synced
        })

        indexer_id = cursor.execute(
            """
            INSERT INTO indexer_clients(
                enabled,
                download_type, client_type,
                title, url,
                gc_service_preference, gc_avoid_large_downloads,
                api_key, categories, min_seeders, priority,
                prowlarr_synced
            ) VALUES (
                :enabled,
                :download_type, :client_type,
                :title, :url,
                :gc_service_preference, :gc_avoid_large_downloads,
                :api_key, :categories, :min_seeders, :priority,
                :prowlarr_synced
            );
            """,
            filtered_data
        ).lastrowid
        return cls.get_client(indexer_id)

    @staticmethod
    def set_prowlarr_synced(indexer_id: int, prowlarr_synced: bool) -> None:
        """Set whether an indexer is managed by Prowlarr (app sync).

        Args:
            indexer_id (int): The ID of the indexer client.
            prowlarr_synced (bool): Whether it's managed by Prowlarr.
        """
        get_db().execute(
            "UPDATE indexer_clients SET prowlarr_synced = ? WHERE id = ?;",
            (prowlarr_synced, indexer_id)
        )
        return

    @classmethod
    def get_all_data(cls) -> List[IndexerClientData]:
        """Get a list of all data of the indexer clients.

        Returns:
            List[IndexerClientData]: The list of all data of the indexer clients.
        """
        clients = get_db().execute("""
            SELECT id, download_type, client_type
            FROM indexer_clients
            ORDER BY title, id;
        """).fetchall()

        result: List[IndexerClientData] = []
        for client in clients:
            ClientClass = (cls
                .clients[DownloadType(client["download_type"])]
                [client["client_type"]]
            )
            result.append(
                ClientClass(client["id"]).get_indexer_data()
            )

        return result

    @classmethod
    def get_all_clients(cls) -> List[IndexerClient]:
        """Get a list of all indexer clients.

        Returns:
            List[IndexerClient]: The list of all indexer clients.
        """
        # Fetch all rows first, as initialising a client uses the same cursor
        clients = get_db().execute("""
            SELECT download_type, client_type, id
            FROM indexer_clients
            ORDER BY title, id;
        """).fetchall()

        return [
            cls.clients[DownloadType(client[0])][client[1]](client[2])
            for client in clients
        ]

    @classmethod
    def get_client(cls, indexer_id: int) -> IndexerClient:
        """Get an indexer client based on its ID.

        Args:
            indexer_id (int): The ID of the indexer client.

        Raises:
            IndexerNotFound: The ID does not link to any client.

        Returns:
            IndexerClient: The client.
        """
        client = get_db().execute("""
            SELECT download_type, client_type
            FROM indexer_clients
            WHERE id = ?
            LIMIT 1;
            """,
            (indexer_id,)
        ).fetchone()

        if not client:
            raise IndexerNotFound(indexer_id)

        return cls.clients[DownloadType(client[0])][client[1]](indexer_id)
