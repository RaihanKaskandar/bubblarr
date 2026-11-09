# -*- coding: utf-8 -*-

"""
Uploading comic files from the device of the user into the library.

A file is uploaded in chunks into its own folder inside the download folder.
Then it's analysed and matched to a volume and issue in the library, using the
metadata inside the file and its filename. Finally, once the user agrees, it's
moved into the volume folder under the name that the naming settings dictate.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import get_close_matches
from json import dump, load
from os import replace, scandir
from os.path import basename, dirname, getsize, isdir, join, splitext
from re import compile
from shutil import disk_usage
from time import time
from typing import IO, Any, Dict, List, Sequence, Tuple, Union
from uuid import uuid4

from backend.base.custom_exceptions import (InvalidKeyValue, UploadNoSpace,
                                            UploadNotFound,
                                            UploadOffsetMismatch)
from backend.base.definitions import (ComicInfoData, Constants, FileConstants,
                                      FilenameData, IssueData, SpecialVersion,
                                      VolumeData)
from backend.base.file_extraction import (extract_filename_data,
                                          extract_issue_number,
                                          refine_special_version,
                                          special_version_regex)
from backend.base.files import (archive_contains_issues, create_folder,
                                delete_file_folder, get_archive_mimetype,
                                rename_file, set_detected_extension)
from backend.base.helpers import extract_year_from_date, force_range
from backend.base.logging import LOGGER
from backend.features.webhooks import upload_imported
from backend.implementations.comicinfo import read_comicinfo
from backend.implementations.conversion import mass_convert
from backend.implementations.file_matching import (scan_files,
                                                   set_file_matching)
from backend.implementations.file_processing import mass_process_files
from backend.implementations.matching import (clean_title,
                                              match_special_version, match_year)
from backend.implementations.naming import (generate_issue_name,
                                            same_name_indexing)
from backend.implementations.volumes import Library, Volume
from backend.internals.db import commit, get_db
from backend.internals.db_models import FilesDB
from backend.internals.server import Server
from backend.internals.settings import Settings

META_FILENAME = "meta.json"
WRITE_SIZE = 1_048_576 # bytes
SIMILAR_TITLE_CUTOFF = 0.85

ARCHIVE_EXTENSIONS = (".cbz", ".zip", ".cbr", ".rar", ".cb7", ".7z")
"Extensions that can be corrected based on the content of the file"

CHECKED_ARCHIVE_EXTENSIONS = (".cbz", ".zip", ".cbr", ".rar")
"Extensions of which we can tell whether the content is a working archive"

upload_id_regex = compile(r'[0-9a-f]{32}')


# region Staging
def _uploads_folder() -> str:
    return join(Settings().sv.download_folder, Constants.UPLOAD_FOLDER)


def _upload_folder(upload_id: str) -> str:
    # The ID ends up in a filepath, so don't let it be anything else than an ID
    if not upload_id_regex.fullmatch(upload_id):
        raise UploadNotFound(upload_id)
    return join(_uploads_folder(), upload_id)


def _read_meta(upload_id: str) -> Dict[str, Any]:
    try:
        with open(
            join(_upload_folder(upload_id), META_FILENAME),
            encoding='utf-8'
        ) as f:
            return load(f)

    except (OSError, ValueError):
        raise UploadNotFound(upload_id)


def _write_meta(meta: Dict[str, Any]) -> None:
    folder = _upload_folder(meta['id'])
    temp_file = join(folder, META_FILENAME + '.tmp')
    with open(temp_file, 'w', encoding='utf-8') as f:
        dump(meta, f)
    replace(temp_file, join(folder, META_FILENAME))
    return


def _data_file(meta: Dict[str, Any]) -> str:
    return join(_upload_folder(meta['id']), 'file' + meta['ext'])


def _delete_stale_uploads() -> None:
    "Delete uploads that were abandoned, so that they don't fill up the disk"
    folder = _uploads_folder()
    if not isdir(folder):
        return

    with scandir(folder) as entries:
        upload_ids = [e.name for e in entries if e.is_dir()]

    now = time()
    for upload_id in upload_ids:
        upload_folder = join(folder, upload_id)
        try:
            with scandir(upload_folder) as entries:
                last_activity = max(
                    (e.stat().st_mtime for e in entries),
                    default=0.0
                )

        except OSError:
            # Imported or deleted in the meantime
            continue

        try:
            complete = _read_meta(upload_id)['complete']
        except UploadNotFound:
            complete = False

        lifetime = (
            Constants.UPLOAD_READY_LIFETIME
            if complete else
            Constants.UPLOAD_INCOMPLETE_LIFETIME
        )
        if now - last_activity > lifetime:
            LOGGER.info(f'Deleting abandoned upload: {upload_id}')
            delete_file_folder(upload_folder)

    return


def create_upload(filename: str, size: int) -> Dict[str, Any]:
    """Start a new upload.

    Args:
        filename (str): The name of the file on the device of the user.
        size (int): The size of the file in bytes.

    Raises:
        InvalidKeyValue: The file is not a type of comic file, or the size is
            invalid.
        UploadNoSpace: There is not enough free disk space for the file.

    Returns:
        Dict[str, Any]: The ID of the upload and the amount of bytes received.
    """
    if not isinstance(filename, str):
        raise InvalidKeyValue('filename', filename)

    # Only used as a source of information, never as the name of a file on disk
    name = basename(filename.replace('\\', '/')).strip()
    ext = splitext(name)[1].lower()
    if ext not in FileConstants.UPLOAD_EXTENSIONS:
        raise InvalidKeyValue('filename', filename)

    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise InvalidKeyValue('size', size)

    uploads_folder = _uploads_folder()
    create_folder(uploads_folder)
    _delete_stale_uploads()

    available = disk_usage(uploads_folder).free
    if size > available:
        raise UploadNoSpace(size, available)

    meta = {
        'id': uuid4().hex,
        'filename': name,
        'ext': ext,
        'size': size,
        'created_at': round(time()),
        'complete': False,
        'comicinfo': None,
        'warnings': []
    }
    create_folder(_upload_folder(meta['id']))
    with open(_data_file(meta), 'wb'):
        pass
    _write_meta(meta)

    LOGGER.info(f'Started upload of {name} ({size} bytes): {meta["id"]}')
    return {'id': meta['id'], 'received': 0}


def write_chunk(upload_id: str, offset: int, stream: IO[bytes]) -> int:
    """Write a chunk of an upload to disk. Sending a chunk again is allowed, as
    the response to the first attempt could've been lost.

    Args:
        upload_id (str): The ID of the upload.
        offset (int): The position in the file of the first byte of the chunk.
        stream (IO[bytes]): The content of the chunk.

    Raises:
        UploadNotFound: The upload does not exist.
        UploadOffsetMismatch: The chunk would leave a gap in the file.
        InvalidKeyValue: The chunk goes past the end of the file, or the
            upload is already complete.

    Returns:
        int: The amount of bytes received so far.
    """
    meta = _read_meta(upload_id)
    if meta['complete']:
        raise InvalidKeyValue('upload_id', upload_id)

    filepath = _data_file(meta)
    received = getsize(filepath)
    if not 0 <= offset <= received:
        raise UploadOffsetMismatch(received)

    position = offset
    with open(filepath, 'r+b') as f:
        f.seek(offset)
        f.truncate()

        while True:
            block = stream.read(WRITE_SIZE)
            if not block:
                break

            position += len(block)
            if position > meta['size']:
                f.truncate(offset)
                raise InvalidKeyValue('offset', offset)

            f.write(block)

    return position


def _analyse_file(meta: Dict[str, Any]) -> None:
    """Fill in what can be learned from the file itself.

    Args:
        meta (Dict[str, Any]): The metadata of the upload. Is updated in-place.
    """
    filepath = _data_file(meta)

    if meta['ext'] in ARCHIVE_EXTENSIONS:
        corrected_filepath = set_detected_extension(filepath)
        if corrected_filepath != filepath:
            replace(filepath, corrected_filepath)
            filepath = corrected_filepath
            meta['ext'] = splitext(filepath)[1]

    if (
        meta['ext'] in CHECKED_ARCHIVE_EXTENSIONS
        and get_archive_mimetype(filepath) is None
    ):
        meta['warnings'].append('unreadable')
        return

    try:
        if archive_contains_issues(filepath):
            meta['warnings'].append('contains_issues')

    except Exception as e:
        LOGGER.debug(f'Failed to check content of {filepath}: {e}')
        meta['warnings'].append('unreadable')
        return

    meta['comicinfo'] = read_comicinfo(filepath)
    return


def complete_upload(upload_id: str) -> Dict[str, Any]:
    """Finish an upload after all chunks are received.

    Args:
        upload_id (str): The ID of the upload.

    Raises:
        UploadNotFound: The upload does not exist.
        UploadOffsetMismatch: Not all bytes of the file have been received.

    Returns:
        Dict[str, Any]: The proposal for the upload.
    """
    meta = _read_meta(upload_id)

    if not meta['complete']:
        received = getsize(_data_file(meta))
        if received != meta['size']:
            raise UploadOffsetMismatch(received)

        _analyse_file(meta)
        meta['complete'] = True
        _write_meta(meta)
        LOGGER.info(f'Finished upload of {meta["filename"]}: {upload_id}')

    return _build_proposal(meta, LibraryMatcher())


def delete_upload(upload_id: str) -> None:
    """Delete an upload.

    Args:
        upload_id (str): The ID of the upload.

    Raises:
        UploadNotFound: The upload does not exist.
    """
    _read_meta(upload_id)
    delete_file_folder(_upload_folder(upload_id))
    LOGGER.info(f'Deleted upload: {upload_id}')
    return


def get_uploads() -> List[Dict[str, Any]]:
    """Get the finished uploads that haven't been imported yet.

    Returns:
        List[Dict[str, Any]]: The proposals, sorted by filename.
    """
    _delete_stale_uploads()

    folder = _uploads_folder()
    if not isdir(folder):
        return []

    with scandir(folder) as entries:
        upload_ids = [e.name for e in entries if e.is_dir()]

    matcher = LibraryMatcher()
    proposals = []
    for upload_id in upload_ids:
        try:
            meta = _read_meta(upload_id)
        except UploadNotFound:
            continue

        if meta['complete']:
            proposals.append(_build_proposal(meta, matcher))

    proposals.sort(key=lambda p: p['filename'].lower())
    return proposals


# region Matching
def _comicinfo_to_file_data(
    comicinfo: ComicInfoData,
    series: str
) -> FilenameData:
    """Convert embedded metadata to the form that the matching works with.

    Args:
        comicinfo (ComicInfoData): The embedded metadata.
        series (str): The series in the metadata.

    Returns:
        FilenameData: The equivalent filename data.
    """
    issue_number = None
    if comicinfo['number']:
        try:
            issue_number = extract_issue_number(comicinfo['number'])
        except (ValueError, IndexError):
            pass

    format = comicinfo['format'] or ''
    special_version = None
    special_version_result = special_version_regex.search(format)
    if special_version_result:
        special_version = special_version_result.lastgroup.replace('_', '-')
    elif issue_number is None:
        special_version = SpecialVersion.TPB.value

    # ComicVine has separate volumes for annuals, named `<series> Annual`
    annual = 'annual' in (series + ' ' + format).lower()
    if annual and 'annual' not in series.lower():
        series += ' Annual'

    return FilenameData({
        'series': series,
        'year': comicinfo['year'],
        'volume_number': comicinfo['volume'],
        'special_version': special_version,
        'issue_number': issue_number,
        'annual': annual
    })


def _get_file_data_options(
    meta: Dict[str, Any]
) -> List[Tuple[str, FilenameData]]:
    """List what we know about the file, best source first.

    Args:
        meta (Dict[str, Any]): The metadata of the upload.

    Returns:
        List[Tuple[str, FilenameData]]: The name of the source and the data.
    """
    options: List[Tuple[str, FilenameData]] = []

    comicinfo: Union[ComicInfoData, None] = meta['comicinfo']
    if comicinfo and comicinfo['series']:
        options.append((
            'comicinfo',
            _comicinfo_to_file_data(comicinfo, comicinfo['series'])
        ))

    options.append((
        'filename',
        extract_filename_data(meta['filename'], assume_volume_number=False)
    ))
    return options


@dataclass
class _Rating:
    "How well a file fits a volume"
    volume_id: int
    issue_ids: List[int]
    score: int
    conflict: Union[str, None]
    year: int

    @property
    def rank(self) -> Tuple[bool, int]:
        return (self.conflict is None, self.score)


class LibraryMatcher:
    """Finds the volume and issue in the library that a file is for. Keep an
    instance for a batch of files; what it loads is reused for the whole batch.
    """

    def __init__(self) -> None:
        self._titles: Dict[str, List[int]] = {}
        self._volumes: Dict[int, Tuple[VolumeData, List[IssueData]]] = {}

        for volume in get_db().execute(
            "SELECT id, title, alt_title FROM volumes;"
        ).fetchall():
            for title in {volume['title'], volume['alt_title']}:
                if title:
                    self._titles.setdefault(
                        clean_title(title), []
                    ).append(volume['id'])
        return

    def _load_volume(
        self,
        volume_id: int
    ) -> Tuple[VolumeData, List[IssueData]]:
        if volume_id not in self._volumes:
            volume = Volume(volume_id)
            issues = volume.get_issues(_skip_files=True)
            issues.sort(key=lambda i: i.calculated_issue_number)
            self._volumes[volume_id] = (volume.get_data(), issues)
        return self._volumes[volume_id]

    @staticmethod
    def _find_issues(
        file_data: FilenameData,
        volume_data: VolumeData,
        issues: List[IssueData]
    ) -> List[IssueData]:
        "Find the issues that the file covers, like the file scanner does"
        if (
            issues
            and file_data['special_version']
            and volume_data.special_version not in (
                SpecialVersion.VOLUME_AS_ISSUE,
                SpecialVersion.NORMAL
            )
        ):
            return issues[:1]

        if file_data['issue_number'] is None:
            return []

        start, end = force_range(file_data['issue_number'])
        return [
            i
            for i in issues
            if start <= i.calculated_issue_number <= end
        ]

    def rate(self, file_data: FilenameData, volume_id: int) -> _Rating:
        """Determine how well a file fits a volume with a matching title.

        Args:
            file_data (FilenameData): What's known about the file.
            volume_id (int): The ID of the volume.

        Returns:
            _Rating: The rating.
        """
        volume_data, issues = self._load_volume(volume_id)
        file_data = refine_special_version(volume_data, FilenameData(
            file_data # type: ignore
        ))
        covered_issues = self._find_issues(file_data, volume_data, issues)
        covered_issue_ids = [i.id for i in covered_issues]

        issue_years = [
            year
            for year in (extract_year_from_date(i.date) for i in issues)
            if year is not None
        ]
        end_year = max(issue_years, default=volume_data.year)

        score = 8 if covered_issues else 0
        conflict = None

        year = file_data['year']
        if year is not None:
            issue_year = None
            if covered_issues:
                issue_year = extract_year_from_date(covered_issues[0].date)

            if issue_year is not None and match_year(issue_year, year):
                score += 3
            elif match_year(volume_data.year, year, end_year):
                score += 1
            else:
                conflict = 'year'

            if year == volume_data.year:
                score += 2

        volume_number = file_data['volume_number']
        if isinstance(volume_number, int):
            # The volume number of ComicVine tagged files is the start year
            if volume_number in (volume_data.volume_number, volume_data.year):
                score += 4
            elif match_year(volume_data.year, volume_number):
                score += 2
            else:
                conflict = conflict or 'volume_number'

        if not match_special_version(
            volume_data.special_version,
            file_data['special_version'],
            volume_data.title,
            file_data['issue_number']
        ):
            conflict = conflict or 'special_version'

        if file_data['annual'] != ('annual' in volume_data.title.lower()):
            conflict = conflict or 'annual'

        return _Rating(
            volume_id,
            covered_issue_ids,
            score,
            conflict,
            volume_data.year or 0
        )

    def describe(
        self,
        meta: Dict[str, Any],
        volume_id: int,
        issue_ids: Sequence[int],
        confidence: str,
        reason: str
    ) -> Dict[str, Any]:
        """Build the description of a match.

        Args:
            meta (Dict[str, Any]): The metadata of the upload.
            volume_id (int): The ID of the volume.
            issue_ids (Sequence[int]): The IDs of the issues in the volume.
            confidence (str): How sure we are; high, low or manual.
            reason (str): What the confidence is based on.

        Returns:
            Dict[str, Any]: The description.
        """
        volume_data, issues = self._load_volume(volume_id)
        covered_issues = [i for i in issues if i.id in issue_ids]

        existing_files = sorted({
            file['filepath']
            for issue in covered_issues
            for file in FilesDB.fetch(issue_id=issue.id)
        })

        return {
            'volume_id': volume_id,
            'volume_title': volume_data.title,
            'volume_year': volume_data.year,
            'volume_number': volume_data.volume_number,
            'special_version': volume_data.special_version.value,
            'issue_ids': [i.id for i in covered_issues],
            'issue_numbers': [i.issue_number for i in covered_issues],
            'confidence': confidence,
            'reason': reason,
            'destination': (
                _plan_destination(meta, volume_data, covered_issues)
                if covered_issues else
                None
            ),
            'existing_files': existing_files
        }

    @staticmethod
    def _judge(
        ratings: List[_Rating],
        similar_title: bool
    ) -> Tuple[str, str]:
        "Determine the confidence and the reason for it of the best rating"
        best = ratings[0]

        if similar_title:
            return 'low', 'similar_title'

        if best.conflict:
            return 'low', best.conflict

        if not best.issue_ids:
            return 'low', 'issue_not_found'

        if len(ratings) > 1 and ratings[1].rank == best.rank:
            return 'low', 'ambiguous'

        return 'high', 'title'

    def match(self, meta: Dict[str, Any]) -> Union[Dict[str, Any], None]:
        """Find the volume and issue in the library that an upload is for.

        Args:
            meta (Dict[str, Any]): The metadata of the upload.

        Returns:
            Union[Dict[str, Any], None]: The description of the match, or `None`
                if nothing in the library resembles the upload.
        """
        comicinfo: ComicInfoData = meta['comicinfo'] or {} # type: ignore
        cursor = get_db()

        # An ID leaves no room for doubt
        if comicinfo.get('comicvine_issue_id'):
            issue = cursor.execute(
                "SELECT id, volume_id FROM issues WHERE comicvine_id = ? LIMIT 1;",
                (comicinfo['comicvine_issue_id'],)
            ).fetchone()
            if issue:
                return self.describe(
                    meta, issue['volume_id'], [issue['id']],
                    'high', 'comicvine_id'
                )

        id_volume = None
        if comicinfo.get('comicvine_volume_id'):
            id_volume = cursor.execute(
                "SELECT id FROM volumes WHERE comicvine_id = ? LIMIT 1;",
                (comicinfo['comicvine_volume_id'],)
            ).exists()

        fallback = None
        for _, file_data in _get_file_data_options(meta):
            title = clean_title(file_data['series'] or '')
            similar_title = False

            if id_volume:
                volume_ids = [id_volume]

            elif title in self._titles:
                volume_ids = self._titles[title]

            else:
                similar_title = True
                volume_ids = [
                    volume_id
                    for similar in get_close_matches(
                        title,
                        self._titles,
                        n=3,
                        cutoff=SIMILAR_TITLE_CUTOFF
                    )
                    for volume_id in self._titles[similar]
                ]

            if not volume_ids:
                continue

            ratings = sorted(
                (self.rate(file_data, v) for v in set(volume_ids)),
                key=lambda r: (*r.rank, r.year),
                reverse=True
            )
            best = ratings[0]
            confidence, reason = self._judge(ratings, similar_title)

            if confidence == 'high':
                if id_volume:
                    reason = 'comicvine_id'
                return self.describe(
                    meta, best.volume_id, best.issue_ids, confidence, reason
                )

            if fallback is None:
                fallback = (best, confidence, reason)

        if fallback is None:
            return None

        best, confidence, reason = fallback
        return self.describe(
            meta, best.volume_id, best.issue_ids, confidence, reason
        )


def _build_proposal(
    meta: Dict[str, Any],
    matcher: LibraryMatcher
) -> Dict[str, Any]:
    source, file_data = _get_file_data_options(meta)[0]
    return {
        'id': meta['id'],
        'filename': meta['filename'],
        'size': meta['size'],
        'created_at': meta['created_at'],
        'warnings': meta['warnings'],
        'detected': {
            'source': source,
            'series': file_data['series'],
            'issue_number': file_data['issue_number'],
            'volume_number': file_data['volume_number'],
            'year': file_data['year']
        },
        'match': matcher.match(meta)
    }


def preview_match(
    upload_id: str,
    volume_id: int,
    issue_ids: Union[List[int], None] = None
) -> Dict[str, Any]:
    """Describe what importing an upload into a specific volume would do.

    Args:
        upload_id (str): The ID of the upload.

        volume_id (int): The ID of the volume.

        issue_ids (Union[List[int], None], optional): The IDs of the issues. If
            `None`, the issues are determined based on the upload.
            Defaults to None.

    Raises:
        UploadNotFound: The upload does not exist.
        VolumeNotFound: The volume does not exist.
        InvalidKeyValue: An issue is not part of the volume.

    Returns:
        Dict[str, Any]: The description of the match.
    """
    meta = _read_meta(upload_id)
    volume = Library.get_volume(volume_id)
    matcher = LibraryMatcher()

    if issue_ids is not None:
        _get_issues(volume, issue_ids)

    else:
        issue_ids = []
        for _, file_data in _get_file_data_options(meta):
            issue_ids = matcher.rate(file_data, volume_id).issue_ids
            if issue_ids:
                break

    return matcher.describe(meta, volume_id, issue_ids, 'manual', 'manual')


# region Importing
def _get_issues(volume: Volume, issue_ids: List[int]) -> List[IssueData]:
    wanted_ids = set(issue_ids)
    issues = [
        issue
        for issue in volume.get_issues(_skip_files=True)
        if issue.id in wanted_ids
    ]
    if not issues or len(issues) != len(wanted_ids):
        raise InvalidKeyValue('issue_ids', issue_ids)

    return issues


def _plan_destination(
    meta: Dict[str, Any],
    volume_data: VolumeData,
    issues: List[IssueData]
) -> str:
    """Determine where an upload should end up. The filepath is never that of
    an existing file; existing files are not to be touched.

    Args:
        meta (Dict[str, Any]): The metadata of the upload.
        volume_data (VolumeData): The data of the volume.
        issues (List[IssueData]): The issues that the upload covers.

    Returns:
        str: The filepath.
    """
    numbers = sorted(i.calculated_issue_number for i in issues)
    filename_body = generate_issue_name(
        volume_data,
        numbers[0] if len(numbers) == 1 else (numbers[0], numbers[-1])
    )

    source = _data_file(meta)
    return same_name_indexing(
        volume_data.folder,
        {source: join(volume_data.folder, filename_body + meta['ext'])}
    )[source]


def _add_to_history(
    meta: Dict[str, Any],
    filepath: str,
    volume_id: int,
    issue_id: Union[int, None]
) -> None:
    get_db().execute(
        """
        INSERT INTO download_history(
            web_link, web_title,
            file_title,
            volume_id, issue_id,
            source, downloaded_at, success
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            f'{Server.url_base}/volumes/{volume_id}', meta['filename'],
            splitext(basename(filepath))[0],
            volume_id, issue_id,
            'Upload', round(time()), True
        )
    )
    return


