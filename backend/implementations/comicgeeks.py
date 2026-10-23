# -*- coding: utf-8 -*-

"""
Client for League of Comic Geeks (LoCG). LoCG has no public API, so this uses
the same (internal) endpoints as their website. It's unofficial, so it can
break when LoCG changes their website.

Used to:
- Mark downloaded issues as collected on LoCG.
- Show the pull list of the user in the calendar.
"""

from datetime import date, datetime, timedelta, timezone
from re import compile
from threading import Lock, RLock
from time import sleep, time
from typing import Any, Dict, List, Union

from bs4 import BeautifulSoup
from requests import RequestException, Session

from backend.base.custom_exceptions import ClientNotWorking, CredentialInvalid
from backend.base.definitions import BrokenClientReason
from backend.base.logging import LOGGER

BASE_URL = "https://leagueofcomicgeeks.com"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0 Safari/537.36"
)
REQUEST_INTERVAL = 1.0
"Seconds between requests, to not overload LoCG"

PULL_LIST_ID = 1
COLLECTION_LIST_ID = 2

comic_url_regex = compile(r"/comic/(\d+)/")
series_url_regex = compile(r"/comics/series/(\d+)/")
issue_number_regex = compile(r"#\s*([^\s(]+)")
user_id_regexes = (
    compile(r"""user_id['"]?\s*[:=]\s*['"]?(\d+)"""),
    compile(r"""data-user(?:-id)?=['"](\d+)['"]"""),
    compile(r"""['"]id['"]\s*:\s*['"]?(\d+)['"]?\s*,\s*['"](?:name|username)['"]"""),
)
year_range_regex = compile(r"(\d{4})(?:\s*-\s*(\d{4}|Present))?")
non_word_regex = compile(r"[^a-z0-9]+")


def normalise_title(title: str) -> str:
    """Normalise a title for comparison, e.g. "Batman & Robin" and
    "Batman and Robin" both become "batmanrobin".

    Args:
        title (str): The title.

    Returns:
        str: The normalised title.
    """
    title = title.lower().replace("&", " and ")
    words = [
        w for w in non_word_regex.split(title)
        if w and w not in ("the", "and", "a")
    ]
    return "".join(words)


def normalise_issue_number(number: str) -> str:
    """Normalise an issue number for comparison, e.g. "001" becomes "1".

    Args:
        number (str): The issue number.

    Returns:
        str: The normalised issue number.
    """
    number = number.strip().lower()
    stripped = number.lstrip("0")
    if not stripped or not stripped[0].isdigit():
        # E.g. "0" or "½"
        return number if not stripped else stripped
    return stripped


def parse_series_results(html: str) -> List[Dict[str, Any]]:
    """Parse the series of a LoCG search result.

    Args:
        html (str): The `list` HTML of the response.

    Returns:
        List[Dict[str, Any]]: The series, with the keys `id`, `title`,
            `publisher`, `start_year` and `end_year`.
    """
    soup = BeautifulSoup(html, "html.parser")
    result = []
    for li in soup.find_all("li"):
        title_el = li.select_one(".title a.link-collection-series")
        if not title_el or not title_el.get("data-id"):
            continue

        details = li.select(".copy-really-small span")
        publisher = details[0].get_text(strip=True) if details else ""
        years = (
            details[1].get_text(" ", strip=True).replace("\xa0", " ")
            if len(details) > 1 else ""
        )
        year_match = year_range_regex.search(years)
        start_year = int(year_match.group(1)) if year_match else None
        end_year = None
        if year_match and year_match.group(2) and year_match.group(2).isdigit():
            end_year = int(year_match.group(2))

        result.append({
            "id": int(title_el["data-id"]),
            "title": title_el.get_text(strip=True),
            "publisher": publisher,
            "start_year": start_year,
            "end_year": end_year
        })
    return result


