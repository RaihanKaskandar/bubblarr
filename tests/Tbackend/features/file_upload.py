import unittest
from io import BytesIO
from os import listdir, makedirs, utime
from os.path import basename, dirname, exists, isdir, join
from time import time
from unittest.mock import patch
from zipfile import ZipFile

from backend.base.custom_exceptions import UploadNotFound
from backend.base.definitions import Constants
from backend.implementations.comicinfo import parse_comicinfo, read_comicinfo
from Tbackend.implementations.torznab import DatabaseTestCase

COMICINFO = """<?xml version="1.0" encoding="utf-8"?>
<ComicInfo xmlns:xsd="http://www.w3.org/2001/XMLSchema">
    <Series>Batman</Series>
    <Number>12</Number>
    <Volume>2016</Volume>
    <Year>2017</Year>
    <Format>Series</Format>
    {extra}
</ComicInfo>
"""


def make_cbz(comicinfo=None, entry='ComicInfo.xml') -> bytes:
    content = BytesIO()
    with ZipFile(content, 'w') as archive:
        archive.writestr('page01.jpg', b'image')
        if comicinfo is not None:
            archive.writestr(entry, comicinfo)
    return content.getvalue()


class ParseComicInfo(unittest.TestCase):
    def test_fields(self):
        result = parse_comicinfo(COMICINFO.format(extra='').encode())
        self.assertEqual(result, {
            'series': 'Batman',
            'number': '12',
            'volume': 2016,
            'year': 2017,
            'format': 'Series',
            'comicvine_issue_id': None,
            'comicvine_volume_id': None
        })

    def test_comicvine_issue_link(self):
        result = parse_comicinfo(COMICINFO.format(extra=(
            '<Web>https://comicvine.gamespot.com/batman-12/4000-555/</Web>'
        )).encode())
        self.assertEqual(result['comicvine_issue_id'], 555)
        self.assertIsNone(result['comicvine_volume_id'])

    def test_comicvine_volume_link(self):
        result = parse_comicinfo(COMICINFO.format(extra=(
            '<Web>https://comicvine.gamespot.com/batman/4050-91273/</Web>'
        )).encode())
        self.assertEqual(result['comicvine_volume_id'], 91273)
        self.assertIsNone(result['comicvine_issue_id'])

    def test_comicvine_id_in_notes(self):
        for notes in (
            'Tagged with ComicTagger 1.3 using info from Comic Vine on '
            '2022-01-01. [Issue ID 777]',
            'Scraped metadata from Comic Vine [CVDB777] on 2022.01.01'
        ):
            result = parse_comicinfo(COMICINFO.format(
                extra=f'<Notes>{notes}</Notes>'
            ).encode())
            self.assertEqual(result['comicvine_issue_id'], 777)

    def test_other_encodings(self):
        text = COMICINFO.format(extra='').replace('utf-8', 'utf-16')
        self.assertEqual(
            parse_comicinfo(text.encode('utf-16'))['series'],
            'Batman'
        )
        self.assertEqual(
            parse_comicinfo(
                b'\xef\xbb\xbf' + COMICINFO.format(extra='').encode()
            )['series'],
            'Batman'
        )

    def test_refuses_doctype(self):
        attack = """<?xml version="1.0"?>
        <!DOCTYPE lolz [<!ENTITY lol "lol">]>
        <ComicInfo><Series>&lol;</Series></ComicInfo>
        """
        self.assertIsNone(parse_comicinfo(attack.encode()))
        self.assertIsNone(parse_comicinfo(attack.encode('utf-16')))

    def test_invalid_input(self):
        for content in (
            b'', b'not xml', b'<ComicInfo><Series>',
            b'<Other><Series>Batman</Series></Other>',
            b'<ComicInfo></ComicInfo>', b'\xff\x00\xff\x00'
        ):
            self.assertIsNone(parse_comicinfo(content))


