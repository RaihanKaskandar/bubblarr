import unittest
from datetime import date, timedelta
from hashlib import sha256
from hmac import new as hmac_new
from json import loads
from os.path import join
from unittest.mock import MagicMock, patch

from Tbackend.implementations.torznab import DatabaseTestCase

PAST = (date.today() - timedelta(days=30)).isoformat()
FUTURE = (date.today() + timedelta(days=30)).isoformat()


class RequestApi(DatabaseTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from backend.internals.db import get_db

        # Inside the temporary folder, because adding a volume creates the
        # root folder and tests don't run as root.
        root = join(cls.db_folder, 'comics', '')
        cursor = get_db()
        cursor.execute(
            "INSERT INTO root_folders(id, folder) VALUES (1, ?);", (root,)
        )
        cursor.execute("""
            INSERT INTO volumes(
                id, comicvine_id, title, year, publisher, volume_number,
                site_url, monitored, root_folder, folder
            ) VALUES (
                1, 101, 'Absolute Batman', 2024, 'DC Comics', 1, '', 0, 1, ?
            );
        """, (join(root, 'Absolute Batman'),))
        cursor.executemany("""
            INSERT INTO issues(
                id, volume_id, comicvine_id, issue_number,
                calculated_issue_number, date, description, monitored
            ) VALUES (?, 1, ?, ?, ?, ?, '', ?);
            """,
            (
                (1, 1001, "1", 1.0, PAST, 1),
                (2, 1002, "2", 2.0, PAST, 0),
                (3, 1003, "3", 3.0, PAST, 0),
                (4, 1004, "4", 4.0, FUTURE, 0),
            )
        )
        cursor.execute(
            "INSERT INTO files(id, filepath, size) VALUES (1, '/c/1.cbz', 1);"
        )
        cursor.execute(
            "INSERT INTO issues_files(file_id, issue_id) VALUES (1, 1);"
        )
        cursor.connection.commit()
        return

    def setUp(self):
        self.downloading = patch(
            'backend.features.comic_requests._downloading_issues',
            return_value={}
        )
        self.downloading.start()
        return

    def tearDown(self):
        self.downloading.stop()
        return

    def get(self, path, **kwargs):
        return self.app.test_client().get(
            '/api' + path, headers={'X-Api-Key': self.api_key}, **kwargs
        )

    def test_1_status_unmonitored_volume(self):
        from backend.features.comic_requests import volume_status

        status = volume_status(1, include_issues=True)
        self.assertEqual(status['status'], 'available')
        self.assertEqual(status['issues'], {
            'total': 4, 'downloaded': 1, 'downloading': 0,
            'missing': 0, 'upcoming': 1, 'unmonitored': 2
        })
        self.assertEqual(
            [i['status'] for i in status['issue_list']],
            ['downloaded', 'unmonitored', 'unmonitored', 'upcoming']
        )

    def test_2_status_by_comicvine_ids(self):
        response = self.get('/lookup/status?comicvine_ids=101,999')
        self.assertEqual(response.status_code, 200)
        result = response.get_json()['result']
        self.assertEqual(result['101']['volume_id'], 1)
        self.assertEqual(result['999']['status'], 'not_added')

        self.assertEqual(
            self.get('/lookup/status?comicvine_ids=abc').status_code, 400
        )

    def test_3_header_and_query_auth(self):
        client = self.app.test_client()
        self.assertEqual(client.get('/api/volumes/1/status').status_code, 401)
        self.assertEqual(client.get(
            '/api/volumes/1/status', headers={'X-Api-Key': 'wrong'}
        ).status_code, 401)
        self.assertEqual(client.get(
            '/api/volumes/1/status', query_string={'api_key': self.api_key}
        ).status_code, 200)
        self.assertEqual(self.get('/volumes/1/status').status_code, 200)

    def test_4_request_specific_issues(self):
        from backend.features.tasks import TaskHandler

        with patch.object(TaskHandler, 'add') as add_task:
            response = self.app.test_client().post(
                '/api/request',
                headers={'X-Api-Key': self.api_key},
                json={'comicvine_id': 101, 'issues': [1002]}
            )
        self.assertEqual(response.status_code, 200)
        result = response.get_json()['result']
        self.assertFalse(result['added'])
        self.assertTrue(result['searching'])
        self.assertTrue(result['monitored'])
        self.assertEqual(result['status'], 'partially_available')
        self.assertEqual(result['issues']['missing'], 1)
        add_task.assert_called_once()
        self.assertEqual(add_task.call_args.args[0].volume_id, 1)

    def test_5_request_unknown_issue(self):
        response = self.app.test_client().post(
            '/api/request',
            headers={'X-Api-Key': self.api_key},
            json={'comicvine_id': 101, 'issues': [5555]}
        )
        self.assertEqual(response.status_code, 400)

        response = self.app.test_client().post(
            '/api/request',
            headers={'X-Api-Key': self.api_key},
            json={'comicvine_id': 101, 'issues': 'everything'}
        )
        self.assertEqual(response.status_code, 400)

    def test_6_request_new_volume(self):
        from backend.base.definitions import MonitorScheme
        from backend.implementations.volumes import Library

        with patch.object(Library, 'add', return_value=1) as add:
            from backend.features.comic_requests import request_volume
            with patch('backend.features.tasks.TaskHandler.add'):
                with patch(
                    'backend.features.comic_requests._volume_ids_for_cv_ids',
                    side_effect=[{}]
                ):
                    result = request_volume(202, issues='future')

        self.assertTrue(result['added'])
        args = add.call_args
        self.assertEqual(args.args[:2], (202, 1))
        self.assertEqual(args.kwargs['monitor_scheme'], MonitorScheme.NONE)
        self.assertTrue(args.kwargs['monitor_new_issues'])
        self.assertFalse(args.kwargs['auto_search'])

    def test_7_lookup_search(self):
        async def search(query):
            return [
                {'comicvine_id': 101, 'title': 'Absolute Batman',
                 'cover': b'x', 'already_added': 1},
                {'comicvine_id': 303, 'title': 'Absolute Superman',
                 'cover': None, 'already_added': None},
            ]

        cv = MagicMock()
        cv.return_value.search_volumes = search
        with patch('backend.features.comic_requests.ComicVine', cv):
            result = self.get('/lookup?query=absolute').get_json()['result']
        self.assertNotIn('cover', result[0])
        self.assertEqual(result[0]['library']['volume_id'], 1)
        self.assertEqual(result[1]['library']['status'], 'not_added')

    def test_8_releases(self):
        from backend.features import comic_requests

        def volume(cv_id, title, translated=False):
            return {
                'comicvine_id': cv_id, 'title': title, 'year': 2024,
                'volume_number': 1, 'publisher': 'DC Comics',
                'cover_link': 'https://cv/cover.jpg', 'issue_count': 20,
                'translated': translated, 'cover': None
            }

        calls = []

        async def fetch_releases(start, end, limit):
            calls.append((start, end))
            return [
                (volume(303, 'Absolute Superman'),
                 [{'comicvine_id': 9, 'issue_number': '12', 'date': '2026-10-21'}]),
                (volume(101, 'Absolute Batman'), [
                    {'comicvine_id': 8, 'issue_number': '26', 'date': '2026-10-21'},
                    {'comicvine_id': 7, 'issue_number': '25', 'date': '2026-10-21'},
                ]),
                (volume(404, 'Batman (Italian edition)', translated=True),
                 [{'comicvine_id': 6, 'issue_number': '1', 'date': '2026-10-21'}]),
            ]

        comic_requests._releases_cache.clear()
        cv = MagicMock()
        cv.return_value.fetch_releases = fetch_releases
        with patch('backend.features.comic_requests.ComicVine', cv):
            result = self.get(
                '/lookup/releases?start=2026-10-19&end=2026-10-25'
            ).get_json()['result']
            # Cached the second time
            self.get('/lookup/releases?start=2026-10-19&end=2026-10-25')

        self.assertEqual(calls, [('2026-10-19', '2026-10-25')])
        # Foreign editions are left out, most issues first
        self.assertEqual([v['comicvine_id'] for v in result], [101, 303])
        self.assertEqual(result[0]['library']['volume_id'], 1)
        self.assertEqual(result[1]['library']['status'], 'not_added')
        self.assertEqual(len(result[0]['issues']), 2)

        self.assertEqual(
            self.get('/lookup/releases?start=2026-10-19&end=2026-12-25')
            .status_code, 400
        )


class Webhooks(DatabaseTestCase):
    def test_signature_and_payload(self):
        from backend.features import webhooks

        response = MagicMock(ok=True)
        with patch.object(webhooks, 'post', return_value=response) as post:
            result = webhooks.test_webhooks(
                ['https://example.com/hook'], 'secret'
            )
        self.assertEqual(result[0]['success'], True)

        body = post.call_args.kwargs['data']
        headers = post.call_args.kwargs['headers']
        self.assertEqual(loads(body)['event'], 'test')
        self.assertEqual(headers['X-Bubblarr-Event'], 'test')
        self.assertEqual(
            headers['X-Bubblarr-Signature'],
            'sha256=' + hmac_new(b'secret', body, sha256).hexdigest()
        )

    def test_failure_reported(self):
        from backend.features import webhooks

        with patch.object(webhooks, 'post', side_effect=ConnectionError):
            result = webhooks.test_webhooks(['https://example.com/hook'], '')
        self.assertEqual(result[0], {
            'url': 'https://example.com/hook',
            'success': False,
            'error': 'ConnectionError'
        })

    def test_settings(self):
        from backend.base.custom_exceptions import InvalidKeyValue
        from backend.internals.settings import Settings

        Settings().update({
            'webhook_urls': ['https://a.example/hook', ' '],
            'webhook_secret': 'secret'
        })
        self.assertEqual(
            list(Settings().sv.webhook_urls), ['https://a.example/hook']
        )
        self.assertNotEqual(
            Settings().sv.todict()['webhook_secret'], 'secret'
        )
        with self.assertRaises(InvalidKeyValue):
            Settings().update({'webhook_urls': ['ftp://a.example']})


if __name__ == '__main__':
    unittest.main()
