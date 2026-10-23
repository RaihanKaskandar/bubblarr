import logging
import unittest
from os.path import join
from tempfile import mkdtemp

from Tbackend.implementations.torznab import DatabaseTestCase

from backend.base.logging import MEMORY_LOG_HANDLER, MemoryLogHandler


def record(level: int, message: str) -> logging.LogRecord:
    return logging.LogRecord(
        'Bubblarr', level, '/app/search.py', 12, message, None, None
    )


class MemoryHandler(unittest.TestCase):
    def test_entries_and_filters(self):
        handler = MemoryLogHandler()
        handler.emit(record(logging.DEBUG, 'Fetching volume data'))
        handler.emit(record(logging.INFO, 'Starting auto search for Batman'))
        handler.emit(record(logging.WARNING, 'No seeders for batman #19'))

        self.assertEqual(
            [e['id'] for e in handler.get_entries()], [1, 2, 3]
        )
        self.assertEqual(
            [e['level'] for e in handler.get_entries(level=logging.INFO)],
            ['INFO', 'WARNING']
        )
        self.assertEqual(
            [e['id'] for e in handler.get_entries(search='BATMAN')], [2, 3]
        )
        self.assertEqual([e['id'] for e in handler.get_entries(after=2)], [3])
        # Newest ones when limited
        self.assertEqual([e['id'] for e in handler.get_entries(limit=1)], [3])
        self.assertEqual(handler.get_entries()[1]['source'], 'search.pyL12')

    def test_load_file(self):
        folder = mkdtemp()
        path = join(folder, 'log.txt')
        with open(path, 'w') as f:
            f.write(
                '2026-10-18T01:56:04+0200 | MainProcess | Thread-3 | '
                'tasks.pyL80 | INFO | Added task: Auto Search (1)\n'
                '2026-10-18T01:56:05+0200 | MainProcess | Thread-3 | '
                'tasks.pyL90 | ERROR | Task failed\n'
                'Traceback (most recent call last):\n'
                '  File "x.py", line 1\n'
            )
        handler = MemoryLogHandler()
        handler.load_file(path)
        entries = handler.get_entries()
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]['time'], '2026-10-18T01:56:04+02:00')
        self.assertEqual(entries[0]['thread'], 'Thread-3')
        self.assertEqual(entries[1]['level'], 'ERROR')
        self.assertTrue(entries[1]['message'].endswith('  File "x.py", line 1'))

    def test_max_entries(self):
        handler = MemoryLogHandler()
        handler.MAX_ENTRIES = 3
        from collections import deque
        handler.entries = deque(maxlen=3)
        for i in range(5):
            handler.emit(record(logging.INFO, f'message {i}'))
        self.assertEqual([e['id'] for e in handler.get_entries()], [3, 4, 5])


class LogApi(DatabaseTestCase):
    def test_entries_endpoint(self):
        from backend.internals.settings import Settings

        MEMORY_LOG_HANDLER.emit(record(
            logging.INFO,
            'Searching https://idx/api?t=search&apikey=abc123&q=batman '
            f'with key {Settings().sv.api_key}'
        ))
        client = self.app.test_client()
        response = client.get(
            '/api/system/logs/entries',
            headers={'X-Api-Key': self.api_key},
            query_string={'search': 'batman', 'level': 'info'}
        )
        self.assertEqual(response.status_code, 200)
        result = response.get_json()['result']
        self.assertEqual(result['instance'], MEMORY_LOG_HANDLER.instance)
        message = result['entries'][-1]['message']
        self.assertNotIn('abc123', message)
        self.assertNotIn(self.api_key, message)
        self.assertIn('apikey=[REDACTED]', message)

        # Nothing newer than the last ID
        response = client.get(
            '/api/system/logs/entries',
            headers={'X-Api-Key': self.api_key},
            query_string={'after': result['last_id']}
        )
        self.assertEqual(response.get_json()['result']['entries'], [])

        self.assertEqual(client.get(
            '/api/system/logs/entries',
            headers={'X-Api-Key': self.api_key},
            query_string={'level': 'loud'}
        ).status_code, 400)


if __name__ == '__main__':
    unittest.main()
