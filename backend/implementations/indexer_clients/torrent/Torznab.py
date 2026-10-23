"""
Torznab indexer client. Torznab is the API that Jackett and Prowlarr (and some
trackers natively) expose for searching torrent sites, the same API that
Sonarr and Radarr use.
"""

from asyncio import run
from datetime import datetime
from email.utils import parsedate_to_datetime
from re import IGNORECASE, compile
from typing import Any, Dict, List, Tuple, Union
from xml.etree.ElementTree import Element, ParseError, fromstring

from aiohttp import ClientError

from backend.base.custom_exceptions import ClientNotWorking, CredentialInvalid
from backend.base.definitions import (BrokenClientReason, DownloadType,
                                      IndexerClientField as ICF, QueryResult,
                                      SearchQuery, SearchResultData)
from backend.base.file_extraction import extract_filename_data
from backend.base.helpers import AsyncSession
from backend.base.logging import LOGGER
from backend.implementations.indexer_client_manager import (BaseIndexerClient,
                                                            IndexerClients)

TORZNAB_NS = "http://torznab.com/schemas/2015/feed"
NEWZNAB_NS = "http://www.newznab.com/DTD/2010/feeds/attributes/"
PAGE_SIZE = 100

INVALID_API_KEY_CODES = ("100", "101", "102")
"Newznab error codes for an incorrect, suspended or missing API key"

file_extension_regex = compile(r"\.(cb[rz7t]|pdf|epub|zip|rar|7z)$", IGNORECASE)
loose_year_regex = compile(r"(?<=\s)(?<![(\[])((?:19|20)\d{2})(?![)\]\d])")
paren_year_regex = compile(r"\((?:19|20)\d{2}\)")
foreign_language_regex = compile(
    # Only unambiguous markers: e.g. "(DE)" can also mean "Deluxe Edition"
    r"[\[(](?:fr|fre|vf|vostfr|ita|esp|spa|ger)[\])]"
    r"|\b(?:french|fran[cç]ais|italian|italiano|spanish|espa[nñ]ol|castellano"
    r"|german|deutsch|portugu[eê]s|vostfr)\b"
    # French volume numbering, e.g. "Tome 4" (but not "The Tome of ...")
    r"|\btome\s*\d+",
    IGNORECASE
)
"""
Releases in another language than English. Torrent sites (unlike GetComics)
also have translated editions, e.g. "Absolute Batman T01 - 2025 [CBR] [FR]".
"""


class TorznabError(Exception):
    "The Torznab API responded with an error"

    def __init__(self, code: str, description: str) -> None:
        self.code = code
        self.description = description
        super().__init__(f"Torznab error {code}: {description}")
        return


def get_api_url(url: str) -> str:
    """Get the URL of the Torznab API endpoint from the user-supplied URL.
    Accepts both the feed URL (e.g. `.../torznab`) and the API URL
    (e.g. `.../torznab/api`), like Sonarr does with its default API path.

    Args:
        url (str): The normalised URL of the indexer.

    Returns:
        str: The URL of the API endpoint.
    """
    if url.endswith("/api"):
        return url
    return url + "/api"


def normalise_release_title(title: str) -> str:
    """Make torrent release names easier to parse for
    `extract_filename_data()`. Scene-style names use dots instead of spaces,
    and often have the year as a loose number instead of between parentheses.

    ```
    >>> normalise_release_title("Batman.001.2016.Digital.Zone-Empire.cbr")
    'Batman 001 (2016) Digital Zone-Empire.cbr'
    ```

    Args:
        title (str): The release title.

    Returns:
        str: The normalised title.
    """
    title = title.strip()

    if ' ' not in title and title.count('.') >= 2:
        ext_match = file_extension_regex.search(title)
        suffix = ext_match.group(0) if ext_match else ''
        body = title[:len(title) - len(suffix)]
        title = body.replace('.', ' ').replace('_', ' ') + suffix

    if not paren_year_regex.search(title):
        year_matches = list(loose_year_regex.finditer(title))
        if year_matches:
            # Last loose year is most likely the release year,
            # as earlier ones could be part of the series title (e.g. 2099).
            m = year_matches[-1]
            title = f"{title[:m.start()]}({m.group(1)}){title[m.end():]}"

    return title


