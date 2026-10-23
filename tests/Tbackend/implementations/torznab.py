import unittest
from datetime import datetime
from hashlib import sha1
from shutil import rmtree
from tempfile import mkdtemp
from types import SimpleNamespace
from unittest.mock import patch

from bencoding import bencode

from backend.base.custom_exceptions import InvalidKeyValue
from backend.base.definitions import (DownloadType, IndexerClientField,
                                      QueryKeys, SearchAction, SpecialVersion)
from backend.base.helpers import Singleton
from backend.implementations.download_preppers.torrent.Torznab import (
    set_magnet_name, torrent_to_magnet)
from backend.implementations.download_clients.Torrent import get_magnet_name
from backend.implementations.indexer_client_manager import (
    IndexerClients, _validate_indexer_data)
from backend.implementations.indexer_clients.torrent.Torznab import (
    TorznabError, TorznabIndexer, get_api_url, normalise_release_title,
    parse_torznab_feed)
from backend.implementations.query_builders.Torrent import TorrentQueryBuilder

ICF = IndexerClientField

JACKETT_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="1.0" xmlns:atom="http://www.w3.org/2005/Atom"
    xmlns:torznab="http://torznab.com/schemas/2015/feed">
  <channel>
    <title>AggregateSearch</title>
    <item>
      <title>Batman.001.2016.Digital.Zone-Empire.cbr</title>
      <guid>https://tracker.example/details/1</guid>
      <comments>https://tracker.example/details/1</comments>
      <pubDate>Wed, 15 Oct 2026 10:00:00 +0000</pubDate>
      <size>45000000</size>
      <link>http://jackett:9117/dl/tracker/?jackett_apikey=x&amp;path=1</link>
      <enclosure url="http://jackett:9117/dl/tracker/?jackett_apikey=x&amp;path=1"
        length="45000000" type="application/x-bittorrent" />
      <torznab:attr name="seeders" value="12" />
      <torznab:attr name="peers" value="15" />
      <torznab:attr name="magneturl" value="magnet:?xt=urn:btih:abc&amp;dn=Batman" />
    </item>
    <item>
      <title>Batman (2016) #1-50 Complete</title>
      <pubDate>Mon, 01 Jan 2024 10:00:00 +0000</pubDate>
      <torznab:attr name="seeders" value="0" />
      <torznab:attr name="size" value="1500000000" />
      <torznab:attr name="magneturl" value="magnet:?xt=urn:btih:def" />
    </item>
  </channel>
</rss>
"""

PROWLARR_FEED = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"
    xmlns:torznab="http://torznab.com/schemas/2015/feed"
    xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/">
  <channel>
    <newznab:response offset="0" total="250" />
    <item>
      <title>Saga v01 (2012) (Digital) (F) (Zone-Empire)</title>
      <enclosure url="http://prowlarr:9696/1/download?apikey=x&amp;link=y"
        length="120000000" type="application/x-bittorrent" />
      <newznab:attr name="seeders" value="3" />
    </item>
  </channel>
</rss>
"""


class TorznabParsing(unittest.TestCase):
    def test_jackett_feed(self):
        items, total, offset = parse_torznab_feed(JACKETT_FEED)
        self.assertEqual(len(items), 2)
        self.assertIsNone(total)
        self.assertIsNone(offset)

        first = items[0]
        # .torrent link is preferred over magnet link
        self.assertTrue(first["link"].startswith("http://jackett:9117/dl/"))
        self.assertEqual(first["seeders"], 12)
        self.assertEqual(first["peers"], 15)
        self.assertEqual(first["size"], 45000000)
        self.assertEqual(first["info_url"], "https://tracker.example/details/1")
        self.assertEqual(first["pub_date"].year, 2026)

        # Only magnet link available
        second = items[1]
        self.assertEqual(second["link"], "magnet:?xt=urn:btih:def")
        self.assertEqual(second["size"], 1500000000)
        self.assertEqual(second["seeders"], 0)

    def test_prowlarr_feed(self):
        items, total, offset = parse_torznab_feed(PROWLARR_FEED)
        self.assertEqual(total, 250)
        self.assertEqual(offset, 0)
        self.assertEqual(items[0]["seeders"], 3)
        self.assertEqual(items[0]["size"], 120000000)
        self.assertTrue(items[0]["link"].startswith("http://prowlarr:9696/"))

    def test_error(self):
        with self.assertRaises(TorznabError) as cm:
            parse_torznab_feed(
                '<?xml version="1.0"?>'
                '<error code="100" description="Invalid API Key" />'
            )
        self.assertEqual(cm.exception.code, "100")

    def test_api_url(self):
        self.assertEqual(
            get_api_url("http://prowlarr:9696/1"),
            "http://prowlarr:9696/1/api"
        )
        self.assertEqual(
            get_api_url("http://prowlarr:9696/1/api"),
            "http://prowlarr:9696/1/api"
        )