class ReadComicInfo(unittest.TestCase):
    def setUp(self):
        from tempfile import mkdtemp
        self.folder = mkdtemp()
        return

    def _write(self, content: bytes) -> str:
        path = join(self.folder, 'file.cbz')
        with open(path, 'wb') as f:
            f.write(content)
        return path

    def test_zip(self):
        path = self._write(make_cbz(COMICINFO.format(extra='')))
        self.assertEqual(read_comicinfo(path)['series'], 'Batman')

    def test_name_is_case_insensitive_and_can_be_in_folder(self):
        path = self._write(make_cbz(
            COMICINFO.format(extra=''), 'Some Folder/COMICINFO.XML'
        ))
        self.assertEqual(read_comicinfo(path)['series'], 'Batman')

    def test_no_metadata(self):
        self.assertIsNone(read_comicinfo(self._write(make_cbz())))

    def test_too_big_metadata_is_ignored(self):
        path = self._write(make_cbz(
            COMICINFO.format(extra='<Notes>' + 'a' * 2_000_000 + '</Notes>')
        ))
        self.assertIsNone(read_comicinfo(path))

    def test_damaged_file(self):
        self.assertIsNone(read_comicinfo(self._write(b'PK\x03\x04garbage')))
        self.assertIsNone(read_comicinfo(self._write(b'just text')))


