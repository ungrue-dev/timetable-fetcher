from __future__ import annotations

from http import HTTPStatus
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from timetable_fetcher import FetchError
from timetable_fetcher.server import create_server


SNAPSHOT = {
    "schema_version": 1,
    "group": {"id": "653", "name": "Group 653"},
    "source_url": "https://example.test/timetable",
    "timezone": "Europe/Minsk",
    "class_duration_minutes": 85,
    "source_updated_at": "2026-09-05T10:00:00+03:00",
    "published_at": "2026-09-05T10:01:00+03:00",
    "weeks": [],
}


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.web_root = Path(self.temporary.name)
        (self.web_root / "index.html").write_text("app", encoding="utf-8")
        (self.web_root / "timetable.json").write_text(
            json.dumps(SNAPSHOT), encoding="utf-8"
        )
        self.refresh_result = True
        self.refresh_error: Exception | None = None
        self.refresh_calls = 0

        def refresher(path: Path) -> bool:
            self.refresh_calls += 1
            if self.refresh_error is not None:
                raise self.refresh_error
            return self.refresh_result

        self.server = create_server(
            port=0,
            web_root=self.web_root,
            refresher=refresher,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def post_refresh(self, *, origin: str | None = None) -> tuple[int, dict]:
        headers = {"Accept": "application/json"}
        if origin is not None:
            headers["Origin"] = origin
        request = Request(
            f"{self.origin}/api/refresh",
            method="POST",
            headers=headers,
        )
        try:
            response = urlopen(request, timeout=2)
        except HTTPError as error:
            try:
                return error.code, json.loads(error.read().decode())
            finally:
                error.close()
        with response:
            return response.status, json.loads(response.read().decode())

    def test_refresh_returns_changed_snapshot(self) -> None:
        status, payload = self.post_refresh(origin=self.origin)

        self.assertEqual(status, HTTPStatus.OK)
        self.assertTrue(payload["changed"])
        self.assertEqual(payload["timetable"]["group"]["id"], "653")
        self.assertEqual(self.refresh_calls, 1)

    def test_refresh_returns_unchanged_snapshot(self) -> None:
        self.refresh_result = False

        status, payload = self.post_refresh(origin=self.origin)

        self.assertEqual(status, HTTPStatus.OK)
        self.assertFalse(payload["changed"])

    def test_refresh_failure_is_structured_and_preserves_snapshot(self) -> None:
        before = (self.web_root / "timetable.json").read_bytes()
        self.refresh_error = FetchError("BSU is offline")

        status, payload = self.post_refresh(origin=self.origin)

        self.assertEqual(status, HTTPStatus.BAD_GATEWAY)
        self.assertEqual(payload["code"], "upstream_unavailable")
        self.assertEqual(
            payload["error"],
            "Сервер расписания БГУ сейчас недоступен. Попробуйте ещё раз немного позже.",
        )
        self.assertEqual((self.web_root / "timetable.json").read_bytes(), before)

    def test_overlapping_refresh_is_rejected(self) -> None:
        self.server.refresh_lock.acquire()
        try:
            status, payload = self.post_refresh(origin=self.origin)
        finally:
            self.server.refresh_lock.release()

        self.assertEqual(status, HTTPStatus.CONFLICT)
        self.assertIn("уже выполняется", payload["error"])
        self.assertEqual(self.refresh_calls, 0)

    def test_cross_origin_refresh_is_rejected(self) -> None:
        status, payload = self.post_refresh(origin="https://evil.example")

        self.assertEqual(status, HTTPStatus.FORBIDDEN)
        self.assertIn("другого сайта", payload["error"])
        self.assertEqual(self.refresh_calls, 0)

    def test_static_interface_is_served(self) -> None:
        with urlopen(f"{self.origin}/", timeout=2) as response:
            self.assertEqual(response.status, HTTPStatus.OK)
            self.assertEqual(response.read(), b"app")


if __name__ == "__main__":
    unittest.main()