class ReleaseTitles(unittest.TestCase):
    def test_normalise(self):
        cases = {
            "Batman.001.2016.Digital.Zone-Empire.cbr":
                "Batman 001 (2016) Digital Zone-Empire.cbr",
            "Batman 2016 001-100 complete pack":
                "Batman (2016) 001-100 complete pack",
            "Spider-Man 2099 001 2019":
                "Spider-Man 2099 001 (2019)",
            "2000 AD 2350 (2023)":
                "2000 AD 2350 (2023)",
            "Saga v01 (2012).cbz":
                "Saga v01 (2012).cbz"
        }
        for title, expected in cases.items():
            self.assertEqual(normalise_release_title(title), expected)


class LanguageFilter(unittest.TestCase):
    def test_foreign_releases(self):
        from backend.implementations.indexer_clients.torrent.Torznab import \
            foreign_language_regex

        foreign = (
            "Absolute Batman T01 - 2025 [CBR] [FR]",
            "Spider-Man (Spanish Edition) 001",
            "Deadpool #1 (Italian)",
            "Batman [ITA] 001",
            "Nightwing (DC Infinite) - Tome 4 - Le Grand Saut - Taylor",
        )
        english = (
            "Batman - The Long Halloween (1998) (Digital) (Zone-Empire).cbr",
            "Saga v01 (2012) (Digital) (F) (Zone-Empire)",
            "Wonder Woman - Dead Earth (2020) (DE) (Digital)",
            "The Tome of Knowledge 001 (2020)",
            "Tomb Raider 001 (2020)",
            "Absolute Batman #10 (2025)",
        )
        for title in foreign:
            self.assertIsNotNone(foreign_language_regex.search(title), title)
        for title in english:
            self.assertIsNone(foreign_language_regex.search(title), title)


class TorrentLinks(unittest.TestCase):
    def test_torrent_to_magnet(self):
        info = {b"name": b"Batman 001 (2016).cbz", b"piece length": 16384,
                b"pieces": b"0" * 20, b"length": 100}
        torrent = bencode({
            b"announce": b"udp://tracker.example:1337/announce",
            b"announce-list": [
                [b"udp://tracker.example:1337/announce"],
                [b"https://tracker2.example/announce?passkey=abc"]
            ],
            b"info": info
        })
        magnet, name = torrent_to_magnet(torrent)

        self.assertEqual(name, "Batman 001 (2016).cbz")
        self.assertIn(
            f"xt=urn:btih:{sha1(bencode(info)).hexdigest()}", magnet
        )
        self.assertEqual(get_magnet_name(magnet), name)
        self.assertEqual(magnet.count("&tr="), 2)

    def test_invalid_torrent(self):
        with self.assertRaises(ValueError):
            torrent_to_magnet(b"<html>Not a torrent</html>")

    def test_set_magnet_name(self):
        magnet = "magnet:?xt=urn:btih:abc&dn=Wrong&tr=udp%3A%2F%2Ft"
        result = set_magnet_name(magnet, "Right Name")
        self.assertEqual(get_magnet_name(result), "Right Name")
        self.assertTrue(result.startswith("magnet:?xt=urn:btih:abc&dn="))
        self.assertIn("&tr=udp%3A%2F%2Ft", result)