class UploadTestCase(DatabaseTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from backend.internals.db import get_db
        from backend.internals.settings import Settings

        cls.root = join(cls.db_folder, 'comics', '')
        cls.downloads = join(cls.db_folder, 'downloads', '')
        makedirs(cls.root)
        makedirs(cls.downloads)
        Settings().update({'download_folder': cls.downloads})

        cursor = get_db()
        cursor.execute(
            "INSERT INTO root_folders(id, folder) VALUES (1, ?);", (cls.root,)
        )
        volumes = (
            # id, cv id, title, year, volume number, special version
            (1, 101, 'Batman', 2011, 2, None),
            (2, 102, 'Batman', 2016, 3, None),
            (3, 103, 'Invincible', 2003, 1, None),
            (4, 104, 'Batman Annual', 2011, 1, None),
            (5, 105, 'The Killing Joke', 1988, 1, 'one-shot'),
            (6, 106, "Marvel's Voices", 2020, 1, None)
        )
        cursor.executemany("""
            INSERT INTO volumes(
                id, comicvine_id, title, year, publisher, volume_number,
                site_url, monitored, root_folder, folder, special_version
            ) VALUES (?, ?, ?, ?, 'DC Comics', ?, '', 1, 1, ?, ?);
            """,
            (
                (i, cv, title, year, number, join(cls.root, f'{title} {year}'), sv)
                for i, cv, title, year, number, sv in volumes
            )
        )
        issues = (
            # id, volume id, cv id, number, calculated number, date
            (1, 1, 1001, '1', 1.0, '2011-09-01'),
            (2, 1, 1012, '12', 12.0, '2012-08-01'),
            (3, 2, 2001, '1', 1.0, '2016-06-01'),
            (4, 2, 2012, '12', 12.0, '2017-01-01'),
            (5, 2, 2013, '13', 13.0, '2017-01-15'),
            (6, 2, 2014, '14', 14.0, '2017-02-01'),
            (7, 3, 3001, '1', 1.0, '2003-01-01'),
            (8, 3, 3050, '50', 50.0, '2008-06-01'),
            (9, 4, 4001, '1', 1.0, '2012-01-01'),
            (10, 5, 5001, '1', 1.0, '1988-03-01'),
            (11, 6, 6001, '1', 1.0, '2020-11-01')
        )
        cursor.executemany("""
            INSERT INTO issues(
                id, volume_id, comicvine_id, issue_number,
                calculated_issue_number, date, description, monitored
            ) VALUES (?, ?, ?, ?, ?, ?, '', 1);
            """,
            issues
        )
        cursor.connection.commit()
        cls._clear_root_folder_cache()
        return

    @classmethod
    def tearDownClass(cls):
        cls._clear_root_folder_cache()
        super().tearDownClass()
        return

    @staticmethod
    def _clear_root_folder_cache():
        from backend.implementations.root_folders import RootFolders

        # The root folder is inserted directly, not via RootFolders.add()
        RootFolders()._RootFolders__get_folder_mapping.cache_clear()
        return

    def setUp(self):
        self.client = self.app.test_client()
        self.webhook = patch('backend.features.file_upload.upload_imported')
        self.webhook_mock = self.webhook.start()
        self.addCleanup(self.webhook.stop)
        return

    def tearDown(self):
        from shutil import rmtree

        from backend.internals.db import get_db

        rmtree(join(self.downloads, Constants.UPLOAD_FOLDER), ignore_errors=True)
        rmtree(self.root, ignore_errors=True)
        makedirs(self.root)
        cursor = get_db()
        cursor.execute("DELETE FROM issues_files;")
        cursor.execute("DELETE FROM files;")
        cursor.execute("DELETE FROM download_history;")
        cursor.connection.commit()
        return

    def api(self, method, path, **kwargs):
        response = self.client.open(
            f'{Constants.API_PREFIX}{path}',
            method=method,
            headers={'X-Api-Key': self.api_key},
            **kwargs
        )
        return response.status_code, response.get_json()

    def upload(self, filename='file.cbz', content=None):
        content = make_cbz() if content is None else content
        code, created = self.api(
            'POST', '/uploads',
            json={'filename': filename, 'size': len(content)}
        )
        self.assertEqual(code, 201)
        upload_id = created['result']['id']

        code, chunk = self.api(
            'PUT', f'/uploads/{upload_id}?offset=0',
            data=content, content_type='application/octet-stream'
        )
        self.assertEqual(code, 200)
        self.assertEqual(chunk['result']['received'], len(content))

        code, completed = self.api('POST', f'/uploads/{upload_id}/complete')
        self.assertEqual(code, 200)
        return completed['result']


class Staging(UploadTestCase):
    def test_chunks_in_order(self):
        content = make_cbz() * 3
        _, created = self.api('POST', '/uploads', json={
            'filename': 'Batman 012.cbz', 'size': len(content)
        })
        upload_id = created['result']['id']
        middle = len(content) // 2

        code, _ = self.api(
            'POST', f'/uploads/{upload_id}/complete'
        )
        self.assertEqual(code, 409)

        for start, end in ((0, middle), (middle, len(content))):
            code, response = self.api(
                'PUT', f'/uploads/{upload_id}?offset={start}',
                data=content[start:end],
                content_type='application/octet-stream'
            )
            self.assertEqual((code, response['result']['received']), (200, end))

        stored = join(
            self.downloads, Constants.UPLOAD_FOLDER, upload_id, 'file.cbz'
        )
        with open(stored, 'rb') as f:
            self.assertEqual(f.read(), content)

    def test_chunk_can_be_sent_again(self):
        _, created = self.api('POST', '/uploads', json={
            'filename': 'a.cbz', 'size': 6
        })
        upload_id = created['result']['id']
        for _ in range(2):
            code, response = self.api(
                'PUT', f'/uploads/{upload_id}?offset=0',
                data=b'abc', content_type='application/octet-stream'
            )
            self.assertEqual((code, response['result']['received']), (200, 3))

    def test_gap_is_refused(self):
        _, created = self.api('POST', '/uploads', json={
            'filename': 'a.cbz', 'size': 6
        })
        code, response = self.api(
            'PUT', f'/uploads/{created["result"]["id"]}?offset=3',
            data=b'abc', content_type='application/octet-stream'
        )
        self.assertEqual(code, 409)
        self.assertEqual(response['result']['received'], 0)

    def test_more_than_announced_is_refused(self):
        _, created = self.api('POST', '/uploads', json={
            'filename': 'a.cbz', 'size': 2
        })
        upload_id = created['result']['id']
        code, _ = self.api(
            'PUT', f'/uploads/{upload_id}?offset=0',
            data=b'abc', content_type='application/octet-stream'
        )
        self.assertEqual(code, 400)
        stored = join(
            self.downloads, Constants.UPLOAD_FOLDER, upload_id, 'file.cbz'
        )
        with open(stored, 'rb') as f:
            self.assertEqual(f.read(), b'')

    def test_refused_input(self):
        for body in (
            {'filename': 'virus.exe', 'size': 5},
            {'filename': 'noextension', 'size': 5},
            {'filename': 'a.cbz', 'size': 0},
            {'filename': 'a.cbz', 'size': -1},
            {'filename': 'a.cbz', 'size': '5'},
            {'filename': 5, 'size': 5}
        ):
            code, _ = self.api('POST', '/uploads', json=body)
            self.assertEqual(code, 400, body)

    def test_id_cant_escape_the_upload_folder(self):
        from backend.features.file_upload import delete_upload, write_chunk

        for upload_id in ('..', '../..', 'a' * 31, 'A' * 32, 'a' * 32 + '/'):
            with self.assertRaises(UploadNotFound):
                delete_upload(upload_id)
            with self.assertRaises(UploadNotFound):
                write_chunk(upload_id, 0, BytesIO(b'a'))

    def test_path_in_filename_is_dropped(self):
        for filename in ('../../etc/x.cbz', '..\\..\\x.cbz', '/abs/x.cbz'):
            proposal = self.upload(filename)
            self.assertEqual(proposal['filename'], 'x.cbz')
            self.assertEqual(
                sorted(listdir(join(
                    self.downloads, Constants.UPLOAD_FOLDER, proposal['id']
                ))),
                ['file.cbz', 'meta.json']
            )

    def test_requires_api_key(self):
        response = self.client.get(f'{Constants.API_PREFIX}/uploads')
        self.assertEqual(response.status_code, 401)

    def test_listing_and_deleting(self):
        first = self.upload('Invincible 001.cbz')
        second = self.upload('Batman v3 012.cbz')

        # Unfinished uploads aren't listed
        self.api('POST', '/uploads', json={'filename': 'a.cbz', 'size': 2})

        code, response = self.api('GET', '/uploads')
        self.assertEqual(code, 200)
        self.assertEqual(
            [p['id'] for p in response['result']],
            [second['id'], first['id']]
        )

        code, _ = self.api('DELETE', f'/uploads/{first["id"]}')
        self.assertEqual(code, 200)
        code, _ = self.api('DELETE', f'/uploads/{first["id"]}')
        self.assertEqual(code, 404)

    def test_stale_uploads_are_deleted(self):
        from backend.features.file_upload import get_uploads

        def age(upload_id, seconds):
            folder = join(self.downloads, Constants.UPLOAD_FOLDER, upload_id)
            for file in listdir(folder):
                moment = time() - seconds
                utime(join(folder, file), (moment, moment))
            return folder

        hours = 3600
        recent_ready = self.upload('a.cbz')['id']
        old_ready = self.upload('b.cbz')['id']
        _, created = self.api('POST', '/uploads', json={
            'filename': 'c.cbz', 'size': 2
        })
        old_incomplete = created['result']['id']

        kept = age(recent_ready, 48 * hours)
        gone1 = age(old_ready, 8 * 24 * hours)
        gone2 = age(old_incomplete, 25 * hours)

        get_uploads()
        self.assertTrue(isdir(kept))
        self.assertFalse(exists(gone1))
        self.assertFalse(exists(gone2))

    def test_empty_download_folder_keeps_uploads(self):
        from backend.features.download_queue import DownloadHandler
        from backend.internals.settings import Settings

        with open(join(self.downloads, 'ghost.cbz'), 'wb'):
            pass
        proposal = self.upload('a.cbz')

        handler = DownloadHandler.__new__(DownloadHandler)
        handler.queue = []
        handler.settings = Settings()
        handler.empty_download_folder()

        self.assertFalse(exists(join(self.downloads, 'ghost.cbz')))
        self.assertTrue(isdir(join(
            self.downloads, Constants.UPLOAD_FOLDER, proposal['id']
        )))

    def test_wrong_extension_is_corrected(self):
        proposal = self.upload('a.cbr')
        self.assertEqual(
            sorted(listdir(join(
                self.downloads, Constants.UPLOAD_FOLDER, proposal['id']
            ))),
            ['file.cbz', 'meta.json']
        )

    def test_epub_keeps_its_extension(self):
        proposal = self.upload('Batman 012.epub')
        self.assertEqual(
            sorted(listdir(join(
                self.downloads, Constants.UPLOAD_FOLDER, proposal['id']
            ))),
            ['file.epub', 'meta.json']
        )

    def test_warnings(self):
        self.assertEqual(
            self.upload('a.cbz', b'this is no archive')['warnings'],
            ['unreadable']
        )
        self.assertEqual(self.upload('b.cbz')['warnings'], [])

        pack = BytesIO()
        with ZipFile(pack, 'w') as archive:
            archive.writestr('Batman 001.cbz', make_cbz())
            archive.writestr('Batman 002.cbz', make_cbz())
        self.assertEqual(
            self.upload('pack.zip', pack.getvalue())['warnings'],
            ['contains_issues']
        )


class Matching(UploadTestCase):
    def assertMatch(self, proposal, volume_id, issue_ids, confidence, reason):
        match = proposal['match']
        self.assertIsNotNone(match)
        self.assertEqual(
            (match['volume_id'], match['issue_ids'],
                match['confidence'], match['reason']),
            (volume_id, issue_ids, confidence, reason)
        )

    def test_filename(self):
        for filename, volume_id, issue_ids in (
            ('Invincible 050.cbz', 3, [8]),
            ('Invincible #50 (2008) (Digital) (Group).cbz', 3, [8]),
            ('invincible_050.cbz', 3, [8]),
            ('Batman v3 012.cbz', 2, [4]),
            ('Batman Volume 2 Issue 12.cbz', 1, [2]),
            ('Batman 012 (2017).cbz', 2, [4]),
            ('Batman 012 (2012).cbz', 1, [2]),
            ('Batman Annual 001 (2012).cbz', 4, [9]),
            ('The Killing Joke (1988).cbz', 5, [10]),
            ("Marvel's Voices 001 (2020).cbz", 6, [11]),
        ):
            with self.subTest(filename):
                self.assertMatch(
                    self.upload(filename), volume_id, issue_ids,
                    'high', 'title'
                )

    def test_issue_range(self):
        self.assertMatch(
            self.upload('Batman v3 012-014.cbz'),
            2, [4, 5, 6], 'high', 'title'
        )

    def test_year_of_volume_in_filename(self):
        self.assertMatch(
            self.upload('Batman (2016) Issue 012.cbz'),
            2, [4], 'high', 'title'
        )

    def test_ambiguous_series_needs_checking(self):
        proposal = self.upload('Batman 012.cbz')
        self.assertEqual(proposal['match']['confidence'], 'low')
        self.assertEqual(proposal['match']['reason'], 'ambiguous')

    def test_unknown_issue_needs_checking(self):
        self.assertMatch(
            self.upload('Invincible 999.cbz'), 3, [], 'low', 'issue_not_found'
        )

    def test_conflicting_year_needs_checking(self):
        self.assertMatch(
            self.upload('Invincible 050 (1995).cbz'), 3, [8], 'low', 'year'
        )

    def test_typo_in_title_needs_checking(self):
        self.assertMatch(
            self.upload('Invincble 050.cbz'), 3, [8], 'low', 'similar_title'
        )

    def test_unknown_series(self):
        self.assertIsNone(self.upload('Saga 001 (2012).cbz')['match'])

    def test_metadata_beats_filename(self):
        proposal = self.upload(
            'scan-0001.cbz', make_cbz(COMICINFO.format(extra=''))
        )
        self.assertMatch(proposal, 2, [4], 'high', 'title')
        self.assertEqual(proposal['detected']['source'], 'comicinfo')

    def test_comicvine_issue_id_beats_everything(self):
        proposal = self.upload(
            'Invincible 001.cbz',
            make_cbz(COMICINFO.format(
                extra='<Notes>[Issue ID 1012]</Notes>'
            ))
        )
        self.assertMatch(proposal, 1, [2], 'high', 'comicvine_id')

    def test_comicvine_volume_id(self):
        proposal = self.upload('a.cbz', make_cbz(COMICINFO.format(extra=(
            '<Web>https://comicvine.gamespot.com/batman/4050-102/</Web>'
        ))))
        self.assertMatch(proposal, 2, [4], 'high', 'comicvine_id')

    def test_unknown_comicvine_id_falls_back(self):
        proposal = self.upload('a.cbz', make_cbz(COMICINFO.format(
            extra='<Notes>[Issue ID 999999]</Notes>'
        )))
        self.assertMatch(proposal, 2, [4], 'high', 'title')

    def test_bad_metadata_falls_back_to_filename(self):
        proposal = self.upload(
            'Invincible 050.cbz', make_cbz('<<< not xml')
        )
        self.assertMatch(proposal, 3, [8], 'high', 'title')
        self.assertEqual(proposal['detected']['source'], 'filename')

    def test_annual_in_format(self):
        proposal = self.upload('a.cbz', make_cbz(
            '<ComicInfo><Series>Batman</Series><Number>1</Number>'
            '<Format>Annual</Format><Year>2012</Year></ComicInfo>'
        ))
        self.assertMatch(proposal, 4, [9], 'high', 'title')

    def test_existing_file_is_reported(self):
        first = self.upload('Invincible 050.cbz')
        code, result = self.api(
            'POST', f'/uploads/{first["id"]}/import',
            json={'volume_id': 3, 'issue_ids': [8]}
        )
        self.assertEqual(code, 201)

        second = self.upload('Invincible 050 (Better Scan).cbz')
        self.assertEqual(
            second['match']['existing_files'],
            [result['result']['filepath']]
        )

    def test_preview_finds_issue_in_chosen_volume(self):
        proposal = self.upload('Batman 012.cbz')
        code, response = self.api(
            'POST', f'/uploads/{proposal["id"]}/match', json={'volume_id': 1}
        )
        self.assertEqual(code, 200)
        self.assertEqual(response['result']['issue_ids'], [2])
        self.assertEqual(response['result']['confidence'], 'manual')

    def test_preview_of_chosen_issues(self):
        proposal = self.upload('Batman 012.cbz')
        code, response = self.api(
            'POST', f'/uploads/{proposal["id"]}/match',
            json={'volume_id': 2, 'issue_ids': [5, 6]}
        )
        self.assertEqual(code, 200)
        self.assertEqual(response['result']['issue_numbers'], ['13', '14'])
        self.assertIn('013 - 014', response['result']['destination'])

    def test_preview_refuses_issue_of_other_volume(self):
        proposal = self.upload('Batman 012.cbz')
        code, _ = self.api(
            'POST', f'/uploads/{proposal["id"]}/match',
            json={'volume_id': 2, 'issue_ids': [1]}
        )
        self.assertEqual(code, 400)

    def test_preview_of_unknown_volume(self):
        proposal = self.upload('Batman 012.cbz')
        code, _ = self.api(
            'POST', f'/uploads/{proposal["id"]}/match',
            json={'volume_id': 999}
        )
        self.assertEqual(code, 404)


class Importing(UploadTestCase):
    def import_upload(self, proposal, volume_id, issue_ids):
        return self.api(
            'POST', f'/uploads/{proposal["id"]}/import',
            json={'volume_id': volume_id, 'issue_ids': issue_ids}
        )

    def bindings(self):
        from backend.internals.db import get_db

        return [tuple(row) for row in get_db().execute("""
            SELECT f.filepath, if.issue_id, if.forced
            FROM files f
            INNER JOIN issues_files if
            ON f.id = if.file_id
            ORDER BY if.issue_id;
        """).fetchall()]

    def test_moves_renames_and_binds(self):
        content = make_cbz() * 2
        proposal = self.upload('some random name (2008) Invincible 050.cbz', content)

        # The name of the upload said nothing useful for this choice,
        # the choice of the user is what counts.
        code, response = self.import_upload(proposal, 3, [8])
        self.assertEqual(code, 201)

        filepath = response['result']['filepath']
        self.assertEqual(
            filepath,
            join(
                self.root, 'Invincible 2003',
                'Invincible (2003) Volume 01 Issue 050.cbz'
            )
        )
        with open(filepath, 'rb') as f:
            self.assertEqual(f.read(), content)

        self.assertEqual(self.bindings(), [(filepath, 8, 0)])
        self.assertFalse(exists(join(
            self.downloads, Constants.UPLOAD_FOLDER, proposal['id']
        )))

    def test_matches_name_of_existing_files(self):
        first = self.upload('Invincible 001.cbz')
        second = self.upload('invincible_050_c2c_scan.cbz')
        _, one = self.import_upload(first, 3, [7])
        _, two = self.import_upload(second, 3, [8])
        self.assertEqual(
            dirname(one['result']['filepath']),
            dirname(two['result']['filepath'])
        )
        self.assertEqual(
            basename(one['result']['filepath']).replace('001', '050'),
            basename(two['result']['filepath'])
        )

    def test_apostrophe_in_title(self):
        proposal = self.upload("Marvel's Voices 001.cbz")
        code, response = self.import_upload(proposal, 6, [11])
        self.assertEqual(code, 201)
        self.assertTrue(exists(response['result']['filepath']))

    def test_issue_range(self):
        proposal = self.upload('Batman v3 012-014.cbz')
        code, response = self.import_upload(proposal, 2, [4, 5, 6])
        self.assertEqual(code, 201)

        filepath = response['result']['filepath']
        self.assertIn('Issue 012 - 014', filepath)
        self.assertEqual(
            self.bindings(),
            [(filepath, 4, 0), (filepath, 5, 0), (filepath, 6, 0)]
        )

    def test_special_version(self):
        proposal = self.upload('The Killing Joke (1988).cbz')
        code, response = self.import_upload(proposal, 5, [10])
        self.assertEqual(code, 201)
        self.assertEqual(
            self.bindings(),
            [(response['result']['filepath'], 10, 0)]
        )

    def test_existing_file_is_not_overwritten(self):
        first = self.upload('a.cbz', make_cbz() + b'first')
        second = self.upload('b.cbz', make_cbz() + b'second')

        _, one = self.import_upload(first, 3, [8])
        code, two = self.import_upload(second, 3, [8])
        self.assertEqual(code, 201)

        path_one = one['result']['filepath']
        path_two = two['result']['filepath']
        self.assertNotEqual(path_one, path_two)
        with open(path_one, 'rb') as f:
            self.assertTrue(f.read().endswith(b'first'))
        with open(path_two, 'rb') as f:
            self.assertTrue(f.read().endswith(b'second'))
        self.assertEqual(
            sorted(self.bindings()),
            sorted([(path_one, 8, 0), (path_two, 8, 0)])
        )

    def test_binding_is_forced_when_name_is_not_enough(self):
        from backend.internals.settings import Settings

        settings = Settings()
        original = settings.sv.file_naming_empty
        settings.update({'file_naming_empty': '{series_name} {issue_number}'})
        self.addCleanup(
            lambda: settings.update({'file_naming_empty': original})
        )

        # Name will be 'Batman 012.cbz', and the scanner can't know which
        # volume that is for, so it won't match the file by itself
        proposal = self.upload('x.cbz')
        code, response = self.import_upload(proposal, 2, [4])
        self.assertEqual(code, 201)

        filepath = response['result']['filepath']
        self.assertEqual(basename(filepath), 'Batman 012.cbz')
        self.assertEqual(self.bindings(), [(filepath, 4, 1)])

    def test_adds_to_history_and_sends_webhook(self):
        proposal = self.upload('Invincible 050.cbz')
        self.import_upload(proposal, 3, [8])

        code, history = self.api('GET', '/activity/history')
        self.assertEqual(code, 200)
        entry = history['result'][0]
        self.assertEqual(entry['web_title'], 'Invincible 050.cbz')
        self.assertEqual(entry['source'], 'Upload')
        self.assertEqual(entry['volume_id'], 3)
        self.assertEqual(entry['issue_id'], 8)
        self.assertEqual(entry['web_link'], '/volumes/3')
        self.assertTrue(entry['success'])

        self.webhook_mock.assert_called_once_with(
            3, [8], 'Invincible 050.cbz', proposal['size']
        )

    def test_webhook_has_the_format_of_a_download(self):
        from backend.features.webhooks import upload_imported

        self.import_upload(self.upload('Invincible 050.cbz'), 3, [8])

        with patch('backend.features.webhooks.send_event') as send_event:
            upload_imported(3, [8], 'Invincible 050.cbz', 10)

        event, data = send_event.call_args.args
        self.assertEqual(event, 'download.imported')
        self.assertEqual(data['volume']['volume_id'], 3)
        self.assertEqual(len(data['issues']), 1)
        self.assertEqual(data['issues'][0]['comicvine_id'], 3050)
        self.assertTrue(data['issues'][0]['downloaded'])
        self.assertEqual(data['download'], {
            'title': 'Invincible 050.cbz',
            'source': 'Upload',
            'size': 10
        })

    def test_refused_input_keeps_the_upload(self):
        proposal = self.upload('Invincible 050.cbz')
        for body, expected_code in (
            ({'volume_id': 3}, 400),
            ({'volume_id': 3, 'issue_ids': []}, 400),
            ({'volume_id': 3, 'issue_ids': ['8']}, 400),
            ({'volume_id': 3, 'issue_ids': [1]}, 400),
            ({'volume_id': 3, 'issue_ids': [8, 8000]}, 400),
            ({'volume_id': '3', 'issue_ids': [8]}, 400),
            ({'volume_id': 999, 'issue_ids': [8]}, 404),
            ({'issue_ids': [8]}, 400)
        ):
            code, _ = self.api(
                'POST', f'/uploads/{proposal["id"]}/import', json=body
            )
            self.assertEqual(code, expected_code, body)

        code, response = self.api('GET', '/uploads')
        self.assertEqual(len(response['result']), 1)
        self.assertEqual(listdir(self.root), [])

    def test_unfinished_upload_cant_be_imported(self):
        _, created = self.api('POST', '/uploads', json={
            'filename': 'a.cbz', 'size': 2
        })
        code, _ = self.api(
            'POST', f'/uploads/{created["result"]["id"]}/import',
            json={'volume_id': 3, 'issue_ids': [8]}
        )
        self.assertEqual(code, 400)

    def test_cant_be_imported_twice(self):
        proposal = self.upload('Invincible 050.cbz')
        self.assertEqual(self.import_upload(proposal, 3, [8])[0], 201)
        self.assertEqual(self.import_upload(proposal, 3, [8])[0], 404)


if __name__ == '__main__':
    unittest.main()
