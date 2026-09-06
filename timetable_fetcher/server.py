"""Local same-origin HTTP server for the timetable interface."""

from __future__ import annotations

from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from typing import Callable
from urllib.parse import urlsplit
import webbrowser

from .core import FetchError, ParseError, TimetableError, refresh_snapshot


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WEB_ROOT = PROJECT_ROOT / "docs"
RefreshCallable = Callable[[Path], bool]


def _load_snapshot(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise TimetableError("Сохранённое расписание отсутствует") from error
    except (json.JSONDecodeError, OSError, UnicodeError) as error:
        raise TimetableError("Сохранённое расписание повреждено") from error
    if not isinstance(payload, dict):
        raise TimetableError("Сохранённое расписание повреждено")
    return payload


class TimetableHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        web_root: Path,
        refresher: RefreshCallable,
    ) -> None:
        self.web_root = web_root.resolve()
        self.snapshot_path = self.web_root / "timetable.json"
        self.refresher = refresher
        self.refresh_lock = threading.Lock()
        handler = partial(TimetableRequestHandler, directory=str(self.web_root))
        super().__init__(server_address, handler)


class TimetableRequestHandler(SimpleHTTPRequestHandler):
    server: TimetableHTTPServer

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        super().end_headers()

    def do_GET(self) -> None:
        if urlsplit(self.path).path.startswith("/api/"):
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"error": "Неизвестный адрес API"},
            )
            return
        super().do_GET()

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/api/refresh":
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"error": "Неизвестный адрес API"},
            )
            return
        if not self._origin_is_allowed():
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {"error": "Запросы обновления с другого сайта запрещены"},
            )
            return
        if not self.server.refresh_lock.acquire(blocking=False):
            self._send_json(
                HTTPStatus.CONFLICT,
                {"error": "Обновление расписания уже выполняется"},
            )
            return

        try:
            changed = self.server.refresher(self.server.snapshot_path)
            timetable = _load_snapshot(self.server.snapshot_path)
        except FetchError as error:
            self.log_error("BSU fetch failed: %s", error)
            self._send_json(
                HTTPStatus.BAD_GATEWAY,
                {
                    "code": "upstream_unavailable",
                    "error": "Сервер расписания БГУ сейчас недоступен. Попробуйте ещё раз немного позже.",
                },
            )
        except ParseError as error:
            self.log_error("BSU timetable parse failed: %s", error)
            self._send_json(
                HTTPStatus.BAD_GATEWAY,
                {
                    "code": "invalid_upstream_response",
                    "error": "БГУ вернул расписание в неожиданном формате. Сохранённая копия по-прежнему доступна.",
                },
            )
        except (TimetableError, OSError, ValueError) as error:
            self._send_json(
                HTTPStatus.BAD_GATEWAY,
                {"code": "refresh_failed", "error": str(error)},
            )
        except Exception as error:
            self.log_error("Unexpected refresh failure: %s", error)
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "При обновлении произошла непредвиденная ошибка"},
            )
        else:
            self._send_json(
                HTTPStatus.OK,
                {"changed": changed, "timetable": timetable},
            )
        finally:
            self.server.refresh_lock.release()

    def _origin_is_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parsed = urlsplit(origin)
        request_host = self.headers.get("Host", "")
        return (
            parsed.scheme == "http"
            and parsed.netloc == request_host
            and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        )

    def _send_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def create_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    web_root: Path = DEFAULT_WEB_ROOT,
    refresher: RefreshCallable = refresh_snapshot,
) -> TimetableHTTPServer:
    """Create the local server; separated from ``serve`` for tests."""

    if not web_root.is_dir():
        raise FileNotFoundError(f"Каталог интерфейса не найден: {web_root}")
    return TimetableHTTPServer((host, port), web_root, refresher)


def serve(*, port: int = 8000, open_browser: bool = True) -> None:
    """Run the local app until interrupted."""

    server = create_server(port=port)
    actual_port = server.server_address[1]
    url = f"http://127.0.0.1:{actual_port}/"
    print(f"Приложение расписания запущено: {url}")
    print("Нажмите Ctrl+C для остановки.")
    if open_browser:
        threading.Timer(0.25, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстанавливаем приложение расписания.")
    finally:
        server.server_close()