def _get_attrs(item: Element) -> Dict[str, str]:
    "Get the torznab:attr (and newznab:attr) values of an item"
    result: Dict[str, str] = {}
    for ns in (NEWZNAB_NS, TORZNAB_NS):
        for attr in item.iterfind(f"{{{ns}}}attr"):
            name = attr.get("name")
            value = attr.get("value")
            if name and value is not None:
                result[name.lower()] = value
    return result


def _to_int(value: Union[str, None]) -> Union[int, None]:
    if value is None:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def parse_torznab_feed(
    xml: str
) -> Tuple[List[Dict[str, Any]], Union[int, None], Union[int, None]]:
    """Parse the response of a Torznab search request.

    Args:
        xml (str): The body of the response.

    Raises:
        TorznabError: The API returned an error.
        ParseError: The response is not valid XML.

    Returns:
        Tuple[List[Dict[str, Any]], Union[int, None], Union[int, None]]:
            The items, the total amount of results (if supplied) and the offset
            of this page (if supplied). Each item has the keys `title`, `link`,
            `info_url`, `size`, `seeders`, `peers` and `pub_date`.
    """
    root = fromstring(xml.strip())

    if root.tag == "error":
        raise TorznabError(
            root.get("code", ""),
            root.get("description", "")
        )

    channel = root.find("channel")
    if channel is None:
        return [], None, None

    total = offset = None
    response = channel.find(f"{{{NEWZNAB_NS}}}response")
    if response is None:
        response = channel.find(f"{{{TORZNAB_NS}}}response")
    if response is not None:
        total = _to_int(response.get("total"))
        offset = _to_int(response.get("offset"))

    items: List[Dict[str, Any]] = []
    for item in channel.iterfind("item"):
        title = (item.findtext("title") or '').strip()
        if not title:
            continue

        attrs = _get_attrs(item)
        enclosure = item.find("enclosure")
        enclosure_url = enclosure.get("url") if enclosure is not None else None

        # Prefer the .torrent file, as it contains the real name of the
        # torrent (needed to find the downloaded files). The download prepper
        # converts it to a magnet link. The link can also redirect to a magnet
        # link (for magnet-only trackers).
        link = (
            enclosure_url
            or (item.findtext("link") or '').strip()
            or attrs.get("magneturl")
        )
        if not link:
            continue

        size = _to_int(item.findtext("size"))
        if size is None:
            size = _to_int(attrs.get("size"))
        if size is None and enclosure is not None:
            size = _to_int(enclosure.get("length"))

        seeders = _to_int(attrs.get("seeders"))
        peers = _to_int(attrs.get("peers"))

        pub_date = None
        pub_date_str = item.findtext("pubDate")
        if pub_date_str:
            try:
                pub_date = parsedate_to_datetime(pub_date_str)
            except (TypeError, ValueError):
                pub_date = None

        items.append({
            "title": title,
            "link": link,
            "info_url": (
                item.findtext("comments")
                or item.findtext("guid")
                or None
            ),
            "size": size if size is not None else -1,
            "seeders": seeders,
            "peers": peers,
            "pub_date": pub_date
        })

    return items, total, offset


