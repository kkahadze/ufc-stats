#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
import time
import unicodedata
from argparse import ArgumentParser, Namespace
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

try:
    import pycountry
except ImportError:  # pragma: no cover - dependency is installed via requirements.txt
    pycountry = None

BASE_URL = "http://ufcstats.com"
EVENTS_URL = f"{BASE_URL}/statistics/events/completed?page=all"
RANKINGS_URL = "https://www.ufc.com/rankings"
ATHLETES_URL = "https://www.ufc.com/athletes/all"
UFC_EVENTS_URL = "https://www.ufc.com/events"
UFC_URL = "https://www.ufc.com"
OUTPUT_DIR = Path(__file__).resolve().parents[1] / "docs" / "data"
CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache" / "ufc-stats"
EVENT_CACHE_DIR = CACHE_DIR / "events"
UFC_EVENTS_PAGE_CACHE_DIR = CACHE_DIR / "ufc-events-pages"
UFC_EVENT_CARD_CACHE_DIR = CACHE_DIR / "ufc-event-cards"
RANKINGS_CACHE_PATH = CACHE_DIR / "rankings.html"
ATHLETE_DIRECTORY_CACHE_PATH = CACHE_DIR / "athlete-directory.json"
ATHLETE_LOCATION_CACHE_PATH = CACHE_DIR / "athlete-locations.json"
ATHLETE_CACHE_DIR = CACHE_DIR / "athletes"
REQUEST_TIMEOUT = 30
RETRY_ATTEMPTS = 3
RECENT_EVENT_REFRESH_COUNT = 12
RECENT_UFC_EVENTS_INDEX_REFRESH_COUNT = 4
RECENT_UFC_EVENT_CARD_REFRESH_COUNT = 24
MAX_UFC_EVENT_ARCHIVE_PAGES = 30
TODAY = datetime.now(timezone.utc).date()
ATHLETE_DIRECTORY_PAGE_SIZE = 11
ATHLETE_DIRECTORY_REFRESH_DAYS = 14

DIVISIONS = (
    ("Flyweight", "flw_fights.json"),
    ("Bantamweight", "bw_fights.json"),
    ("Featherweight", "fw_fights.json"),
    ("Lightweight", "lw_fights.json"),
    ("Welterweight", "ww_fights.json"),
    ("Middleweight", "mw_fights.json"),
    ("Light Heavyweight", "lhw_fights.json"),
    ("Heavyweight", "hw_fights.json"),
    ("Women's Strawweight", "wsw_fights.json"),
    ("Women's Flyweight", "wflw_fights.json"),
    ("Women's Bantamweight", "wbw_fights.json"),
    ("Women's Featherweight", "wfw_fights.json"),
)
DIVISION_FILENAMES = dict(DIVISIONS)
COUNTRY_NAME_OVERRIDES = {
    "bosnia and herzegovina": "BA",
    "cape verde": "CV",
    "curacao": "CW",
    "czech republic": "CZ",
    "democratic republic of the congo": "CD",
    "england": "GB",
    "hong kong": "HK",
    "iran": "IR",
    "ivory coast": "CI",
    "kosovo": "XK",
    "laos": "LA",
    "macao": "MO",
    "macau": "MO",
    "moldova": "MD",
    "netherlands": "NL",
    "north korea": "KP",
    "palestine": "PS",
    "republic of ireland": "IE",
    "republic of korea": "KR",
    "republic of moldova": "MD",
    "russia": "RU",
    "scotland": "GB",
    "slovakia": "SK",
    "south korea": "KR",
    "syria": "SY",
    "taiwan": "TW",
    "tanzania": "TZ",
    "the netherlands": "NL",
    "u.s.a.": "US",
    "uk": "GB",
    "united states": "US",
    "usa": "US",
    "venezuela": "VE",
    "vietnam": "VN",
    "wales": "GB",
}
COUNTRY_CODE_OVERRIDES = {
    "EN": "GB",
    "SF": "GB",
    "WL": "GB",
}
FIGHTER_COUNTRY_OVERRIDES = {
    "farid basharat": {
        "countryCode": "AF",
        "countryName": "Afghanistan",
    },
    "jonathan brookins": {
        "countryCode": "US",
        "countryName": "United States",
    },
    "manvel gamburyan": {
        "countryCode": "US",
        "countryName": "United States",
    },
}


@dataclass(frozen=True)
class EventFight:
    division: str
    fighter_a_id: str
    fighter_a_name: str
    fighter_b_id: str
    fighter_b_name: str
    event_name: str
    event_date: str | None
    result_summary: str
    method: str
    round: str
    time: str
    is_title_bout: bool
    winner_id: str | None


@dataclass(frozen=True)
class CountryInfo:
    code: str
    name: str
    flag_emoji: str


def clean_text(value: str) -> str:
    return " ".join(value.split())


def normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_value = ascii_value.lower().replace("&", "and")
    ascii_value = re.sub(r"[^a-z0-9]+", " ", ascii_value)
    return clean_text(ascii_value)


def parse_event_date_value(value: str | None) -> date | None:
    if not value:
        return None

    try:
        return datetime.strptime(value, "%B %d, %Y").date()
    except ValueError:
        return None


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)


def parse_args() -> Namespace:
    parser = ArgumentParser(description="Refresh UFC fight graph data.")
    parser.add_argument(
        "--refresh-all",
        action="store_true",
        help="Ignore cached event and ranking pages and fetch everything from the network.",
    )
    return parser.parse_args()


