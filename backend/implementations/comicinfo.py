# -*- coding: utf-8 -*-

"""
Reading the metadata that is embedded in comic files. This is the
ComicInfo.xml file that tools like ComicTagger and ComicRack put inside
the archive of a comic.
"""

from os.path import basename
from re import IGNORECASE, compile
from typing import Dict, Iterable, Union
from xml.etree.ElementTree import ParseError, fromstring
from zipfile import ZipFile

from backend.base.definitions import ComicInfoData
from backend.base.files import get_archive_mimetype
from backend.base.helpers import read_file_from_rar, run_rar
from backend.base.logging import LOGGER

COMICINFO_FILENAME = "comicinfo.xml"
MAX_COMICINFO_SIZE = 1_048_576 # bytes

cv_link_regex = compile(
    r'comicvine\.(?:gamespot\.)?com/\S*?/(40[05]0)-(\d+)',
    IGNORECASE
)
cv_notes_regex = compile(r'(?:\bissue\s*id|\bcvdb)[\s:#\-]*(\d+)', IGNORECASE)
year_regex = compile(r'\d{4}')


def _pick_comicinfo_entry(names: Iterable[str]) -> Union[str, None]:
    """Find the ComicInfo.xml file in a list of files in an archive. The one
    closest to the root of the archive is preferred.

    Args:
        names (Iterable[str]): The paths of the files in the archive.

    Returns:
        Union[str, None]: The path of the file, or `None` if there is none.
    """
    return min(
        (
            name
            for name in names
            if basename(name.replace('\\', '/')).lower() == COMICINFO_FILENAME
        ),
        key=lambda name: (name.count('/') + name.count('\\'), len(name)),
        default=None
    )


def _read_from_zip(filepath: str) -> Union[bytes, None]:
    with ZipFile(filepath, "r") as archive:
        entry = _pick_comicinfo_entry(archive.namelist())
        if entry is None:
            return None

        with archive.open(entry) as file:
            content = file.read(MAX_COMICINFO_SIZE + 1)

    return content if len(content) <= MAX_COMICINFO_SIZE else None


def _read_from_rar(filepath: str) -> Union[bytes, None]:
    entry = _pick_comicinfo_entry(
        run_rar(["lb", "-p-", filepath]).stdout.splitlines()
    )
    if entry is None:
        return None

    return read_file_from_rar(filepath, entry, MAX_COMICINFO_SIZE)


def _decode_xml(content: bytes) -> Union[str, None]:
    if content.startswith((b'\xff\xfe', b'\xfe\xff')):
        encoding = 'utf-16'
    else:
        encoding = 'utf-8-sig'

    try:
        return content.decode(encoding)
    except UnicodeDecodeError:
        return None


def _to_int(value: Union[str, None]) -> Union[int, None]:
    try:
        return int(value or '')
    except ValueError:
        return None


def _extract_comicvine_ids(fields: Dict[str, str]) -> Dict[str, int]:
    """Find the ComicVine IDs of the issue and volume in the metadata. A link
    to ComicVine can be in the `Web` field, and tools like ComicTagger write
    the ID of the issue in the `Notes` field.

    Args:
        fields (Dict[str, str]): The fields of the metadata.

    Returns:
        Dict[str, int]: The ID's that were found, under the keys
            `comicvine_issue_id` and `comicvine_volume_id`.
    """
    result: Dict[str, int] = {}

    for link in cv_link_regex.finditer(fields.get('web', '')):
        key = (
            'comicvine_issue_id'
            if link.group(1) == '4000' else
            'comicvine_volume_id'
        )
        result.setdefault(key, int(link.group(2)))

    if 'comicvine_issue_id' not in result:
        notes = cv_notes_regex.search(fields.get('notes', ''))
        if notes:
            result['comicvine_issue_id'] = int(notes.group(1))

    return result


def parse_comicinfo(content: bytes) -> Union[ComicInfoData, None]:
    """Parse the content of a ComicInfo.xml file.

    The file comes from the outside, so it's refused when it has a document
    type declaration. ComicInfo doesn't need one, and it's what entity
    expansion attacks are built on.

    Args:
        content (bytes): The content of the file.

    Returns:
        Union[ComicInfoData, None]: The metadata, or `None` if the file is
            invalid or doesn't contain anything useful.
    """
    text = _decode_xml(content)
    if text is None or '<!doctype' in text.lower():
        return None

    try:
        root = fromstring(text)
    except ParseError:
        return None

    if root.tag.split('}')[-1].lower() != 'comicinfo':
        return None

    fields = {
        child.tag.split('}')[-1].lower(): child.text.strip()
        for child in root
        if isinstance(child.tag, str) and child.text and child.text.strip()
    }

    comicvine_ids = _extract_comicvine_ids(fields)
    year = fields.get('year', '')

    result: ComicInfoData = {
        'series': fields.get('series'),
        'number': fields.get('number'),
        'volume': _to_int(fields.get('volume')),
        'year': int(year) if year_regex.fullmatch(year) else None,
        'format': fields.get('format'),
        'comicvine_issue_id': comicvine_ids.get('comicvine_issue_id'),
        'comicvine_volume_id': comicvine_ids.get('comicvine_volume_id')
    }

    if not (
        result['series']
        or result['comicvine_issue_id']
        or result['comicvine_volume_id']
    ):
        return None

    return result


def read_comicinfo(filepath: str) -> Union[ComicInfoData, None]:
    """Read the embedded metadata of a comic file.

    Args:
        filepath (str): The filepath of the comic file. Zip and rar based
            files are supported, the type is based on the content of the file.

    Returns:
        Union[ComicInfoData, None]: The metadata, or `None` if the file doesn't
            have any or if the file isn't readable.
    """
    archive_type = get_archive_mimetype(filepath)

    try:
        if archive_type == 'zip':
            content = _read_from_zip(filepath)
        elif archive_type == 'rar':
            content = _read_from_rar(filepath)
        else:
            return None

    except Exception as e:
        # Broken or encrypted archives raise a wide range of exceptions.
        # Metadata is optional, so it's not a problem when it isn't readable.
        LOGGER.debug(f'Failed to read metadata of {filepath}: {e}')
        return None

    if content is None:
        return None

    return parse_comicinfo(content)
