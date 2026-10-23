import unittest
from datetime import date
from os.path import dirname, join
from unittest.mock import patch

from Tbackend.implementations.torznab import DatabaseTestCase

from backend.implementations.comicgeeks import (normalise_issue_number,
                                                normalise_title,
                                                parse_issue_results,
                                                parse_series_results,
                                                pick_series)

FIXTURES = join(dirname(__file__), "fixtures")


def fixture(name: str) -> str:
    with open(join(FIXTURES, name)) as f:
        return f.read()


class Parsing(unittest.TestCase):
    "Parsing real responses of League of Comic Geeks (saved Oct 2026)"

    def test_series_search(self):
        series = parse_series_results(fixture("locg_series_search.html"))
        by_id = {s["id"]: s for s in series}
        self.assertEqual(by_id[178012], {
            "id": 178012, "title": "Absolute Batman",
            "publisher": "DC Comics", "start_year": 2024, "end_year": None
        })
        self.assertEqual(by_id[191129]["publisher"], "Panini Comics")

    def test_pick_series(self):
        series = parse_series_results(fixture("locg_series_search.html"))
        # Not the Panini edition, Ashcan or DC Go! Edition
        self.assertEqual(
            pick_series(series, "Absolute Batman", 2024, "DC Comics")["id"],
            178012
        )
        # Start year may differ by one
        self.assertEqual(
            pick_series(series, "Absolute Batman", 2025, "DC")["id"], 178012
        )
        self.assertIsNone(pick_series(series, "Absolute Batman", 2020, "DC"))
        self.assertIsNone(
            pick_series(series, "Absolute Batman", 2024, "Image")
        )
        self.assertIsNone(pick_series(series, "Absolute Superman", 2024, None))

    def test_series_issues(self):
        issues = parse_issue_results(fixture("locg_series_issues.html"))
        # The variant cover of #24 is skipped
        self.assertEqual(
            [(i["comic_id"], i["issue_number"], i["date"]) for i in issues],
            [
                (1337824, "26", None),  # TBD
                (3952461, "24", "2026-09-23"),
                (1669159, "23", "2026-08-12"),
            ]
        )
        self.assertEqual(issues[1]["series_title"], "Absolute Batman")
        self.assertEqual(
            issues[1]["url"],
            "https://leagueofcomicgeeks.com/comic/3952461/absolute-batman-24"
        )

    def test_normalising(self):
        self.assertEqual(normalise_issue_number("001"), "1")
        self.assertEqual(normalise_issue_number("1"), "1")
        self.assertEqual(normalise_issue_number("0"), "0")
        self.assertEqual(normalise_issue_number("½"), "½")
        self.assertEqual(normalise_issue_number("12.1"), "12.1")
        self.assertEqual(
            normalise_title("Batman & Robin"), normalise_title("Batman and Robin")
        )
        self.assertEqual(
            normalise_title("Absolute Batman: Ark M Special"),
            "absolutebatmanarkmspecial"
        )