def parse_issue_results(html: str) -> List[Dict[str, Any]]:
    """Parse the issues of a LoCG list (series, pull list, etc.).
    Variant covers are skipped.

    Args:
        html (str): The `list` HTML of the response.

    Returns:
        List[Dict[str, Any]]: The issues, with the keys `comic_id`, `title`,
            `series_title`, `issue_number`, `date` (`YYYY-MM-DD` or None),
            `publisher` and `url`.
    """
    soup = BeautifulSoup(html, "html.parser")
    result = []
    for li in soup.select("li.issue"):
        if li.get("data-parent", "0") not in ("0", ""):
            # Variant cover
            continue

        link = li.select_one(".title a")
        if not link:
            continue
        href = link.get("href", "")
        if "variant=" in href:
            continue
        id_match = comic_url_regex.search(href)
        if not id_match:
            continue

        title = link.get_text(strip=True)
        number_match = issue_number_regex.search(title)
        series_title = title.split("#")[0].strip() if "#" in title else title

        release_date = None
        date_el = li.select_one(".date[data-date]")
        if date_el:
            try:
                timestamp = int(date_el["data-date"])
                # Far future timestamps are used for "TBD"
                if timestamp < 4_000_000_000:
                    # Timestamps are midnight US time (04:00/05:00 UTC), so
                    # use the UTC date instead of the local one
                    release_date = datetime.fromtimestamp(
                        timestamp, timezone.utc
                    ).strftime("%Y-%m-%d")
            except (TypeError, ValueError, OSError):
                pass

        publisher_el = li.select_one(".publisher")
        result.append({
            "comic_id": int(id_match.group(1)),
            "title": title,
            "series_title": series_title,
            "issue_number": number_match.group(1) if number_match else None,
            "date": release_date,
            "publisher": (
                publisher_el.get_text(strip=True) if publisher_el else ""
            ),
            "url": BASE_URL + href
        })
    return result


def pick_series(
    series: List[Dict[str, Any]],
    title: str,
    year: Union[int, None],
    publisher: Union[str, None]
) -> Union[Dict[str, Any], None]:
    """Pick the series that matches a volume. The title must match exactly
    (ignoring punctuation), the start year can differ by one and the publisher
    must match if known. That way foreign editions and spin-offs are skipped.

    Args:
        series (List[Dict[str, Any]]): The search results.
        title (str): The title of the volume.
        year (Union[int, None]): The start year of the volume.
        publisher (Union[str, None]): The publisher of the volume.

    Returns:
        Union[Dict[str, Any], None]: The matching series, if found.
    """
    wanted_title = normalise_title(title)
    wanted_publisher = normalise_title(publisher or "")

    candidates = []
    for s in series:
        if normalise_title(s["title"]) != wanted_title:
            continue

        if wanted_publisher:
            found_publisher = normalise_title(s["publisher"])
            if not (
                found_publisher.startswith(wanted_publisher)
                or wanted_publisher.startswith(found_publisher)
            ):
                continue

        if year is not None and s["start_year"] is not None:
            if abs(s["start_year"] - year) > 1:
                continue
            candidates.append((abs(s["start_year"] - year), s))
        else:
            candidates.append((2, s))

    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    return candidates[0][1]


