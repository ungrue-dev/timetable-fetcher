"""Fetcher and parser for the BSU group 653 timetable."""

from .core import (
    CLASS_DURATION_MINUTES,
    GROUP_ID,
    SOURCE_URL,
    TIMEZONE_NAME,
    FetchError,
    ParseError,
    fetch_html,
    parse_timetable,
    refresh_snapshot,
    update_snapshot,
)

__all__ = [
    "CLASS_DURATION_MINUTES",
    "GROUP_ID",
    "SOURCE_URL",
    "TIMEZONE_NAME",
    "FetchError",
    "ParseError",
    "fetch_html",
    "parse_timetable",
    "refresh_snapshot",
    "update_snapshot",
]