class QueryBuilding(unittest.TestCase):
    def _keys(self, **kwargs) -> QueryKeys:
        return QueryKeys(**{
            "titles": ["Batman"],
            "year": 2016,
            "volume_number": 3,
            "special_version": SpecialVersion.NORMAL,
            "issue_number": None,
            **kwargs
        })

    def test_volume(self):
        qb = TorrentQueryBuilder()
        q = qb.next_query(SearchAction.SEARCH_VOLUME, self._keys())
        self.assertEqual(q["query"], "Batman 2016")
        self.assertEqual(q["page"], 1)

    def test_issue(self):
        qb = TorrentQueryBuilder()
        keys = self._keys(issue_number="1")
        q = qb.next_query(SearchAction.SEARCH_ISSUE, keys)
        self.assertEqual(q["query"], "Batman 001 2016")
        q = qb.next_query(SearchAction.NEXT_QUERY_VARIATION, keys)
        self.assertEqual(q["query"], "Batman 001")

    def test_no_year(self):
        qb = TorrentQueryBuilder()
        q = qb.next_query(
            SearchAction.SEARCH_ISSUE,
            self._keys(year=None, issue_number="1.5")
        )
        self.assertEqual(q["query"], "Batman 1.5")

    def test_tpb(self):
        qb = TorrentQueryBuilder()
        q = qb.next_query(
            SearchAction.SEARCH_VOLUME,
            self._keys(special_version=SpecialVersion.TPB)
        )
        self.assertEqual(q["query"], "Batman Vol 3 2016")


class Validation(unittest.TestCase):
    tokens = TorznabIndexer.required_tokens

    def test_defaults(self):
        data = _validate_indexer_data(
            {"title": "T", "enabled": True, "url": "jackett:9117/api/v2.0/x/"},
            self.tokens
        )
        self.assertEqual(data["url"], "http://jackett:9117/api/v2.0/x")
        self.assertEqual(data["categories"], "7030")
        self.assertEqual(data["priority"], 25)
        self.assertEqual(data["min_seeders"], 1)
        self.assertIsNone(data["gc_service_preference"])

    def test_values(self):
        data = _validate_indexer_data(
            {
                "title": "T", "enabled": True, "url": "http://x",
                "api_key": "abc", "categories": ["7030", 8000],
                "min_seeders": 0, "priority": 1
            },
            self.tokens
        )
        self.assertEqual(data["categories"], "7030,8000")
        self.assertEqual(data["min_seeders"], 0)
        self.assertEqual(data["priority"], 1)

    def test_invalid(self):
        base = {"title": "T", "enabled": True, "url": "http://x"}
        for key, value in (
            ("priority", 0), ("priority", 51), ("priority", "1"),
            ("min_seeders", -1), ("categories", ["comics"]),
            ("categories", []), ("api_key", 5)
        ):
            with self.assertRaises(InvalidKeyValue, msg=f"{key}={value}"):
                _validate_indexer_data({**base, key: value}, self.tokens)


class Ranking(unittest.TestCase):
    def test_priority_and_seeders(self):
        from backend.features.search_full import SearchCoordinator

        coordinator = SearchCoordinator.__new__(SearchCoordinator)
        coordinator.volume_data = SimpleNamespace(
            title="Batman", volume_number=3, year=2016
        )

        def result(priority, seeders):
            return {
                "match": True, "series": "Batman", "volume_number": 3,
                "year": 2016, "issue_number": 1.0, "special_version": None,
                "indexer_priority": priority, "seeders": seeders
            }

        results = [
            result(25, 5), result(25, 50), result(10, 1), result(25, None)
        ]
        results.sort(key=lambda r: coordinator._rank_search_result(
            r, 2016, 1.0  # type: ignore
        ))
        self.assertEqual(
            [(r["indexer_priority"], r["seeders"]) for r in results],
            [(10, 1), (25, 50), (25, 5), (25, None)]
        )