def import_upload(
    upload_id: str,
    volume_id: int,
    issue_ids: List[int]
) -> Dict[str, Any]:
    """Move an upload into the library. The file gets the name that the naming
    settings dictate for the issues, and is processed like a download would be.
    Existing files are left alone, so the issues could end up having
    multiple files.

    Args:
        upload_id (str): The ID of the upload.
        volume_id (int): The ID of the volume to import into.
        issue_ids (List[int]): The IDs of the issues that the file covers.

    Raises:
        UploadNotFound: The upload does not exist.
        VolumeNotFound: The volume does not exist.
        InvalidKeyValue: The upload is not finished, or an issue is not part
            of the volume.

    Returns:
        Dict[str, Any]: The final filepath of the file and the volume and
            issues it's for.
    """
    meta = _read_meta(upload_id)
    if not meta['complete']:
        raise InvalidKeyValue('upload_id', upload_id)

    volume = Library.get_volume(volume_id)
    volume_data = volume.get_data()
    issues = _get_issues(volume, issue_ids)
    single_issue_id = issues[0].id if len(issues) == 1 else None

    filepath = _plan_destination(meta, volume_data, issues)
    LOGGER.info(f'Importing upload {meta["filename"]} to {filepath}')

    # Moving a big file takes a while, so don't keep the database locked
    commit()
    rename_file(_data_file(meta), filepath)
    delete_file_folder(_upload_folder(upload_id))

    scan_files(volume_id, filepath_filter=[filepath], update_websocket=True)

    # The scanner works on the filename, and depending on the naming format
    # that might not contain enough information to come to the same conclusion
    numbers = sorted({i.calculated_issue_number for i in issues})
    if FilesDB.issues_covered(filepath) != numbers:
        set_file_matching(volume_id, [{
            'filepath': filepath,
            'issue_ids': [i.id for i in issues],
            'general_file': False,
            'forced_match': True
        }])

    if Settings().sv.convert:
        converted_files = mass_convert(
            volume_id,
            single_issue_id,
            filepath_filter=[filepath],
            update_websocket_files=True,
            process_individual_files=False
        )
        if converted_files:
            filepath = converted_files[0]

    mass_process_files(volume_id, single_issue_id)

    _add_to_history(meta, filepath, volume_id, single_issue_id)
    commit()

    upload_imported(
        volume_id,
        [i.id for i in issues],
        meta['filename'],
        meta['size']
    )

    return {
        'filepath': filepath,
        'folder': dirname(filepath),
        'volume_id': volume_id,
        'issue_ids': [i.id for i in issues]
    }