class ComicGeeks:
    """A logged in session with League of Comic Geeks"""

    _lock = Lock()
    _request_lock = RLock()
    _session: Union[Session, None] = None
    _user_id: Union[int, None] = None
    _username: Union[str, None] = None
    _last_request: float = 0.0

    @classmethod
    def _request(cls, method: str, path: str, **kwargs: Any):
        # One request at a time, with a pause in between, as the background
        # sync and the calendar can use LoCG at the same time.
        with cls._request_lock:
            if cls._session is None:
                cls._session = Session()
                cls._session.headers.update({"User-Agent": USER_AGENT})

            wait = cls._last_request + REQUEST_INTERVAL - time()
            if wait > 0:
                sleep(wait)

            try:
                return cls._session.request(
                    method, BASE_URL + path, timeout=30, **kwargs
                )
            except RequestException:
                raise ClientNotWorking(BrokenClientReason.CONNECTION_ERROR)
            finally:
                cls._last_request = time()

    @classmethod
    def _get_json(cls, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        response = cls._request(
            "GET", path, params=params,
            headers={"X-Requested-With": "XMLHttpRequest"}
        )
        if response.status_code != 200:
            raise ClientNotWorking(BrokenClientReason.FAILED_PROCESSING_RESPONSE)
        try:
            return response.json()
        except ValueError:
            raise ClientNotWorking(BrokenClientReason.FAILED_PROCESSING_RESPONSE)

    @classmethod
    def _find_user_id(cls, html: str) -> Union[int, None]:
        for regex in user_id_regexes:
            match = regex.search(html)
            if match:
                return int(match.group(1))
        return None

    @classmethod
    def login(cls, username: str, password: str) -> int:
        """Log in to LoCG.

        Args:
            username (str): The username (or email address).
            password (str): The password.

        Raises:
            CredentialInvalid: The username or password is wrong.
            ClientNotWorking: Couldn't connect or understand the response.

        Returns:
            int: The ID of the user.
        """
        with cls._lock:
            cls._session = None
            cls._user_id = None

            # Get session and CSRF cookies first
            cls._request("GET", "/login")
            csrf = cls._session.cookies.get("ci_csrf_token", "")  # type: ignore

            response = cls._request(
                "POST", "/login",
                data={
                    "ci_csrf_token": csrf,
                    "username": username,
                    "password": password,
                    "submit": "Log In"
                },
                allow_redirects=True
            )
            html = response.text
            soup = BeautifulSoup(html, "html.parser")

            if (
                response.url.rstrip("/").endswith("/login")
                or soup.select_one(".alert-error, .alert-danger")
            ):
                raise CredentialInvalid

            user_id = cls._find_user_id(html)
            if user_id is None:
                # Look on the own profile page
                profile = cls._request("GET", f"/profile/{username}")
                user_id = cls._find_user_id(profile.text)

            if user_id is None:
                LOGGER.warning(
                    "Logged in to League of Comic Geeks, but couldn't find "
                    "the user ID"
                )
                raise ClientNotWorking(
                    BrokenClientReason.FAILED_PROCESSING_RESPONSE
                )

            cls._user_id = user_id
            cls._username = username
            LOGGER.info("Logged in to League of Comic Geeks as %s", username)
            return user_id

    @classmethod
    def reset(cls) -> None:
        "Forget the login, e.g. after the credentials changed"
        with cls._lock:
            cls._session = None
            cls._user_id = None
            cls._username = None
        return

    @classmethod
    def ensure_login(cls) -> int:
        """Log in with the credentials from the settings, if not already.

        Raises:
            CredentialInvalid: No or wrong credentials.
            ClientNotWorking: Couldn't connect or understand the response.

        Returns:
            int: The ID of the user.
        """
        from backend.internals.settings import Settings

        sv = Settings().sv
        if not (sv.comicgeeks_username and sv.comicgeeks_password):
            raise CredentialInvalid

        if cls._user_id is not None and cls._username == sv.comicgeeks_username:
            return cls._user_id

        return cls.login(sv.comicgeeks_username, sv.comicgeeks_password)

    @classmethod
    def search_series(cls, title: str) -> List[Dict[str, Any]]:
        """Search for series on LoCG.

        Args:
            title (str): The title to search for.

        Returns:
            List[Dict[str, Any]]: The series. See `parse_series_results()`.
        """
        result = cls._get_json("/comic/get_comics", {
            "list": "search",
            "list_option": "series",
            "view": "thumbs",
            "order": "alpha-asc",
            "title": title
        })
        return parse_series_results(result.get("list", ""))

    @classmethod
    def get_series_issues(cls, series_id: int) -> List[Dict[str, Any]]:
        """Get the issues (without variants) of a series.

        Args:
            series_id (int): The ID of the series on LoCG.

        Returns:
            List[Dict[str, Any]]: The issues. See `parse_issue_results()`.
        """
        result = cls._get_json("/comic/get_comics", {
            "list": "series",
            "list_option": "issue",
            "series_id": series_id,
            "view": "list",
            "order": "date-desc"
        })
        return parse_issue_results(result.get("list", ""))

    @classmethod
    def get_pull_list(cls, week: date) -> List[Dict[str, Any]]:
        """Get the pull list of the user for a week.

        Args:
            week (date): A date in the week.

        Returns:
            List[Dict[str, Any]]: The pulled issues. See
                `parse_issue_results()`.
        """
        user_id = cls.ensure_login()
        # LoCG weeks start on Sunday
        sunday = week - timedelta(days=(week.weekday() + 1) % 7)
        result = cls._get_json("/comic/get_comics", {
            "list": PULL_LIST_ID,
            "list_option": "issue",
            "user_id": user_id,
            "view": "list",
            "order": "alpha-asc",
            "date_type": "week",
            "date": sunday.strftime("%m/%d/%Y")
        })
        return parse_issue_results(result.get("list", ""))

    @classmethod
    def set_collected(cls, comic_id: int, collected: bool = True) -> None:
        """Add or remove an issue from the collection of the user.

        Args:
            comic_id (int): The ID of the issue on LoCG.
            collected (bool, optional): Add (True) or remove (False).
                Defaults to True.

        Raises:
            ClientNotWorking: The change failed.
        """
        cls.ensure_login()
        response = cls._request(
            "POST", "/comic/my_list_move",
            data={
                "comic_id": comic_id,
                "list_id": COLLECTION_LIST_ID,
                "action_id": 1 if collected else 0
            },
            headers={"X-Requested-With": "XMLHttpRequest"}
        )
        if response.status_code != 200 or not response.text.strip().lstrip(
            "-"
        ).isdigit():
            LOGGER.warning(
                "League of Comic Geeks didn't accept collection change for "
                "comic %d: %s",
                comic_id, response.text[:200]
            )
            raise ClientNotWorking(
                BrokenClientReason.FAILED_PROCESSING_RESPONSE
            )
        return