class Sync(DatabaseTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from backend.internals.db import get_db
        from backend.internals.settings import Settings

        Settings().update({
            "comicgeeks_username": "reader",
            "comicgeeks_password": "secret"
        })

        cursor = get_db()
        cursor.execute(
            "INSERT INTO root_folders(id, folder) VALUES (1, '/comics/');"
        )
        cursor.execute("""
            INSERT INTO volumes(
                id, comicvine_id, title, year, publisher, volume_number,
                site_url, monitored, root_folder, folder
            ) VALUES (
                1, 101, 'Absolute Batman', 2024, 'DC Comics', 1, '', 1, 1,
                '/comics/Absolute Batman'
            );
        """)
        cursor.executemany("""
            INSERT INTO issues(
                id, volume_id, comicvine_id, issue_number,
                calculated_issue_number, date, description, monitored
            ) VALUES (?, 1, ?, ?, ?, ?, '', 1);
            """,
            (
                (1, 1001, "23", 23.0, "2026-08-12"),
                (2, 1002, "24", 24.0, "2026-09-23"),
                (3, 1003, "26", 26.0, None),
            )
        )
        cursor.execute(
            "INSERT INTO files(id, filepath, size) VALUES (1, '/c/23.cbz', 1);"
        )
        cursor.execute(
            "INSERT INTO issues_files(file_id, issue_id) VALUES (1, 1);"
        )
        return

    def setUp(self):
        from backend.features import comicgeeks_sync
        from backend.implementations.comicgeeks import ComicGeeks

        comicgeeks_sync.clear_cache()
        self.patches = [
            patch.object(
                ComicGeeks, "search_series",
                return_value=parse_series_results(
                    fixture("locg_series_search.html")
                )
            ),
            patch.object(
                ComicGeeks, "get_series_issues",
                return_value=parse_issue_results(
                    fixture("locg_series_issues.html")
                )
            ),
            patch.object(ComicGeeks, "set_collected"),
            patch.object(ComicGeeks, "get_pull_list"),
        ]
        self.mocks = [p.start() for p in self.patches]
        return

    def tearDown(self):
        for p in self.patches:
            p.stop()
        return

    def test_collection_sync(self):
        from backend.features.comicgeeks_sync import (match_volume,
                                                      sync_volume_collection)
        from backend.internals.db import get_db

        self.assertEqual(match_volume(1), 178012)
        mapping = dict(get_db().execute(
            "SELECT issue_id, comic_id FROM comicgeeks_issues;"
        ).fetchall())
        self.assertEqual(mapping, {1: 1669159, 2: 3952461, 3: 1337824})

        set_collected = self.mocks[2]
        # Only #23 is downloaded
        self.assertEqual(sync_volume_collection(1), 1)
        set_collected.assert_called_once_with(1669159, True)

        # Already marked, so not again
        set_collected.reset_mock()
        self.assertEqual(sync_volume_collection(1), 0)
        set_collected.assert_not_called()

    def test_pull_list(self):
        from backend.features.comicgeeks_sync import get_pull_list, match_volume

        match_volume(1)
        self.mocks[3].return_value = [
            {
                "comic_id": 3952461, "title": "Absolute Batman #24",
                "series_title": "Absolute Batman", "issue_number": "24",
                "date": "2026-09-23", "publisher": "DC Comics",
                "url": "https://leagueofcomicgeeks.com/comic/3952461/x"
            },
            {
                "comic_id": 999, "title": "Saga #70",
                "series_title": "Saga", "issue_number": "70",
                "date": "2026-09-23", "publisher": "Image Comics",
                "url": "https://leagueofcomicgeeks.com/comic/999/saga-70"
            },
            {
                # Outside of the requested range
                "comic_id": 998, "title": "Saga #71",
                "series_title": "Saga", "issue_number": "71",
                "date": "2026-10-28", "publisher": "Image Comics",
                "url": "https://leagueofcomicgeeks.com/comic/998/saga-71"
            },
        ]
        result = get_pull_list(date(2026, 9, 21), date(2026, 9, 27))
        self.assertEqual(
            [(r["comic_id"], r["issue_id"], r["volume_id"]) for r in result],
            [(3952461, 2, 1), (999, None, None)]
        )
        # LoCG weeks start on Sunday, so Mon 21 - Sun 27 spans two weeks
        self.assertEqual(
            [c.args[0] for c in self.mocks[3].call_args_list],
            [date(2026, 9, 20), date(2026, 9, 27)]
        )

        # Cached
        get_pull_list(date(2026, 9, 21), date(2026, 9, 27))
        self.assertEqual(self.mocks[3].call_count, 2)

    def test_password_hidden(self):
        response = self.app.test_client().get(
            "/api/settings", query_string={"api_key": self.api_key}
        ).get_json()["result"]
        self.assertEqual(response["comicgeeks_username"], "reader")
        self.assertNotEqual(response["comicgeeks_password"], "secret")

    def test_pull_list_api_never_fails(self):
        from backend.base.custom_exceptions import CredentialInvalid

        self.mocks[3].side_effect = CredentialInvalid
        response = self.app.test_client().get(
            "/api/calendar/pull-list",
            query_string={
                "api_key": self.api_key,
                "start": "2026-09-21", "end": "2026-09-27"
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["result"], {
            "items": [], "error": "invalid_credentials"
        })