@IndexerClients.register_client(
    DownloadType.TORRENT, 'Torznab',
    (
        ICF.TITLE, ICF.ENABLED, ICF.URL,
        ICF.API_KEY, ICF.CATEGORIES, ICF.MIN_SEEDERS, ICF.PRIORITY
    ),
    allow_multiple_instances=True
)
class TorznabIndexer(BaseIndexerClient):
    def __init__(self, indexer_id: int) -> None:
        super().__init__(indexer_id)
        self.session: Union[AsyncSession, None] = None
        return

    def _base_params(self) -> Dict[str, str]:
        params = {
            "t": "search",
            "extended": "1",
            "limit": str(PAGE_SIZE)
        }
        if self._api_key:
            params["apikey"] = self._api_key
        if self._categories:
            params["cat"] = ','.join(map(str, self._categories))
        return params

    async def _fetch(
        self,
        params: Dict[str, str]
    ) -> Tuple[List[Dict[str, Any]], Union[int, None], Union[int, None]]:
        if not self.session:
            self.session = AsyncSession()

        xml = await self.session.get_text(
            get_api_url(self._url),
            params=params,
            quiet_fail=True
        )
        if not xml:
            return [], None, None

        try:
            return parse_torznab_feed(xml)

        except TorznabError as e:
            LOGGER.warning(
                "Indexer %s returned an error: %s",
                self._title, e.description or e.code
            )

        except ParseError:
            LOGGER.warning(
                "Indexer %s returned a response that is not Torznab",
                self._title
            )

        return [], None, None

    def _to_search_result(self, item: Dict[str, Any]) -> SearchResultData:
        return {
            **extract_filename_data(
                normalise_release_title(item["title"]),
                assume_volume_number=False,
                fix_year=True
            ),
            "link": item["link"],
            "display_title": item["title"],
            "size": item["size"],
            "indexer_id": self._id,
            "indexer_title": self._title,
            "download_type": self.download_type.value,
            "seeders": item["seeders"],
            "indexer_priority": self._priority
        }

    def _passes_filters(self, item: Dict[str, Any]) -> bool:
        if (
            self._min_seeders
            and item["seeders"] is not None
            and item["seeders"] < self._min_seeders
        ):
            return False

        if foreign_language_regex.search(item["title"]):
            return False

        return True

    async def search(self, query: SearchQuery) -> QueryResult:
        offset = (query["page"] - 1) * PAGE_SIZE
        items, total, response_offset = await self._fetch({
            **self._base_params(),
            "q": query["query"],
            "offset": str(offset)
        })

        if total is not None:
            next_page_available = (
                (response_offset or offset) + len(items) < total
            )
        else:
            next_page_available = len(items) >= PAGE_SIZE

        return QueryResult(
            [
                self._to_search_result(item)
                for item in items
                if self._passes_filters(item)
            ],
            next_page_available=next_page_available
        )

    async def discover(self, last_check: datetime) -> List[SearchResultData]:
        # A search without a query returns the latest releases (the RSS feed)
        items, _, _ = await self._fetch(self._base_params())

        result: List[SearchResultData] = []
        for item in items:
            pub_date: Union[datetime, None] = item["pub_date"]
            if pub_date is not None:
                if pub_date.tzinfo is not None:
                    # last_check is naive local time
                    pub_date = pub_date.astimezone().replace(tzinfo=None)
                if pub_date < last_check:
                    continue

            if not self._passes_filters(item):
                continue

            result.append(self._to_search_result(item))

        return result

    async def shutdown(self) -> None:
        if self.session:
            await self.session.close()
            self.session = None
        return

    @classmethod
    async def __test(cls, url: str, api_key: Union[str, None]) -> None:
        params = {"t": "caps"}
        if api_key:
            params["apikey"] = api_key

        async with AsyncSession() as session:
            try:
                async with session.get(
                    get_api_url(url),
                    params=params
                ) as response:
                    status = response.status
                    xml = await response.text()

            except ClientError:
                raise ClientNotWorking(BrokenClientReason.CONNECTION_ERROR)

        try:
            root = fromstring(xml.strip())
        except ParseError:
            if status in (401, 403):
                raise CredentialInvalid
            raise ClientNotWorking(BrokenClientReason.NOT_CLIENT_INSTANCE)

        if root.tag == "error":
            if root.get("code") in INVALID_API_KEY_CODES:
                raise CredentialInvalid
            raise ClientNotWorking(
                BrokenClientReason.FAILED_PROCESSING_RESPONSE
            )

        if root.tag != "caps":
            raise ClientNotWorking(BrokenClientReason.NOT_CLIENT_INSTANCE)

        return

    @classmethod
    def test(
        cls,
        url: str,
        api_key: Union[str, None] = None,
        **extra_fields: Any
    ) -> None:
        run(cls.__test(url, api_key))
        return