class DatabaseTestCase(unittest.TestCase):
    "Sets up a fresh Bubblarr database in a temporary folder"

    @classmethod
    def setUpClass(cls):
        from backend.internals.db import DBConnection, set_db_location, setup_db
        from backend.internals.server import Server
        from backend.internals.settings import Settings

        IndexerClients.trigger_client_registration()

        cls.db_folder = mkdtemp()
        set_db_location(cls.db_folder)
        DBConnection.close_connection_of_thread()

        cls.app = Server().app
        cls.ctx = cls.app.app_context()
        cls.ctx.push()
        setup_db()
        Settings().clear_cache()
        cls.api_key = Settings().sv.api_key
        return

    @classmethod
    def tearDownClass(cls):
        from backend.internals.db import DBConnection
        from backend.internals.settings import Settings

        cls.ctx.pop()
        DBConnection.close_connection_of_thread()
        # Settings is a singleton tied to the database, so start fresh for
        # the next database
        Settings().clear_cache()
        Singleton._instances.pop(
            f"{Settings.__module__}.{Settings.__name__}", None
        )
        rmtree(cls.db_folder, ignore_errors=True)
        return


class IndexerStorage(DatabaseTestCase):
    def test_add_update_torznab(self):
        indexer = IndexerClients.add(
            download_type=DownloadType.TORRENT,
            client_type="Torznab",
            enabled=True,
            title="Jackett",
            url="http://jackett:9117/api/v2.0/indexers/all/results/torznab/",
            api_key="secret",
            categories=["7030"],
            min_seeders=2,
            priority=10,
            run_test=False
        )
        data = indexer.get_indexer_data()
        self.assertEqual(data["download_type"], DownloadType.TORRENT.value)
        self.assertEqual(data["categories"], [7030])
        self.assertEqual(data["min_seeders"], 2)
        self.assertEqual(data["priority"], 10)
        self.assertFalse(data["prowlarr_synced"])

        indexer.update_indexer(
            {**data, "priority": 30, "categories": [7030, 7020]},
            run_test=False
        )
        data = IndexerClients.get_client(indexer.id).get_indexer_data()
        self.assertEqual(data["priority"], 30)
        self.assertEqual(data["categories"], [7030, 7020])
        self.assertEqual(data["api_key"], "secret")

        indexer.delete_indexer()

    def test_get_all_clients(self):
        "All clients are returned, not only the first (cursor re-use bug)"
        ids = [
            IndexerClients.add(
                download_type=DownloadType.TORRENT, client_type="Torznab",
                enabled=True, title=title, url="http://x", run_test=False
            ).id
            for title in ("Zeta", "Omega")
        ]
        titles = [c.title for c in IndexerClients.get_all_clients()]
        self.assertEqual(titles, ["GetComics", "Omega", "Zeta"])
        for indexer_id in ids:
            IndexerClients.get_client(indexer_id).delete_indexer()

    def test_getcomics_still_works(self):
        gc = [
            c for c in IndexerClients.get_all_clients()
            if c.client_type == "GetComics"
        ][0]
        data = gc.get_indexer_data()
        self.assertEqual(data["priority"], 25)
        self.assertIsNotNone(data["gc_service_preference"])

        # The service preference as stored by Kapowarr, before this fork
        existing_preference = [
            "Mega", "MediaFire", "WeTransfer", "Pixeldrain",
            "GetComics", "GetComics (torrent)"
        ]
        with patch.object(type(gc), "test", return_value=None):
            gc.update_indexer({
                **data,
                "gc_service_preference": existing_preference,
                "priority": 5
            })
        data = IndexerClients.get_client(gc.id).get_indexer_data()
        self.assertEqual(data["priority"], 5)
        self.assertIsNotNone(data["gc_service_preference"])


