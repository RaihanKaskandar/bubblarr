from Tbackend.implementations.torznab import DatabaseTestCase


class Calendar(DatabaseTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from backend.internals.db import get_db

        cursor = get_db()
        cursor.execute(
            "INSERT INTO root_folders(id, folder) VALUES (1, '/comics/');"
        )
        cursor.executemany("""
            INSERT INTO volumes(
                id, comicvine_id, title, year, volume_number, site_url,
                monitored, root_folder, folder
            ) VALUES (?, ?, ?, ?, 1, '', ?, 1, ?);
            """,
            (
                (1, 101, "Saga", 2012, True, "/comics/Saga"),
                (2, 102, "Nightwing", 2016, False, "/comics/Nightwing"),
            )
        )
        cursor.executemany("""
            INSERT INTO issues(
                id, volume_id, comicvine_id, issue_number,
                calculated_issue_number, title, date, description, monitored
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?);
            """,
            (
                (1, 1, 1001, "1", 1.0, "Chapter One", "2026-10-07", True),
                (2, 1, 1002, "2", 2.0, None, "2026-10-14", True),
                (3, 1, 1003, "3", 3.0, None, "2026-10-14", False),
                (4, 2, 1004, "1", 1.0, None, "2026-10-14", True),
                (5, 1, 1005, "4", 4.0, None, "2026-11-04", True),
                (6, 1, 1006, "5", 5.0, None, None, True),
            )
        )
        cursor.execute(
            "INSERT INTO files(id, filepath, size) VALUES (1, '/comics/Saga/1.cbz', 10);"
        )
        cursor.execute(
            "INSERT INTO issues_files(file_id, issue_id) VALUES (1, 1);"
        )
        return

    def _get(self, **params):
        response = self.app.test_client().get(
            "/api/calendar",
            query_string={"api_key": self.api_key, **params}
        )
        return response.status_code, response.get_json()

    def test_range(self):
        code, json = self._get(start="2026-10-01", end="2026-10-31")
        self.assertEqual(code, 200)
        result = json["result"]
        # Sorted on date, then volume title, then issue number
        self.assertEqual(
            [(r["date"], r["volume_title"], r["issue_number"]) for r in result],
            [
                ("2026-10-07", "Saga", "1"),
                ("2026-10-14", "Nightwing", "1"),
                ("2026-10-14", "Saga", "2"),
                ("2026-10-14", "Saga", "3"),
            ]
        )

        by_id = {r["issue_id"]: r for r in result}
        self.assertTrue(by_id[1]["downloaded"])
        self.assertFalse(by_id[2]["downloaded"])
        self.assertTrue(by_id[2]["monitored"])
        # Issue unmonitored
        self.assertFalse(by_id[3]["monitored"])
        # Volume unmonitored
        self.assertFalse(by_id[4]["monitored"])
        self.assertEqual(by_id[1]["title"], "Chapter One")
        self.assertEqual(by_id[1]["volume_year"], 2012)

    def test_inclusive_end_and_monitored_only(self):
        code, json = self._get(
            start="2026-10-14", end="2026-11-04", monitored_only="true"
        )
        self.assertEqual(code, 200)
        self.assertEqual(
            [r["issue_id"] for r in json["result"]], [2, 5]
        )

    def test_invalid(self):
        for params in (
            {"start": "2026-10-01"},
            {"start": "2026-13-01", "end": "2026-10-31"},
            {"start": "2026-10-31", "end": "2026-10-01"},
            {"start": "2026-01-01", "end": "2027-06-01"},
        ):
            code, _ = self._get(**params)
            self.assertEqual(code, 400, params)

    def test_page(self):
        response = self.app.test_client().get("/calendar")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'id="calendar-grid"', response.data)


class StoreDates(DatabaseTestCase):
    def _format(self, date_type, cover_date, store_date):
        from backend.implementations.comicvine import ComicVine

        cv = ComicVine.__new__(ComicVine)
        cv.date_type = date_type
        return cv._ComicVine__format_issue_output({
            "id": 1, "volume": {"id": 2}, "issue_number": "1",
            "name": "Test", "description": None,
            "cover_date": cover_date, "store_date": store_date
        })["date"]

    def test_default_is_store_date(self):
        from backend.base.definitions import DateType
        from backend.internals.settings import SettingsValues
        self.assertEqual(SettingsValues.date_type, DateType.STORE_DATE)

    def test_fallback_to_other_date(self):
        self.assertEqual(
            self._format("store_date", "2026-12-01", "2026-10-14"),
            "2026-10-14"
        )
        self.assertEqual(
            self._format("store_date", "1988-07-01", None), "1988-07-01"
        )
        self.assertEqual(
            self._format("cover_date", None, "2026-10-14"), "2026-10-14"
        )
        self.assertIsNone(self._format("store_date", None, None))

    def test_changing_date_type_refreshes_all(self):
        from unittest.mock import patch

        from backend.internals.settings import Settings

        current = Settings().sv.date_type.value
        other = "cover_date" if current == "store_date" else "store_date"
        client = self.app.test_client()
        with patch("frontend.api.TaskHandler") as handler:
            # Unchanged value: no refresh
            client.put(
                "/api/settings", query_string={"api_key": self.api_key},
                json={"date_type": current}
            )
            handler.return_value.add.assert_not_called()

            response = client.put(
                "/api/settings", query_string={"api_key": self.api_key},
                json={"date_type": other}
            )
            self.assertEqual(response.status_code, 200)
            handler.return_value.add.assert_called_once()
            task = handler.return_value.add.call_args[0][0]
            self.assertEqual(type(task).__name__, "UpdateAll")
            self.assertFalse(task.allow_skipping)
