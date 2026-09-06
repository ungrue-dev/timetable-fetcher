"""Download, parse, compare, and atomically publish a timetable snapshot."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from html.parser import HTMLParser
import json
import logging
import os
from pathlib import Path
import re
import ssl
import tempfile
import time as time_module
from typing import Callable, Iterator
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import certifi


SOURCE_URL = "https://sb.bsu.by/raspisanie/map-653__engl_.xml"
GROUP_ID = "653"
TIMEZONE_NAME = "Europe/Minsk"
CLASS_DURATION_MINUTES = 85
SCHEMA_VERSION = 1
USER_AGENT = "BSU-Timetable-Fetcher/1.0 (local timetable app)"
HTML_RELAY_URL = "https://r.jina.ai/"

DATE_PATTERN = re.compile(r"\b(\d{2}\.\d{2}\.\d{4})\b")
TIME_PATTERN = re.compile(r"^(\d{2}):(\d{2})$")
UPDATED_PATTERN = re.compile(
    r"Дата\s+обновления\s+(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})",
    re.IGNORECASE,
)


class TimetableError(Exception):
    """Base error for failures that must not replace the current snapshot."""


class FetchError(TimetableError):
    """Raised when all download attempts fail."""


class ParseError(TimetableError):
    """Raised when the source does not match the expected timetable structure."""


@dataclass
class Node:
    tag: str
    attrs: dict[str, str]
    children: list[Node | str] = field(default_factory=list)


class _TreeBuilder(HTMLParser):
    """Small dependency-free HTML tree builder sufficient for the source page."""

    VOID_TAGS = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("document", {})
        self._stack = [self.root]

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        node = Node(tag.lower(), {key: value or "" for key, value in attrs})
        self._stack[-1].children.append(node)
        if node.tag not in self.VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        node = Node(tag.lower(), {key: value or "" for key, value in attrs})
        self._stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        wanted = tag.lower()
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == wanted:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self._stack[-1].children.append(data)


def _classes(node: Node) -> set[str]:
    return set(node.attrs.get("class", "").split())


def _walk(node: Node) -> Iterator[Node]:
    for child in node.children:
        if isinstance(child, Node):
            yield child
            yield from _walk(child)


def _find_all(
    node: Node, *, tag: str | None = None, class_name: str | None = None
) -> list[Node]:
    return [
        candidate
        for candidate in _walk(node)
        if (tag is None or candidate.tag == tag)
        and (class_name is None or class_name in _classes(candidate))
    ]


def _direct_children(node: Node, tag: str) -> list[Node]:
    return [
        child
        for child in node.children
        if isinstance(child, Node) and child.tag == tag
    ]


def _text_parts(node: Node) -> list[str]:
    parts: list[str] = []
    for child in node.children:
        if isinstance(child, str):
            parts.append(child)
        elif child.tag == "br":
            parts.append("\n")
        else:
            parts.extend(_text_parts(child))
    return parts


def _text(node: Node) -> str:
    return " ".join("".join(_text_parts(node)).split())


def _lines(node: Node) -> list[str]:
    raw = "".join(_text_parts(node))
    return [" ".join(line.split()) for line in raw.splitlines() if line.strip()]


def _parse_date(value: str, context: str) -> date:
    try:
        return datetime.strptime(value, "%d.%m.%Y").date()
    except ValueError as error:
        raise ParseError(f"Invalid {context}: {value!r}") from error


def _parse_time(value: str) -> time:
    match = TIME_PATTERN.fullmatch(value)
    if match is None:
        raise ParseError(f"Invalid class start time: {value!r}")
    try:
        return time(int(match.group(1)), int(match.group(2)))
    except ValueError as error:
        raise ParseError(f"Invalid class start time: {value!r}") from error


def _parse_tree(html: str) -> Node:
    parser = _TreeBuilder()
    try:
        parser.feed(html)
        parser.close()
    except Exception as error:
        raise ParseError("The timetable response is not valid HTML") from error
    return parser.root


def _source_updated_at(html: str, timezone: ZoneInfo) -> str:
    match = UPDATED_PATTERN.search(html)
    if match is None:
        raise ParseError("Source update timestamp was not found")
    try:
        updated = datetime.strptime(
            f"{match.group(1)} {match.group(2)}", "%d.%m.%Y %H:%M"
        ).replace(tzinfo=timezone)
    except ValueError as error:
        raise ParseError("Source update timestamp is invalid") from error
    return updated.isoformat()


def _declared_week_starts(root: Node) -> list[date]:
    containers = _find_all(root, class_name="schedule-days")
    if len(containers) != 1:
        raise ParseError("Expected exactly one list of available weeks")

    starts: list[date] = []
    seen: set[date] = set()
    for span in _find_all(containers[0], tag="span"):
        raw = span.attrs.get("vl", "").strip()
        if not raw:
            continue
        parsed = _parse_date(raw, "week start")
        if parsed not in seen:
            seen.add(parsed)
            starts.append(parsed)
    if not starts:
        raise ParseError("No available timetable weeks were found")
    return sorted(starts)


def parse_timetable(
    html: str,
    *,
    source_url: str = SOURCE_URL,
    published_at: datetime | None = None,
) -> dict[str, object]:
    """Parse all published weeks and return the versioned JSON-ready payload."""

    timezone = ZoneInfo(TIMEZONE_NAME)
    root = _parse_tree(html)
    tables = _find_all(root, tag="table", class_name="table-schedule")
    if len(tables) != 1:
        raise ParseError("Expected exactly one timetable table")

    headings = _find_all(root, tag="h1")
    if not headings or not _text(headings[0]):
        raise ParseError("Timetable group name was not found")
    group_name = _text(headings[0])

    declared_starts = _declared_week_starts(root)
    weeks: dict[date, dict[date, list[dict[str, object]]]] = {
        start: {} for start in declared_starts
    }
    bodies = _find_all(tables[0], tag="tbody")
    if len(bodies) != 1:
        raise ParseError("Expected exactly one timetable body")

    current_day: date | None = None
    class_count = 0
    for row in _direct_children(bodies[0], "tr"):
        raw_week = row.attrs.get("vl", "").strip()
        if not raw_week:
            raise ParseError("A timetable row has no week identifier")
        week_start = _parse_date(raw_week, "row week")
        if week_start not in weeks:
            raise ParseError(f"Row refers to undeclared week {raw_week!r}")

        date_cells = _find_all(row, tag="td", class_name="head-date")
        if date_cells:
            match = DATE_PATTERN.search(_text(date_cells[0]))
            if match is None:
                raise ParseError("A day heading has no date")
            current_day = _parse_date(match.group(1), "class date")
            weeks[week_start].setdefault(current_day, [])
            continue

        cells = _direct_children(row, "td")
        if len(cells) != 4:
            raise ParseError(
                f"Expected 4 cells in a class row, found {len(cells)}"
            )
        if current_day is None:
            raise ParseError("A class row appears before its day heading")

        time_lines = _lines(cells[0])
        if not time_lines:
            raise ParseError("A class row has no start time")
        start_time = _parse_time(time_lines[0])
        subgroup = " ".join(time_lines[1:]) or None
        starts_at = datetime.combine(current_day, start_time, tzinfo=timezone)
        ends_at = starts_at + timedelta(minutes=CLASS_DURATION_MINUTES)

        subject, teacher, room = (_text(cell) for cell in cells[1:])
        if not subject:
            raise ParseError("A class row has no subject")
        weeks[week_start].setdefault(current_day, []).append(
            {
                "start_time": starts_at.strftime("%H:%M"),
                "end_time": ends_at.strftime("%H:%M"),
                "starts_at": starts_at.isoformat(),
                "ends_at": ends_at.isoformat(),
                "subject": subject,
                "teacher": teacher,
                "room": room,
                "subgroup": subgroup,
            }
        )
        class_count += 1

    if class_count == 0:
        raise ParseError("The timetable contains no classes")

    week_payloads: list[dict[str, object]] = []
    for week_start in sorted(weeks):
        day_payloads: list[dict[str, object]] = []
        for class_date in sorted(weeks[week_start]):
            classes = sorted(
                weeks[week_start][class_date],
                key=lambda item: (
                    str(item["starts_at"]),
                    str(item["subgroup"] or ""),
                    str(item["subject"]),
                    str(item["teacher"]),
                    str(item["room"]),
                ),
            )
            day_payloads.append({"date": class_date.isoformat(), "classes": classes})
        week_payloads.append(
            {"starts_on": week_start.isoformat(), "days": day_payloads}
        )

    publication_time = published_at or datetime.now(timezone)
    if publication_time.tzinfo is None:
        publication_time = publication_time.replace(tzinfo=timezone)
    else:
        publication_time = publication_time.astimezone(timezone)

    return {
        "schema_version": SCHEMA_VERSION,
        "group": {"id": GROUP_ID, "name": group_name},
        "source_url": source_url,
        "timezone": TIMEZONE_NAME,
        "class_duration_minutes": CLASS_DURATION_MINUTES,
        "source_updated_at": _source_updated_at(html, timezone),
        "published_at": publication_time.replace(microsecond=0).isoformat(),
        "weeks": week_payloads,
    }


def fetch_html(
    url: str = SOURCE_URL,
    *,
    attempts: int = 3,
    timeout: float = 20.0,
    opener: Callable[..., object] | None = None,
    sleep: Callable[[float], None] = time_module.sleep,
) -> str:
    """Download HTML with bounded retries and exponential backoff."""

    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    if timeout <= 0:
        raise ValueError("timeout must be greater than 0")

    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    relay_request = None
    if url == SOURCE_URL:
        relay_request = Request(
            f"{HTML_RELAY_URL}{url}",
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "X-Return-Format": "html",
            },
        )
    if opener is None:
        context = ssl.create_default_context(cafile=certifi.where())

        def trusted_opener(request: Request, *, timeout: float) -> object:
            return urlopen(request, timeout=timeout, context=context)

        opener = trusted_opener

    active_request = request
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with opener(active_request, timeout=timeout) as response:  # type: ignore[attr-defined]
                body = response.read()
                headers = getattr(response, "headers", None)
                charset = (
                    headers.get_content_charset() if headers is not None else None
                )
                return body.decode(charset or "utf-8")
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeError) as error:
            last_error = error
            if (
                active_request is request
                and relay_request is not None
                and _can_use_html_relay(error)
            ):
                logging.getLogger(__name__).warning(
                    "Direct BSU request failed; trying HTTPS HTML relay: %s", error
                )
                active_request = relay_request
                try:
                    with opener(active_request, timeout=timeout) as response:  # type: ignore[attr-defined]
                        body = response.read()
                        headers = getattr(response, "headers", None)
                        charset = (
                            headers.get_content_charset()
                            if headers is not None
                            else None
                        )
                        return body.decode(charset or "utf-8")
                except (
                    HTTPError,
                    URLError,
                    TimeoutError,
                    OSError,
                    UnicodeError,
                ) as relay_error:
                    last_error = relay_error
            if attempt + 1 < attempts:
                sleep(2**attempt)

    detail = f": {last_error}" if last_error is not None else ""
    raise FetchError(
        f"Could not fetch {url} after {attempts} attempts{detail}"
    ) from last_error


def _can_use_html_relay(error: Exception) -> bool:
    """Use the public HTML relay for protocol mismatches and timed-out connections."""

    reason = error.reason if isinstance(error, URLError) else error
    if isinstance(reason, TimeoutError):
        return True
    return isinstance(reason, ssl.SSLError) and any(
        marker in str(reason).upper()
        for marker in ("WRONG_VERSION_NUMBER", "PROTOCOL_VERSION")
    )


def _semantic_payload(payload: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in payload.items() if key != "published_at"}


def _read_existing(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeError):
        return None
    return value if isinstance(value, dict) else None


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            json.dump(payload, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


def update_snapshot(
    output: str | Path,
    html: str,
    *,
    source_url: str = SOURCE_URL,
    published_at: datetime | None = None,
) -> bool:
    """Parse HTML and atomically update output only when semantic data changed."""

    output_path = Path(output)
    payload = parse_timetable(
        html, source_url=source_url, published_at=published_at
    )
    existing = _read_existing(output_path)
    if existing is not None and _semantic_payload(existing) == _semantic_payload(payload):
        return False
    _atomic_write_json(output_path, payload)
    return True


def refresh_snapshot(
    output: str | Path,
    *,
    source_url: str = SOURCE_URL,
    attempts: int = 3,
    timeout: float = 20.0,
    opener: Callable[..., object] | None = None,
    sleep: Callable[[float], None] = time_module.sleep,
    published_at: datetime | None = None,
) -> bool:
    """Fetch the source and update the snapshot after complete validation."""

    html = fetch_html(
        source_url,
        attempts=attempts,
        timeout=timeout,
        opener=opener,
        sleep=sleep,
    )
    return update_snapshot(
        output,
        html,
        source_url=source_url,
        published_at=published_at,
    )
