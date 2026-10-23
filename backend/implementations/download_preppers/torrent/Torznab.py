"""
Turns a release from a Torznab indexer (Jackett, Prowlarr, etc.) into a
torrent download. The link of a release is either a magnet link, or a link to
a .torrent file (which can also redirect to a magnet link).
"""

from hashlib import sha1
from typing import Any, Dict, List, Tuple, Union
from urllib.parse import quote, urljoin

from bencoding import bdecode, bencode
from requests import RequestException

from backend.base.custom_exceptions import (DownloadLinkBroken,
                                            EnqueuingDownloadFailure,
                                            IssueNotFound)
from backend.base.definitions import (Download, DownloadPrepper,
                                      DownloadService, DownloadType,
                                      EnqueuingDownloadFailureReason,
                                      SpecialVersion)
from backend.base.file_extraction import (extract_filename_data,
                                          refine_special_version)
from backend.base.helpers import Session
from backend.base.logging import LOGGER
from backend.implementations.download_clients.Torrent import (
    TorrentDownload, fetch_torrent_name, get_magnet_name)
from backend.implementations.download_prepper_manager import DownloadPreppers
from backend.implementations.indexer_client_manager import IndexerClients
from backend.implementations.indexer_clients.torrent.Torznab import \
    normalise_release_title
from backend.implementations.volumes import Volume

MAX_REDIRECTS = 5


def torrent_to_magnet(torrent: bytes) -> Tuple[str, str]:
    """Convert the contents of a .torrent file to a magnet link.

    Args:
        torrent (bytes): The contents of the .torrent file.

    Raises:
        ValueError: The content is not a valid torrent file.

    Returns:
        Tuple[str, str]: The magnet link and the name of the torrent (which is
            also the name of the file or folder that is downloaded).
    """
    try:
        data: Dict[bytes, Any] = bdecode(torrent) # type: ignore
        info = data[b"info"]
        name: str = info[b"name"].decode("utf-8", errors="replace")
    except Exception:
        raise ValueError("Invalid torrent file")

    info_hash = sha1(bencode(info)).hexdigest()

    trackers: List[str] = []
    if b"announce" in data:
        trackers.append(data[b"announce"].decode(errors="replace"))
    for tier in data.get(b"announce-list", []):
        for tracker in tier:
            tracker = tracker.decode(errors="replace")
            if tracker not in trackers:
                trackers.append(tracker)

    magnet = (
        f"magnet:?xt=urn:btih:{info_hash}"
        f"&dn={quote(name, safe='')}"
        + ''.join(f"&tr={quote(t, safe='')}" for t in trackers)
    )
    return magnet, name


def set_magnet_name(magnet: str, name: str) -> str:
    """Set the display name (`dn`) of a magnet link.

    Args:
        magnet (str): The magnet link.
        name (str): The new display name.

    Returns:
        str: The magnet link with the new display name.
    """
    prefix, _, query = magnet.partition('?')
    params = [
        p
        for p in query.split('&')
        if p and not p.startswith('dn=')
    ]
    params.insert(1, f"dn={quote(name, safe='')}")
    return f"{prefix}?{'&'.join(params)}"


def resolve_torrent_link(link: str) -> Tuple[str, Union[str, None]]:
    """Get a magnet link from a release link, and the name of the torrent if
    it's known for certain.

    Args:
        link (str): The link of the release (magnet or URL).

    Raises:
        DownloadLinkBroken: The link doesn't lead to a torrent.

    Returns:
        Tuple[str, Union[str, None]]: The magnet link and the name of the
            torrent. The name is None if only a magnet link is available.
    """
    if link.startswith("magnet:"):
        return link, None

    url = link
    with Session() as session:
        for _ in range(MAX_REDIRECTS):
            try:
                response = session.get(url, allow_redirects=False)
            except RequestException:
                raise DownloadLinkBroken(link)

            if response.is_redirect:
                location = response.headers.get("Location", "")
                if location.startswith("magnet:"):
                    return location, None
                url = urljoin(url, location)
                continue

            if not response.ok:
                raise DownloadLinkBroken(link)

            try:
                return torrent_to_magnet(response.content)
            except ValueError:
                raise DownloadLinkBroken(link)

    raise DownloadLinkBroken(link)


@DownloadPreppers.register_prepper(DownloadType.TORRENT, "Torznab")
class TorznabPrepper(DownloadPrepper):
    @property
    def web_title(self) -> Union[str, None]:
        return self._web_title

    def __init__(
        self,
        link: str,
        indexer_id: int,
        volume_id: int,
        issue_id: Union[int, None] = None,
        force_match: bool = False
    ) -> None:
        self.link = link
        self.indexer_id = indexer_id
        self.volume_id = volume_id
        self.issue_id = issue_id
        self.force_match = force_match

        self._web_title: Union[str, None] = None
        return

    def __get_covered_issues(
        self,
        release_name: str
    ) -> Union[float, Tuple[float, float], None]:
        volume = Volume(self.volume_id)
        volume_data = volume.get_data()

        file_data = refine_special_version(
            volume_data,
            extract_filename_data(
                normalise_release_title(release_name),
                assume_volume_number=False,
                fix_year=True
            )
        )

        if (
            file_data["issue_number"] is None
            and file_data["special_version"] in (None, SpecialVersion.NORMAL)
            and self.issue_id is not None
        ):
            # Release name doesn't contain the issue number, but the release
            # was explicitly chosen for the issue.
            return volume.get_issue(
                self.issue_id
            ).get_data().calculated_issue_number

        return file_data["issue_number"]

    def get_downloads(self) -> List[Download]:
        LOGGER.debug("Resolving torrent link: %s", self.link)

        try:
            magnet, torrent_name = resolve_torrent_link(self.link)
        except DownloadLinkBroken:
            raise EnqueuingDownloadFailure(
                EnqueuingDownloadFailureReason.WEBPAGE_BROKEN
            )

        if torrent_name is None:
            # Only a magnet link is available. Its display name is set by the
            # indexer, which is not always the real name of the torrent.
            try:
                torrent_name = fetch_torrent_name(magnet)
                magnet = set_magnet_name(magnet, torrent_name)
            except DownloadLinkBroken:
                LOGGER.warning(
                    "Couldn't fetch metadata of torrent, assuming the name "
                    "in the magnet link is correct: %s",
                    magnet
                )

        release_name = torrent_name or get_magnet_name(magnet) or ''
        self._web_title = release_name or None

        indexer_title = IndexerClients.get_client(self.indexer_id).title

        try:
            download = TorrentDownload(
                download_link=magnet,
                volume_id=self.volume_id,
                covered_issues=self.__get_covered_issues(release_name),
                download_service=DownloadService.TORZNAB,
                source_name=indexer_title,
                web_link=self.link,
                web_title=self._web_title,
                web_sub_title=None,
                forced_match=self.force_match,
                torrent_name=torrent_name
            )

        except DownloadLinkBroken:
            raise EnqueuingDownloadFailure(
                EnqueuingDownloadFailureReason.LINK_BROKEN
            )

        except IssueNotFound:
            # Release covers issues that the volume doesn't have
            raise EnqueuingDownloadFailure(
                EnqueuingDownloadFailureReason.NO_MATCHES
            )

        return [download]