class MylarCompat(DatabaseTestCase):
    def _call(self, **params):
        client = self.app.test_client()
        response = client.get(
            "/api",
            query_string={"apikey": self.api_key, **params}
        )
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def _providers(self):
        return self._call(cmd="listProviders")["data"]["torznabs"]

    def test_auth(self):
        response = self.app.test_client().get(
            "/api", query_string={"cmd": "getVersion", "apikey": "wrong"}
        ).get_json()
        self.assertFalse(response["success"])
        self.assertTrue(self._call(cmd="getVersion")["success"])

    def test_sync_lifecycle(self):
        self.assertEqual(self._providers(), [])

        # Prowlarr adds an indexer
        result = self._call(
            cmd="addProvider", name="1337x (Prowlarr)",
            providertype="torznab", host="http://prowlarr:9696/3/api",
            prov_apikey="prowlarr-key", enabled="true", categories="7030"
        )
        self.assertTrue(result["success"], result)

        providers = self._providers()
        self.assertEqual(len(providers), 1)
        self.assertEqual(providers[0]["host"], "http://prowlarr:9696/3/api")
        self.assertEqual(providers[0]["apikey"], "prowlarr-key")
        self.assertEqual(providers[0]["categories"], "7030")
        self.assertTrue(providers[0]["enabled"])

        indexer = [
            c for c in IndexerClients.get_all_clients()
            if c.title == "1337x (Prowlarr)"
        ][0]
        self.assertTrue(indexer.get_indexer_data()["prowlarr_synced"])

        # User changes priority in Bubblarr, then Prowlarr renames it
        indexer.update_indexer(
            {**indexer.get_indexer_data(), "priority": 5}, run_test=False
        )
        result = self._call(
            cmd="changeProvider", name="1337x (Prowlarr)",
            altername="1337x Renamed (Prowlarr)", providertype="torznab",
            host="http://prowlarr:9696/3/api", prov_apikey="prowlarr-key",
            enabled="false", categories="7030"
        )
        self.assertTrue(result["success"], result)
        providers = self._providers()
        self.assertEqual(providers[0]["name"], "1337x Renamed (Prowlarr)")
        self.assertFalse(providers[0]["enabled"])
        data = IndexerClients.get_client(indexer.id).get_indexer_data()
        self.assertEqual(data["priority"], 5)
        self.assertTrue(data["prowlarr_synced"])

        # Usenet is not supported
        result = self._call(
            cmd="addProvider", name="NZB", providertype="newznab",
            host="http://prowlarr:9696/4/api", prov_apikey="k",
            enabled="true", categories="7030"
        )
        self.assertFalse(result["success"])

        # Prowlarr removes it
        result = self._call(
            cmd="delProvider", name="1337x Renamed (Prowlarr)",
            providertype="torznab"
        )
        self.assertTrue(result["success"], result)
        self.assertEqual(self._providers(), [])


class Migration(DatabaseTestCase):
    def test_migrate_from_51(self):
        from backend.internals.db import get_db
        from backend.internals.db_migration import DatabaseMigrationHandler
        from backend.internals.settings import Settings

        cursor = get_db()
        # Recreate the table as it was before migration 51
        cursor.executescript("""
            DROP TABLE indexer_clients;
            CREATE TABLE indexer_clients(
                id INTEGER PRIMARY KEY,
                enabled BOOL NOT NULL DEFAULT 1,
                download_type INTEGER NOT NULL,
                client_type VARCHAR(255) NOT NULL,
                title VARCHAR(255) NOT NULL,
                url TEXT NOT NULL,
                gc_service_preference TEXT,
                gc_avoid_large_downloads BOOL
            );
            INSERT INTO indexer_clients(
                download_type, client_type, title, url,
                gc_service_preference, gc_avoid_large_downloads
            ) VALUES (
                1, 'GetComics', 'GetComics', 'https://getcomics.org',
                'Mega,MediaFire,WeTransfer,Pixeldrain,GetComics,GetComics (torrent)',
                0
            );
        """)
        Settings().update({"database_version": 51})
        DatabaseMigrationHandler.migrate()

        self.assertEqual(
            Settings().sv.database_version,
            DatabaseMigrationHandler.latest_db_version()
        )
        data = IndexerClients.get_all_data()[0]
        self.assertEqual(data["priority"], 25)
        self.assertFalse(data["prowlarr_synced"])