def fetch_text(url: str) -> str:
    error: Exception | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            result = subprocess.run(
                [
                    "curl",
                    "--fail",
                    "--location",
                    "--silent",
                    "--show-error",
                    "--max-time",
                    str(REQUEST_TIMEOUT),
                    "--user-agent",
                    USER_AGENT,
                    url,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            return result.stdout
        except Exception as exc:  # pragma: no cover - network path
            error = exc
            if attempt == RETRY_ATTEMPTS:
                break
            time.sleep(attempt)

    detail = ""
    if isinstance(error, subprocess.CalledProcessError):
        stderr = clean_text(error.stderr or "")
        detail = f" (curl exit {error.returncode}"
        if stderr:
            detail += f": {stderr}"
        detail += ")"
    raise RuntimeError(f"Failed to fetch {url}{detail}") from error


def read_cached_text(cache_path: Path) -> str:
    return cache_path.read_text(encoding="utf-8")


def write_cached_text(cache_path: Path, contents: str) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(contents, encoding="utf-8")


def read_cached_json(cache_path: Path) -> dict:
    return json.loads(cache_path.read_text(encoding="utf-8"))


def write_cached_json(cache_path: Path, payload: dict) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def cache_is_recent(cache_payload: dict, max_age_days: int) -> bool:
    cached_at = cache_payload.get("cached_at")
    if not cached_at:
        return False

    try:
        cached_time = datetime.fromisoformat(cached_at)
    except ValueError:
        return False

    return (datetime.now(timezone.utc) - cached_time) <= timedelta(days=max_age_days)


def fetch_soup_from_text(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def fetch_soup(url: str) -> BeautifulSoup:
    return fetch_soup_from_text(fetch_text(url))


def get_event_id(event_link: str) -> str:
    return event_link.rstrip("/").rsplit("/", 1)[-1]


def parse_completed_event_links(soup: BeautifulSoup) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()

    for anchor in soup.select("a.b-link_style_black, a.b-link_style_white"):
        href = anchor.get("href", "").strip()
        if "/event-details/" not in href or href in seen:
            continue
        links.append(href)
        seen.add(href)

    return links


def parse_event_date_text(soup: BeautifulSoup) -> str | None:
    for item in soup.select("li.b-list__box-list-item"):
        title = item.select_one(".b-list__box-item-title")
        if title is None:
            continue
        title_text = clean_text(title.get_text(" ", strip=True))
        if title_text.rstrip(":") != "Date":
            continue

        item_text = clean_text(item.get_text(" ", strip=True))
        if item_text.startswith(title_text):
            return clean_text(item_text[len(title_text) :].strip())
        return item_text

    return None


def parse_event_name_text(soup: BeautifulSoup) -> str | None:
    title = soup.select_one(".b-content__title-highlight") or soup.select_one(".b-content__title")
    if title is None:
        return None
    return clean_text(title.get_text(" ", strip=True))


def is_future_event_soup(soup: BeautifulSoup) -> bool:
    event_date = parse_event_date_value(parse_event_date_text(soup))
    return event_date is not None and event_date > TODAY


def build_result_summary(result_flags: list[str], fighter_a_name: str, fighter_b_name: str) -> str:
    if result_flags == ["win"]:
        return f"{fighter_a_name} def. {fighter_b_name}"
    if result_flags == ["draw", "draw"]:
        return "Draw"
    if result_flags == ["nc", "nc"]:
        return "No contest"
    return "Recorded UFC bout"


def is_title_bout_row(weight_class_cell: BeautifulSoup) -> bool:
    return weight_class_cell.select_one('img[src*="belt.png"]') is not None


def get_winning_fighter_id(
    result_flags: list[str], fighter_a_id: str, fighter_b_id: str
) -> str | None:
    if result_flags == ["win"]:
        return fighter_a_id
    if result_flags == ["loss"]:
        return fighter_b_id
    return None


def parse_event_fights(
    soup: BeautifulSoup, event_name: str, event_date: str | None
) -> list[EventFight]:
    fights: list[EventFight] = []

    for row in soup.select("tbody.b-fight-details__table-body tr"):
        result_cell = row.select_one("td:nth-of-type(1)")
        fighter_links = row.select("td:nth-of-type(2) a[href*='/fighter-details/']")
        weight_class_cell = row.select_one("td:nth-of-type(7)")
        method_cell = row.select_one("td:nth-of-type(8)")
        if result_cell is None or weight_class_cell is None or method_cell is None:
            continue
        if len(fighter_links) != 2:
            continue

        result_flags = [
            clean_text(flag.get_text(" ", strip=True)).lower()
            for flag in result_cell.select(".b-flag__text")
            if clean_text(flag.get_text(" ", strip=True))
        ]
        result_text = " ".join(result_flags)
        method_text = clean_text(method_cell.get_text(" ", strip=True))
        round_cell = row.select_one("td:nth-of-type(9)")
        time_cell = row.select_one("td:nth-of-type(10)")
        if not result_text and not method_text:
            continue

        division = clean_text(weight_class_cell.get_text(" ", strip=True))
        if division not in DIVISION_FILENAMES:
            continue

        fighter_a_href = fighter_links[0].get("href", "").rstrip("/")
        fighter_b_href = fighter_links[1].get("href", "").rstrip("/")
        fighter_a_id = fighter_a_href.rsplit("/", 1)[-1]
        fighter_b_id = fighter_b_href.rsplit("/", 1)[-1]
        fighter_a_name = clean_text(fighter_links[0].get_text(" ", strip=True))
        fighter_b_name = clean_text(fighter_links[1].get_text(" ", strip=True))

        if not fighter_a_id or not fighter_b_id or fighter_a_id == fighter_b_id:
            continue

        fights.append(
            EventFight(
                division=division,
                fighter_a_id=fighter_a_id,
                fighter_a_name=fighter_a_name,
                fighter_b_id=fighter_b_id,
                fighter_b_name=fighter_b_name,
                event_name=event_name,
                event_date=event_date,
                result_summary=build_result_summary(result_flags, fighter_a_name, fighter_b_name),
                method=method_text,
                round=clean_text(round_cell.get_text(" ", strip=True)) if round_cell else "",
                time=clean_text(time_cell.get_text(" ", strip=True)) if time_cell else "",
                is_title_bout=is_title_bout_row(weight_class_cell),
                winner_id=get_winning_fighter_id(result_flags, fighter_a_id, fighter_b_id),
            )
        )

    return fights


def parse_rankings_updated_text(soup: BeautifulSoup) -> str | None:
    text = soup.get_text("\n", strip=True)
    match = re.search(r"Last updated:\s*(.+)", text)
    if not match:
        return None

    return clean_text(match.group(1))


def parse_rankings_snapshot(soup: BeautifulSoup) -> tuple[dict[str, dict], str | None]:
    snapshot = build_empty_rankings_snapshot()

    for grouping in soup.select(".view-grouping"):
        header = grouping.select_one(".view-grouping-header")
        if header is None:
            continue

        division = clean_text(header.get_text(" ", strip=True))
        if division not in snapshot:
            continue

        champion_link = grouping.select_one(".rankings--athlete--champion .info h5 a")
        if champion_link is not None:
            snapshot[division]["champion"] = clean_text(champion_link.get_text(" ", strip=True))

        for row in grouping.select("tbody tr"):
            rank_cell = row.select_one(".views-field-weight-class-rank")
            fighter_cell = row.select_one(".views-field-title")
            if rank_cell is None or fighter_cell is None:
                continue

            fighter_name = clean_text(fighter_cell.get_text(" ", strip=True))
            if not fighter_name:
                continue

            snapshot[division]["ranked"][normalize_name(fighter_name)] = clean_text(
                rank_cell.get_text(" ", strip=True)
            )

    return snapshot, parse_rankings_updated_text(soup)


def build_empty_rankings_snapshot() -> dict[str, dict]:
    return {division: {"champion": None, "ranked": {}} for division, _ in DIVISIONS}


def load_committed_rankings_snapshot() -> tuple[dict[str, dict], str | None]:
    snapshot = build_empty_rankings_snapshot()
    rankings_updated_text: str | None = None

    for division, filename in DIVISIONS:
        data_path = OUTPUT_DIR / filename
        if not data_path.exists():
            continue

        payload = json.loads(data_path.read_text(encoding="utf-8"))
        if rankings_updated_text is None:
            rankings_updated_text = payload.get("meta", {}).get("rankings_updated_text")

        for node in payload.get("nodes", []):
            fighter_name = clean_text(str(node.get("label", "")))
            if not fighter_name:
                continue

            if node.get("isCurrentChampion"):
                snapshot[division]["champion"] = fighter_name

            current_rank = node.get("currentRank")
            if node.get("isCurrentlyRanked") and current_rank is not None:
                snapshot[division]["ranked"][normalize_name(fighter_name)] = str(current_rank)

    return snapshot, rankings_updated_text


def rankings_snapshot_has_data(snapshot: dict[str, dict]) -> bool:
    return any(
        division_snapshot.get("champion") or division_snapshot.get("ranked")
        for division_snapshot in snapshot.values()
    )


def load_rankings_snapshot(refresh_all: bool) -> tuple[dict[str, dict], str | None]:
    try:
        snapshot, rankings_updated_text = parse_rankings_snapshot(
            load_rankings_soup(refresh_all)
        )
    except RuntimeError as exc:
        if refresh_all:
            raise

        committed_snapshot, rankings_updated_text = load_committed_rankings_snapshot()
        if not rankings_snapshot_has_data(committed_snapshot):
            raise RuntimeError(
                "Failed to fetch UFC rankings and no committed rankings metadata was available."
            ) from exc

        print(
            "Warning: could not fetch live UFC rankings; "
            "reusing rankings metadata from committed docs/data files."
        )
        print(f"Rankings fetch error: {exc}")
        return committed_snapshot, rankings_updated_text

    if rankings_snapshot_has_data(snapshot):
        return snapshot, rankings_updated_text

    if refresh_all:
        raise RuntimeError("Fetched UFC rankings page did not contain ranking metadata.")

    committed_snapshot, committed_rankings_updated_text = load_committed_rankings_snapshot()
    if not rankings_snapshot_has_data(committed_snapshot):
        raise RuntimeError(
            "Fetched UFC rankings page did not contain ranking metadata and no "
            "committed rankings metadata was available."
        )

    print(
        "Warning: fetched UFC rankings page did not contain ranking metadata; "
        "reusing rankings metadata from committed docs/data files."
    )
    return committed_snapshot, committed_rankings_updated_text


def load_rankings_soup(refresh_all: bool) -> BeautifulSoup:
    if not refresh_all and RANKINGS_CACHE_PATH.exists():
        try:
            html = fetch_text(RANKINGS_URL)
            write_cached_text(RANKINGS_CACHE_PATH, html)
            return fetch_soup_from_text(html)
        except RuntimeError:
            return fetch_soup_from_text(read_cached_text(RANKINGS_CACHE_PATH))

    html = fetch_text(RANKINGS_URL)
    write_cached_text(RANKINGS_CACHE_PATH, html)
    return fetch_soup_from_text(html)


def build_athletes_url(page: int = 0, country_code: str | None = None) -> str:
    params: dict[str, str | int] = {}
    if country_code:
        params["filters[0]"] = f"location:{country_code}"
    if page > 0 or country_code:
        params["page"] = page
    if not params:
        return ATHLETES_URL
    return f"{ATHLETES_URL}?{urlencode(params)}"


def build_ufc_events_url(page: int = 0) -> str:
    if page <= 0:
        return UFC_EVENTS_URL
    return f"{UFC_EVENTS_URL}?{urlencode({'page': page})}"


def parse_ufc_event_links(soup: BeautifulSoup) -> list[str]:
    event_links: list[str] = []
    seen: set[str] = set()

    for anchor in soup.select('a[href*="/event/"]'):
        href = clean_text(anchor.get("href", ""))
        if not href:
            continue

        normalized_href = urljoin(UFC_URL, href)
        split_href = urlsplit(normalized_href)
        normalized_href = urlunsplit(
            (split_href.scheme, split_href.netloc, split_href.path.rstrip("/"), "", "")
        )
        if not normalized_href.startswith(f"{UFC_URL}/event/"):
            continue
        if normalized_href in seen:
            continue

        event_links.append(normalized_href)
        seen.add(normalized_href)

    return event_links


def load_ufc_events_page_soup(page: int, refresh_all: bool) -> tuple[BeautifulSoup, str]:
    cache_path = UFC_EVENTS_PAGE_CACHE_DIR / f"page-{page}.html"

    if cache_path.exists():
        cached_soup = fetch_soup_from_text(read_cached_text(cache_path))
    else:
        cached_soup = None

    should_refresh = refresh_all or cached_soup is None or page < RECENT_UFC_EVENTS_INDEX_REFRESH_COUNT
    if should_refresh:
        try:
            html = fetch_text(build_ufc_events_url(page))
            write_cached_text(cache_path, html)
            if cached_soup is None:
                return fetch_soup_from_text(html), "fetched"
            return fetch_soup_from_text(html), "refreshed"
        except RuntimeError:
            if cached_soup is None:
                raise
            return cached_soup, "cached"

    return cached_soup, "cached"


def collect_ufc_event_links(
    refresh_all: bool, max_pages: int | None = None
) -> tuple[list[str], Counter[str]]:
    event_links: list[str] = []
    seen: set[str] = set()
    page = 0
    fetch_stats: Counter[str] = Counter()

    while True:
        if max_pages is not None and page >= max_pages:
            break

        page_soup, source = load_ufc_events_page_soup(page, refresh_all)
        fetch_stats[source] += 1
        page_links = parse_ufc_event_links(page_soup)
        if not page_links:
            break

        new_links = 0
        for event_link in page_links:
            if event_link in seen:
                continue
            seen.add(event_link)
            event_links.append(event_link)
            new_links += 1

        print(
            f"Loaded UFC events archive page {page + 1}: "
            f"{new_links} new event pages ({source})"
        )
        if new_links == 0:
            break
        if page_soup.select_one('a[rel="next"]') is None:
            break
        page += 1

    if max_pages is not None and page >= max_pages:
        print(
            f"Stopped UFC events archive crawl after {max_pages} pages "
            "to keep flag resolution focused on recent official event cards."
        )

    print(
        "UFC events index cache summary: "
        f"{fetch_stats['cached']} cached, "
        f"{fetch_stats['fetched']} fetched, "
        f"{fetch_stats['refreshed']} refreshed."
    )
    return event_links, fetch_stats


def load_ufc_event_card_soup(
    event_link: str, index: int, refresh_all: bool
) -> tuple[BeautifulSoup, str]:
    event_id = get_event_id(event_link)
    cache_path = UFC_EVENT_CARD_CACHE_DIR / f"{event_id}.html"

    if cache_path.exists():
        cached_soup = fetch_soup_from_text(read_cached_text(cache_path))
    else:
        cached_soup = None

    should_refresh = (
        refresh_all or cached_soup is None or index < RECENT_UFC_EVENT_CARD_REFRESH_COUNT
    )
    if should_refresh:
        try:
            html = fetch_text(event_link)
            write_cached_text(cache_path, html)
            if cached_soup is None:
                return fetch_soup_from_text(html), "fetched"
            return fetch_soup_from_text(html), "refreshed"
        except RuntimeError:
            if cached_soup is None:
                raise
            return cached_soup, "cached"

    return cached_soup, "cached"


def parse_ufc_event_flag_map(soup: BeautifulSoup) -> dict[str, CountryInfo]:
    fighter_flags: dict[str, CountryInfo] = {}

    for fight in soup.select(".c-listing-fight"):
        for corner in ("red", "blue"):
            name_node = fight.select_one(f".c-listing-fight__corner-name--{corner}")
            country_node = fight.select_one(f".c-listing-fight__country--{corner}")
            if name_node is None or country_node is None:
                continue

            fighter_name = clean_text(name_node.get_text(" ", strip=True))
            country_name_node = country_node.select_one(".c-listing-fight__country-text")
            flag_image_node = country_node.select_one("img")
            if not fighter_name or country_name_node is None or flag_image_node is None:
                continue

            country_name = clean_text(country_name_node.get_text(" ", strip=True))
            flag_src = clean_text(flag_image_node.get("src", ""))
            flag_match = re.search(r"/flags/([A-Z]{2})\.", flag_src)
            country_code = flag_match.group(1) if flag_match else None

            country_info = build_country_info(country_name=country_name, country_code=country_code)
            if country_info is None:
                continue

            fighter_flags[normalize_name(fighter_name)] = country_info

    return fighter_flags


def build_fighter_event_flag_map(
    required_names: set[str], refresh_all: bool
) -> dict[str, CountryInfo]:
    event_links, _ = collect_ufc_event_links(
        refresh_all, max_pages=MAX_UFC_EVENT_ARCHIVE_PAGES
    )
    fighter_flags: dict[str, CountryInfo] = {}
    event_fetch_stats: Counter[str] = Counter()
    unresolved_required_names = set(required_names)

    for index, event_link in enumerate(event_links):
        event_soup, source = load_ufc_event_card_soup(event_link, index, refresh_all)
        event_fetch_stats[source] += 1
        event_flags = parse_ufc_event_flag_map(event_soup)
        for fighter_name_key, country_info in event_flags.items():
            if fighter_name_key not in required_names or fighter_name_key in fighter_flags:
                continue
            fighter_flags[fighter_name_key] = country_info
            unresolved_required_names.discard(fighter_name_key)

        if not unresolved_required_names:
            print(
                f"Resolved all required fighter flags after scanning {index + 1} recent UFC event pages."
            )
            break

    print(
        f"Resolved official UFC event-card flags for {len(fighter_flags)} fighters "
        f"from up to {len(event_links)} recent event pages."
    )
    print(
        "UFC event-card cache summary: "
        f"{event_fetch_stats['cached']} cached, "
        f"{event_fetch_stats['fetched']} fetched, "
        f"{event_fetch_stats['refreshed']} refreshed."
    )
    return fighter_flags


def parse_athlete_directory_page(soup: BeautifulSoup) -> list[tuple[str, str]]:
    athletes: list[tuple[str, str]] = []

    for item in soup.select("li.l-flex__item"):
        name_node = item.select_one(".c-listing-athlete__name")
        link_node = item.select_one("a[href*='/athlete/']")
        if name_node is None or link_node is None:
            continue

        athlete_name = clean_text(name_node.get_text(" ", strip=True))
        athlete_link = clean_text(link_node.get("href", ""))
        if not athlete_name or not athlete_link:
            continue

        athletes.append((athlete_name, athlete_link))

    return athletes


def parse_athlete_location_filters(soup: BeautifulSoup) -> list[tuple[str, str, int]]:
    countries: list[tuple[str, str, int]] = []

    for anchor in soup.select(
        '[data-drupal-facet-id="athletes_residence_country_code"] a[data-drupal-facet-item-value]'
    ):
        country_code = clean_text(anchor.get("data-drupal-facet-item-value", "")).upper()
        country_name = clean_text(anchor.get_text(" ", strip=True))
        count_text = clean_text(anchor.get("data-drupal-facet-item-count", "0"))

        try:
            athlete_count = int(count_text)
        except ValueError:
            athlete_count = 0

        if not country_code or not country_name or athlete_count <= 0:
            continue

        countries.append((country_code, country_name, athlete_count))

    return countries


def crawl_athlete_directory() -> dict[str, dict[str, str]]:
    athlete_directory: dict[str, dict[str, str]] = {}
    page = 0

    while True:
        soup = fetch_soup(build_athletes_url(page=page))
        page_entries = parse_athlete_directory_page(soup)
        if not page_entries:
            break

        for athlete_name, athlete_link in page_entries:
            athlete_directory[normalize_name(athlete_name)] = {
                "name": athlete_name,
                "href": athlete_link,
            }

        print(f"Loaded athlete directory page {page + 1}: {len(page_entries)} athletes")
        if soup.select_one('a[rel="next"]') is None:
            break
        page += 1

    return athlete_directory


def load_or_build_athlete_directory(
    refresh_all: bool, required_names: set[str]
) -> dict[str, dict[str, str]]:
    if not refresh_all and ATHLETE_DIRECTORY_CACHE_PATH.exists():
        cached_payload = read_cached_json(ATHLETE_DIRECTORY_CACHE_PATH)
        cached_entries = cached_payload.get("entries", {})
        if required_names.issubset(cached_entries):
            return cached_entries
        if cache_is_recent(cached_payload, ATHLETE_DIRECTORY_REFRESH_DAYS):
            print(
                "Athlete directory cache is missing some fighters, "
                "but the cache is still fresh enough to reuse."
            )
            return cached_entries
        print("Athlete directory cache is stale and missing fighters from the current graph. Refreshing it.")

    athlete_directory = crawl_athlete_directory()
    write_cached_json(
        ATHLETE_DIRECTORY_CACHE_PATH,
        {
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "entries": athlete_directory,
        },
    )
    return athlete_directory


def crawl_athlete_location_map() -> dict[str, dict[str, str]]:
    root_soup = fetch_soup(ATHLETES_URL)
    countries = parse_athlete_location_filters(root_soup)
    athlete_locations: dict[str, dict[str, str]] = {}

    for index, (country_code, country_name, athlete_count) in enumerate(countries, start=1):
        total_pages = max(
            1,
            (athlete_count + ATHLETE_DIRECTORY_PAGE_SIZE - 1) // ATHLETE_DIRECTORY_PAGE_SIZE,
        )
        print(
            f"[{index}/{len(countries)}] Loading {country_name} athlete pages "
            f"({athlete_count} athletes across {total_pages} pages)"
        )

        for page in range(total_pages):
            page_soup = fetch_soup(build_athletes_url(page=page, country_code=country_code))
            for athlete_name, _ in parse_athlete_directory_page(page_soup):
                athlete_locations.setdefault(
                    normalize_name(athlete_name),
                    {
                        "countryCode": country_code,
                        "countryName": country_name,
                    },
                )

    return athlete_locations


def load_or_build_athlete_location_map(refresh_all: bool) -> dict[str, dict[str, str]]:
    if not refresh_all and ATHLETE_LOCATION_CACHE_PATH.exists():
        return read_cached_json(ATHLETE_LOCATION_CACHE_PATH).get("entries", {})

    athlete_locations = crawl_athlete_location_map()
    write_cached_json(
        ATHLETE_LOCATION_CACHE_PATH,
        {
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "entries": athlete_locations,
        },
    )
    return athlete_locations


def get_athlete_slug(athlete_link: str) -> str:
    return athlete_link.rstrip("/").rsplit("/", 1)[-1]


def load_athlete_profile_soup(athlete_link: str, refresh_all: bool) -> tuple[BeautifulSoup, str]:
    slug = get_athlete_slug(athlete_link)
    cache_path = ATHLETE_CACHE_DIR / f"{slug}.html"

    if not refresh_all and cache_path.exists():
        return fetch_soup_from_text(read_cached_text(cache_path)), "cached"

    athlete_url = urljoin(UFC_URL, athlete_link)
    if cache_path.exists():
        try:
            html = fetch_text(athlete_url)
            write_cached_text(cache_path, html)
            return fetch_soup_from_text(html), "refreshed"
        except RuntimeError:
            return fetch_soup_from_text(read_cached_text(cache_path)), "cached"

    html = fetch_text(athlete_url)
    write_cached_text(cache_path, html)
    return fetch_soup_from_text(html), "fetched"


def parse_country_name_from_athlete_profile(soup: BeautifulSoup) -> str | None:
    bio_fields: dict[str, str] = {}
    for field in soup.select(".c-bio__field"):
        label_node = field.select_one(".c-bio__label")
        value_node = field.select_one(".c-bio__text")
        if label_node is None or value_node is None:
            continue

        label = clean_text(label_node.get_text(" ", strip=True)).casefold()
        value = clean_text(value_node.get_text(" ", strip=True))
        if label and value:
            bio_fields[label] = value

    location_text = bio_fields.get("place of birth") or bio_fields.get("from")
    if not location_text:
        return None

    location_parts = [clean_text(part) for part in location_text.split(",") if clean_text(part)]
    if not location_parts:
        return None

    return location_parts[-1]


def flag_emoji_from_country_code(country_code: str) -> str | None:
    if len(country_code) != 2 or not country_code.isalpha():
        return None

    base = 127397
    return "".join(chr(base + ord(letter)) for letter in country_code.upper())


def country_name_to_code(country_name: str) -> str | None:
    normalized_country_name = normalize_name(country_name)
    if not normalized_country_name:
        return None

    override = COUNTRY_NAME_OVERRIDES.get(normalized_country_name)
    if override is not None:
        return override

    if pycountry is None:
        raise RuntimeError("pycountry is required to map athlete countries. Install requirements.txt.")

    country = pycountry.countries.get(name=country_name)
    if country is None:
        try:
            country = pycountry.countries.search_fuzzy(country_name)[0]
        except LookupError:
            return None

    return getattr(country, "alpha_2", None)


def normalize_country_code(country_code: str | None, country_name: str) -> str | None:
    if not country_code:
        return country_name_to_code(country_name)

    normalized_country_code = clean_text(country_code).upper()
    override_country_code = COUNTRY_CODE_OVERRIDES.get(normalized_country_code)
    if override_country_code is not None:
        return override_country_code

    if pycountry is not None and pycountry.countries.get(alpha_2=normalized_country_code) is not None:
        return normalized_country_code

    return country_name_to_code(country_name)


def build_country_info(country_name: str, country_code: str | None = None) -> CountryInfo | None:
    resolved_country_code = normalize_country_code(country_code, country_name)
    if not resolved_country_code:
        return None

    flag_emoji = flag_emoji_from_country_code(resolved_country_code)
    if not flag_emoji:
        return None

    return CountryInfo(
        code=resolved_country_code.upper(),
        name=clean_text(country_name),
        flag_emoji=flag_emoji,
    )


def collect_fighter_names(all_fights: Iterable[EventFight]) -> dict[str, str]:
    fighter_names: dict[str, str] = {}

    for fight in all_fights:
        fighter_names.setdefault(fight.fighter_a_id, fight.fighter_a_name)
        fighter_names.setdefault(fight.fighter_b_id, fight.fighter_b_name)

    return fighter_names


def load_committed_fighter_country_map(required_names: set[str]) -> dict[str, CountryInfo]:
    fighter_countries: dict[str, CountryInfo] = {}

    for _, filename in DIVISIONS:
        data_path = OUTPUT_DIR / filename
        if not data_path.exists():
            continue

        payload = json.loads(data_path.read_text(encoding="utf-8"))
        for node in payload.get("nodes", []):
            fighter_name_key = normalize_name(str(node.get("label", "")))
            if not fighter_name_key or fighter_name_key not in required_names:
                continue
            if fighter_name_key in fighter_countries:
                continue

            country_code = node.get("countryCode")
            country_name = node.get("countryName")
            if not isinstance(country_code, str) or not isinstance(country_name, str):
                continue

            country_info = build_country_info(country_name=country_name, country_code=country_code)
            if country_info is not None:
                fighter_countries[fighter_name_key] = country_info

    return fighter_countries


def build_fighter_country_map(
    fighter_names: dict[str, str], refresh_all: bool
) -> dict[str, CountryInfo]:
    required_names = {normalize_name(fighter_name) for fighter_name in fighter_names.values()}
    committed_fighter_countries = load_committed_fighter_country_map(required_names)

    try:
        fighter_event_flags = build_fighter_event_flag_map(required_names, refresh_all)
    except RuntimeError as exc:
        if refresh_all:
            raise
        print(
            "Warning: could not fetch UFC event-card flag metadata; "
            "using committed country metadata where available."
        )
        print(f"UFC event-card fetch error: {exc}")
        fighter_event_flags = {}

    try:
        athlete_directory = load_or_build_athlete_directory(refresh_all, required_names)
    except RuntimeError as exc:
        if refresh_all:
            raise
        print(
            "Warning: could not fetch UFC athlete directory metadata; "
            "using committed country metadata where available."
        )
        print(f"UFC athlete directory fetch error: {exc}")
        athlete_directory = {}

    try:
        athlete_locations = load_or_build_athlete_location_map(refresh_all)
    except RuntimeError as exc:
        if refresh_all:
            raise
        print(
            "Warning: could not fetch UFC athlete location metadata; "
            "using committed country metadata where available."
        )
        print(f"UFC athlete location fetch error: {exc}")
        athlete_locations = {}

    fighter_countries: dict[str, CountryInfo] = {}
    profile_cache_stats: Counter[str] = Counter()
    unresolved_names: list[str] = []

    for fighter_name_key in sorted(required_names):
        event_flag = fighter_event_flags.get(fighter_name_key)
        if event_flag is not None:
            fighter_countries[fighter_name_key] = event_flag
            continue

        manual_country = FIGHTER_COUNTRY_OVERRIDES.get(fighter_name_key)
        if manual_country is not None:
            country_info = build_country_info(
                country_name=manual_country["countryName"],
                country_code=manual_country["countryCode"],
            )
            if country_info is not None:
                fighter_countries[fighter_name_key] = country_info
                continue

        cached_country = athlete_locations.get(fighter_name_key)
        if cached_country is not None:
            country_info = build_country_info(
                country_name=cached_country["countryName"],
                country_code=cached_country["countryCode"],
            )
            if country_info is not None:
                fighter_countries[fighter_name_key] = country_info
                continue

        committed_country = committed_fighter_countries.get(fighter_name_key)
        if committed_country is not None:
            fighter_countries[fighter_name_key] = committed_country
            continue

        athlete_entry = athlete_directory.get(fighter_name_key)
        if athlete_entry is None:
            unresolved_names.append(fighter_name_key)
            continue

        try:
            athlete_soup, source = load_athlete_profile_soup(athlete_entry["href"], refresh_all)
        except RuntimeError as exc:
            if refresh_all:
                raise
            print(f"Warning: could not fetch athlete profile for {fighter_name_key}: {exc}")
            unresolved_names.append(fighter_name_key)
            continue

        profile_cache_stats[source] += 1
        profile_country_name = parse_country_name_from_athlete_profile(athlete_soup)
        if not profile_country_name:
            unresolved_names.append(fighter_name_key)
            continue

        country_info = build_country_info(profile_country_name)
        if country_info is None:
            unresolved_names.append(fighter_name_key)
            continue

        fighter_countries[fighter_name_key] = country_info

    print(
        f"Resolved fighter countries for {len(fighter_countries)} of {len(required_names)} fighters."
    )
    print(
        f"Official UFC event-card flags covered "
        f"{sum(1 for name in required_names if name in fighter_event_flags)} of {len(required_names)} fighters."
    )
    if committed_fighter_countries:
        print(
            f"Committed country metadata covered "
            f"{sum(1 for name in required_names if name in committed_fighter_countries)} "
            f"of {len(required_names)} fighters."
        )
    if profile_cache_stats:
        print(
            "Athlete profile cache summary: "
            f"{profile_cache_stats['cached']} cached, "
            f"{profile_cache_stats['fetched']} fetched, "
            f"{profile_cache_stats['refreshed']} refreshed."
        )
    if unresolved_names:
        unresolved_preview = ", ".join(unresolved_names[:12])
        print(
            f"Could not resolve country data for {len(unresolved_names)} fighters: "
            f"{unresolved_preview}"
        )

    return fighter_countries


def load_event_soup(event_link: str, index: int, refresh_all: bool) -> tuple[BeautifulSoup, str]:
    event_id = get_event_id(event_link)
    cache_path = EVENT_CACHE_DIR / f"{event_id}.html"
    cached_soup: BeautifulSoup | None = None

    if cache_path.exists():
        cached_soup = fetch_soup_from_text(read_cached_text(cache_path))

    should_refresh = (
        refresh_all
        or cached_soup is None
        or index < RECENT_EVENT_REFRESH_COUNT
        or is_future_event_soup(cached_soup)
    )

    if should_refresh:
        try:
            html = fetch_text(event_link)
            write_cached_text(cache_path, html)
            if cached_soup is None:
                return fetch_soup_from_text(html), "fetched"
            return fetch_soup_from_text(html), "refreshed"
        except RuntimeError:
            if cached_soup is None:
                raise
            return cached_soup, "cached"

    return cached_soup, "cached"


def collect_fights(refresh_all: bool) -> tuple[list[EventFight], date, Counter[str]]:
    events_page = fetch_soup(EVENTS_URL)
    event_links = parse_completed_event_links(events_page)

    all_fights: list[EventFight] = []
    latest_event_date: date | None = None
    completed_events = 0
    fetch_stats: Counter[str] = Counter()

    for index, event_link in enumerate(event_links, start=1):
        event_soup, source = load_event_soup(event_link, index - 1, refresh_all)
        fetch_stats[source] += 1
        event_name = parse_event_name_text(event_soup) or "UFC event"
        event_date_value = parse_event_date_value(parse_event_date_text(event_soup))
        event_date = event_date_value.isoformat() if event_date_value is not None else None
        event_fights = parse_event_fights(event_soup, event_name, event_date)
        if not event_fights:
            continue

        completed_events += 1
        if event_date_value is not None and (
            latest_event_date is None or event_date_value > latest_event_date
        ):
            latest_event_date = event_date_value

        all_fights.extend(event_fights)
        print(
            f"[{index}/{len(event_links)}] {event_link} -> "
            f"{len(event_fights)} supported bouts ({source})"
        )

    if latest_event_date is None:
        raise RuntimeError("No completed event data found on UFC Stats.")

    print(f"Collected {len(all_fights)} supported bouts across {completed_events} events.")
    print(
        "Event page cache summary: "
        f"{fetch_stats['cached']} cached, "
        f"{fetch_stats['fetched']} fetched, "
        f"{fetch_stats['refreshed']} refreshed."
    )
    return all_fights, latest_event_date, fetch_stats


def build_graph_payload(
    division: str,
    filename: str,
    fights: Iterable[EventFight],
    latest_event_date: date,
    rankings_snapshot: dict[str, dict],
    rankings_updated_text: str | None,
    fighter_countries: dict[str, CountryInfo],
) -> dict:
    fighter_names: dict[str, str] = {}
    fighter_fight_counts: Counter[str] = Counter()
    unique_opponents: dict[str, set[str]] = {}
    matchup_counts: Counter[tuple[str, str]] = Counter()
    matchup_bouts: dict[tuple[str, str], list[dict[str, str | bool | None]]] = {}
    title_bout_participants: set[str] = set()
    title_bout_winners: set[str] = set()
    division_rankings = rankings_snapshot.get(division, {"champion": None, "ranked": {}})
    champion_name = division_rankings.get("champion")
    champion_key = normalize_name(champion_name) if champion_name else None
    ranked_names = division_rankings.get("ranked", {})

    for fight in fights:
        fighter_names.setdefault(fight.fighter_a_id, fight.fighter_a_name)
        fighter_names.setdefault(fight.fighter_b_id, fight.fighter_b_name)
        fighter_fight_counts[fight.fighter_a_id] += 1
        fighter_fight_counts[fight.fighter_b_id] += 1
        unique_opponents.setdefault(fight.fighter_a_id, set()).add(fight.fighter_b_id)
        unique_opponents.setdefault(fight.fighter_b_id, set()).add(fight.fighter_a_id)

        edge = tuple(sorted((fight.fighter_a_id, fight.fighter_b_id)))
        matchup_counts[edge] += 1
        matchup_bouts.setdefault(edge, []).append(
            {
                "eventName": fight.event_name,
                "eventDate": fight.event_date,
                "resultSummary": fight.result_summary,
                "method": fight.method,
                "round": fight.round,
                "time": fight.time,
                "isTitleBout": fight.is_title_bout,
            }
        )
        if fight.is_title_bout:
            title_bout_participants.add(fight.fighter_a_id)
            title_bout_participants.add(fight.fighter_b_id)
            if fight.winner_id is not None:
                title_bout_winners.add(fight.winner_id)

    nodes = []
    for fighter_id in sorted(fighter_names, key=lambda value: fighter_names[value].casefold()):
        fighter_name = fighter_names[fighter_id]
        fighter_key = normalize_name(fighter_name)
        current_rank = ranked_names.get(fighter_key)
        country_info = fighter_countries.get(fighter_key)
        won_division_title = fighter_id in title_bout_winners
        fought_for_division_title = fighter_id in title_bout_participants
        nodes.append(
            {
                "id": fighter_id,
                "label": fighter_name,
                "group": division,
                "fightCount": fighter_fight_counts[fighter_id],
                "uniqueOpponentCount": len(unique_opponents.get(fighter_id, set())),
                "isCurrentChampion": fighter_key == champion_key,
                "isCurrentlyRanked": current_rank is not None,
                "currentRank": current_rank,
                "isFormerChampion": won_division_title and fighter_key != champion_key,
                "isFormerTitleChallenger": fought_for_division_title and not won_division_title,
                "hasTitleFightInDivision": fought_for_division_title,
                "countryCode": country_info.code if country_info else None,
                "countryName": country_info.name if country_info else None,
                "flagEmoji": country_info.flag_emoji if country_info else None,
            }
        )

    links = [
        {
            "source": source,
            "target": target,
            "value": matchup_counts[(source, target)],
            "bouts": matchup_bouts.get((source, target), []),
        }
        for source, target in sorted(
            matchup_counts,
            key=lambda pair: (
                fighter_names[pair[0]].casefold(),
                fighter_names[pair[1]].casefold(),
            ),
        )
    ]

    return {
        "meta": {
            "division": division,
            "filename": filename,
            "source_name": "UFC Stats",
            "source_url": EVENTS_URL,
            "ranking_source_name": "UFC.com Rankings",
            "ranking_source_url": RANKINGS_URL,
            "rankings_updated_text": rankings_updated_text,
            "latest_resolved_event_date": latest_event_date.isoformat(),
            "fighter_count": len(nodes),
            "matchup_count": len(links),
            "bout_count": sum(matchup_counts.values()),
        },
        "nodes": nodes,
        "links": links,
    }


def write_payloads(
    all_fights: list[EventFight],
    latest_event_date: date,
    rankings_snapshot: dict[str, dict],
    rankings_updated_text: str | None,
    fighter_countries: dict[str, CountryInfo],
) -> None:
    fights_by_division: dict[str, list[EventFight]] = {division: [] for division, _ in DIVISIONS}
    for fight in all_fights:
        fights_by_division[fight.division].append(fight)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for division, filename in DIVISIONS:
        payload = build_graph_payload(
            division=division,
            filename=filename,
            fights=fights_by_division[division],
            latest_event_date=latest_event_date,
            rankings_snapshot=rankings_snapshot,
            rankings_updated_text=rankings_updated_text,
            fighter_countries=fighter_countries,
        )
        destination = OUTPUT_DIR / filename
        destination.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(
            f"Wrote {destination.name}: "
            f"{payload['meta']['fighter_count']} fighters, "
            f"{payload['meta']['matchup_count']} unique matchups, "
            f"{payload['meta']['bout_count']} total bouts"
        )


def main() -> None:
    args = parse_args()
    rankings_snapshot, rankings_updated_text = load_rankings_snapshot(args.refresh_all)
    fights, latest_event_date, _ = collect_fights(args.refresh_all)
    fighter_countries = build_fighter_country_map(collect_fighter_names(fights), args.refresh_all)
    write_payloads(
        fights,
        latest_event_date,
        rankings_snapshot,
        rankings_updated_text,
        fighter_countries,
    )


if __name__ == "__main__":
    main()
