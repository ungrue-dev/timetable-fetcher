"""Vercel adapter: refresh public timetable data without writing deployment files."""

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
import json
import logging
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit

from timetable_fetcher.core import FetchError, ParseError, fetch_html, parse_timetable


SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "docs" / "timetable.json"
CACHE_SECONDS = 60
_lock = threading.Lock()
_snapshot = None
_checked_at = 0.0


def refresh():
    """Coalesce concurrent requests within an instance and cache successful reads."""
    global _snapshot, _checked_at
    with _lock:
        if _snapshot is not None and time.monotonic() - _checked_at < CACHE_SECONDS:
            return {"changed": False, "timetable": _snapshot}
        # Keep the existing snapshot until both fetching and parsing succeed.
        payload = parse_timetable(fetch_html(attempts=3, timeout=15))
        previous = _snapshot
        if previous is None:
            try:
                previous = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                previous = None
        semantic = lambda data: {k: v for k, v in data.items() if k != "published_at"}
        changed = previous is None or semantic(previous) != semantic(payload)
        _snapshot = payload if changed else previous
        _checked_at = time.monotonic()
        return {"changed": changed, "timetable": _snapshot}


def origin_is_allowed(origin, host, fetch_site=None):
    if fetch_site == "cross-site":
        return False
    if not origin:
        return True
    try:
        parsed = urlsplit(origin)
        return (
            parsed.scheme in {"http", "https"}
            and parsed.netloc == host
            and not parsed.path
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return False


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if not origin_is_allowed(
            self.headers.get("Origin"),
            self.headers.get("Host"),
            self.headers.get("Sec-Fetch-Site"),
        ):
            self._json(HTTPStatus.FORBIDDEN, {"error": "Запросы обновления с другого сайта запрещены."})
            return
        try:
            payload = refresh()
        except FetchError as error:
            logging.warning("BSU fetch failed: %s", error)
            self._json(HTTPStatus.BAD_GATEWAY, {
                "code": "upstream_unavailable",
                "error": "Сервер расписания БГУ сейчас недоступен. Попробуйте ещё раз немного позже.",
            })
        except ParseError as error:
            logging.warning("BSU parsing failed: %s", error)
            self._json(HTTPStatus.BAD_GATEWAY, {
                "code": "invalid_upstream_response",
                "error": "БГУ вернул расписание в неожиданном формате.",
            })
        except Exception:
            logging.exception("Timetable refresh failed")
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                "error": "При обновлении произошла непредвиденная ошибка.",
            })
        else:
            self._json(HTTPStatus.OK, payload)

    def do_GET(self):
        self._json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "Используйте POST для обновления."}, allow="POST")

    def _json(self, status, payload, *, allow=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if allow:
            self.send_header("Allow", allow)
        self.end_headers()
        self.wfile.write(body)