class Discover(unittest.TestCase):
    def test_filters_old_and_low_seeders(self):
        indexer = TorznabIndexer.__new__(TorznabIndexer)
        indexer._id = 1
        indexer._title = "Jackett"
        indexer._priority = 25
        indexer._min_seeders = 1

        async def fake_fetch(params):
            return parse_torznab_feed(JACKETT_FEED)

        indexer._fetch = fake_fetch  # type: ignore
        indexer._base_params = lambda: {}  # type: ignore

        from asyncio import run
        results = run(indexer.discover(datetime(2025, 1, 1)))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["series"], "Batman")
        self.assertEqual(results[0]["issue_number"], 1.0)
        self.assertEqual(results[0]["year"], 2016)
        self.assertEqual(results[0]["seeders"], 12)
        self.assertEqual(
            results[0]["download_type"], DownloadType.TORRENT.value
        )


class LegacyDatabaseName(unittest.TestCase):
    def test_kapowarr_db_is_renamed(self):
        from os import listdir
        from os.path import join

        from backend.internals.db import DBConnection, set_db_location

        folder = mkdtemp()
        old_default = DBConnection.default_file
        try:
            for suffix in ('', '-wal'):
                with open(join(folder, 'Kapowarr.db' + suffix), 'w') as f:
                    f.write('data' + suffix)

            set_db_location(folder)

            self.assertEqual(
                sorted(listdir(folder)), ['Bubblarr.db', 'Bubblarr.db-wal']
            )
            with open(join(folder, 'Bubblarr.db')) as f:
                self.assertEqual(f.read(), 'data')
        finally:
            DBConnection.default_file = old_default
            rmtree(folder, ignore_errors=True)


class SearchIteration(unittest.TestCase):
    def test_multiple_indexers_stop_in_same_iteration(self):
        "Removing several finished indexers at once used to raise IndexError"
        from asyncio import run

        from backend.base.definitions import SearchAction
        from backend.features.search_full import SearchCoordinator

        class FakeIndexer:
            def __init__(self, name):
                self.name = name
                self.shut_down = False

            async def shutdown(self):
                self.shut_down = True

            async def search(self, query):
                from backend.base.definitions import QueryResult
                return QueryResult([], next_page_available=False)

        class FakePlanner:
            def __init__(self, action):
                self.action = action

            def next_action(self):
                return (self.action, None)

        class FakeBuilder:
            def next_query(self, action, keys):
                return {"query": "q", "page": 1,
                        "total_available_variations": 1}

        stopping = [FakeIndexer("a"), FakeIndexer("b"), FakeIndexer("d")]
        searching = FakeIndexer("c")
        coordinator = SearchCoordinator.__new__(SearchCoordinator)
        coordinator.indexers = [
            {"indexer": stopping[0], "query_builder": FakeBuilder(),
             "search_action_planner": FakePlanner(SearchAction.STOP)},
            {"indexer": stopping[1], "query_builder": FakeBuilder(),
             "search_action_planner": FakePlanner(SearchAction.STOP)},
            {"indexer": searching, "query_builder": FakeBuilder(),
             "search_action_planner": FakePlanner(SearchAction.SEARCH_VOLUME)},
            {"indexer": stopping[2], "query_builder": FakeBuilder(),
             "search_action_planner": FakePlanner(SearchAction.STOP)},
        ]

        results = run(coordinator._run_iteration())

        self.assertEqual(len(results), 1)
        self.assertEqual(
            [t["indexer"] for t in coordinator.indexers], [searching]
        )
        self.assertTrue(all(i.shut_down for i in stopping))
        self.assertFalse(searching.shut_down)


class SearchPlanning(unittest.TestCase):
    def _planner(self, per_issue: bool):
        from backend.base.definitions import SearchIterationStats
        from backend.implementations.search_action_planner import \
            SearchActionPlanner

        volume = SimpleNamespace(
            title="Nightwing", alt_title=None, year=2016, volume_number=4,
            special_version=SpecialVersion.NORMAL
        )
        issues = [
            SimpleNamespace(id=i, issue_number=str(i)) for i in range(1, 6)
        ]
        planner = SearchActionPlanner(
            volume, issues, [5, 3, 1, 2, 4],  # type: ignore
            search_issues_without_volume_match=per_issue
        )

        def no_matches(wanted):
            return SearchIterationStats(
                result_count=10, matched_count=0, new_match_count=0,
                next_page_available=False, remaining_wanted_issues=wanted,
                total_available_variations=2
            )
        return planner, no_matches

    def _run(self, per_issue: bool):
        planner, no_matches = self._planner(per_issue)
        actions = []
        for _ in range(30):
            action, keys = planner.next_action()
            actions.append((action, keys.issue_number))
            if action == SearchAction.STOP:
                break
            planner.process_stats(no_matches([5, 3, 1, 2, 4]))
        return actions

    def test_ddl_stops_without_volume_match(self):
        actions = self._run(per_issue=False)
        self.assertNotIn(SearchAction.SEARCH_ISSUE, [a for a, _ in actions])
        self.assertEqual(actions[-1][0], SearchAction.STOP)

    def test_torrent_searches_issues_in_given_order(self):
        actions = self._run(per_issue=True)
        searched = [n for a, n in actions if a == SearchAction.SEARCH_ISSUE]
        # Wanted order is kept, and it gives up after 3 more failed issues
        self.assertEqual(searched, ["5", "3", "1", "2"])
        self.assertEqual(actions[-1][0], SearchAction.STOP)


class QBittorrentStates(unittest.TestCase):
    def _client(self, torrent_state: str, progress: float, timeout: int = 0):
        from unittest.mock import MagicMock

        from backend.implementations.external_clients.torrent.qBittorrent import \
            qBittorrent

        client = qBittorrent.__new__(qBittorrent)
        client._base_url = "http://qbit"
        client.statuses = {"abc": None}
        client.fail_timestamps = {"abc": None}
        client.settings = SimpleNamespace(
            sv=SimpleNamespace(failing_download_timeout=timeout)
        )
        client._ensure_login = lambda: None  # type: ignore
        client.ssn = MagicMock()
        client.ssn.get.return_value.json.return_value = [{
            "hash": "abc", "state": torrent_state, "progress": progress,
            "total_size": 100, "dlspeed": 0, "name": "x",
            "content_path": "/dl/x", "save_path": "/dl"
        }]
        return client

    def _states(self, torrent_state, progress, polls=3, timeout=0):
        client = self._client(torrent_state, progress, timeout)
        result = []
        for _ in range(polls):
            client._update_statuses()
            result.append(client.statuses["abc"]["state"])
        return result

    def test_stalled_download_is_never_imported(self):
        from backend.base.definitions import DownloadState
        self.assertEqual(
            self._states("stalledDL", 0.4),
            [DownloadState.DOWNLOADING_STATE] * 3
        )

    def test_unknown_state_is_not_imported(self):
        from backend.base.definitions import DownloadState
        self.assertEqual(
            self._states("someNewState", 0.0, polls=1),
            [DownloadState.DOWNLOADING_STATE]
        )

    def test_qbittorrent_5_stopped_completed(self):
        from backend.base.definitions import DownloadState
        self.assertEqual(
            self._states("stoppedUP", 1.0, polls=1),
            [DownloadState.IMPORTING_STATE]
        )
        self.assertEqual(
            self._states("stoppedDL", 0.2, polls=1),
            [DownloadState.PAUSED_STATE]
        )

    def test_incomplete_seeding_is_downloading(self):
        from backend.base.definitions import DownloadState
        self.assertEqual(
            self._states("uploading", 0.5, polls=1),
            [DownloadState.DOWNLOADING_STATE]
        )

    def test_stalled_fails_after_timeout(self):
        from backend.base.definitions import DownloadState
        client = self._client("metaDL", 0.0, timeout=1)
        client._update_statuses()
        client.fail_timestamps["abc"] -= 10
        client._update_statuses()
        self.assertEqual(
            client.statuses["abc"]["state"], DownloadState.FAILED_STATE
        )


class QueueDuplicates(unittest.TestCase):
    def test_torrent_hash(self):
        from backend.features.download_queue import get_torrent_hash
        self.assertEqual(
            get_torrent_hash(
                "magnet:?xt=urn:btih:820EF5544B1A64710B41EC5BC074DE362030B215"
                "&dn=BATGIRL+%23+19"
            ),
            "820ef5544b1a64710b41ec5bc074de362030b215"
        )
        self.assertIsNone(get_torrent_hash("https://getcomics.org/x"))
