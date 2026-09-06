from __future__ import annotations

from datetime import datetime
import io
import json
from pathlib import Path
import ssl
import tempfile
import unittest
from urllib.error import URLError
from zoneinfo import ZoneInfo

from timetable_fetcher import (
    FetchError,
    ParseError,
    fetch_html,
    parse_timetable,
    refresh_snapshot,
    update_snapshot,
)


FIXTURE = Path(__file__).parent / "fixtures" / "timetable.html"
PUBLISHED = datetime(2026, 9, 5, 12, 0, tzinfo=ZoneInfo("Europe/Minsk"))


class FakeHeaders:
    def get_content_charset(self) -> str:
        return "utf-8"


class FakeResponse(io.BytesIO):
    headers = FakeHeaders()

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class ParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = FIXTURE.read_text(encoding="utf-8")

    def test_parses_all_weeks_and_source_fields(self) -> None:
        payload = parse_timetable(self.html, published_at=PUBLISHED)

        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["group"]["id"], "653")
        self.assertIn("653 (Engl)", payload["group"]["name"])
        self.assertEqual(payload["timezone"], "Europe/Minsk")
        self.assertEqual(payload["class_duration_minutes"], 85)
        self.assertEqual(
            payload["source_updated_at"], "2026-09-04T15:51:00+03:00"
        )
        self.assertEqual(len(payload["weeks"]), 2)
        self.assertEqual(payload["weeks"][0]["starts_on"], "2026-08-31")
        self.assertEqual(payload["weeks"][1]["starts_on"], "2026-09-07")

        classes = payload["weeks"][0]["days"][0]["classes"]
        self.assertEqual([item["subgroup"] for item in classes], ["1 подгр", "2 подгр"])
        self.assertEqual(classes[0]["start_time"], "14:10")
        self.assertEqual(classes[0]["end_time"], "15:35")
        self.assertEqual(classes[0]["teacher"], "Ваценко Екатерина Викторовна")
        self.assertEqual(classes[0]["room"], "ул. Обойная 7, 301")

    def test_end_time_can_cross_midnight(self) -> None:
        payload = parse_timetable(self.html, published_at=PUBLISHED)
        event = payload["weeks"][1]["days"][0]["classes"][0]
        self.assertEqual(event["end_time"], "00:55")
        self.assertEqual(event["ends_at"], "2026-09-08T00:55:00+03:00")

    def test_newly_published_week_is_discovered_automatically(self) -> None:
        extra_rows = """
          <tr vl="14.09.2026"><td class="head-date" colspan="6">14.09.2026 понедельник</td></tr>
          <tr vl="14.09.2026">
            <td>09:00<br></td><td>Higher Mathematics (Lecture)</td>
            <td>Новый преподаватель</td><td>Новая аудитория</td>
          </tr>
        """
        expanded = self.html.replace("</tbody>", f"{extra_rows}</tbody>")
        expanded = expanded.replace(
            "</div>\n      <div>Дата обновления",
            '<span vl="14.09.2026">14.09.2026</span>\n      </div>\n'
            "      <div>Дата обновления",
        )

        payload = parse_timetable(expanded, published_at=PUBLISHED)

        self.assertEqual(len(payload["weeks"]), 3)
        self.assertEqual(payload["weeks"][2]["starts_on"], "2026-09-14")
        self.assertEqual(
            payload["weeks"][2]["days"][0]["classes"][0]["subject"],
            "Higher Mathematics (Lecture)",
        )

    def test_missing_schedule_is_rejected(self) -> None:
        with self.assertRaises(ParseError):
            parse_timetable("<html><h1>653</h1></html>")

    def test_malformed_class_row_is_rejected(self) -> None:
        malformed = self.html.replace(
            "<td>ул. Обойная 7, 301А</td>", "", 1
        )
        with self.assertRaises(ParseError):
            parse_timetable(malformed)


class SnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = FIXTURE.read_text(encoding="utf-8")

    def test_unchanged_data_does_not_rewrite_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "timetable.json"
            self.assertTrue(update_snapshot(output, self.html, published_at=PUBLISHED))
            original_bytes = output.read_bytes()
            original_mtime = output.stat().st_mtime_ns

            later = datetime(2026, 9, 5, 13, 0, tzinfo=PUBLISHED.tzinfo)
            self.assertFalse(update_snapshot(output, self.html, published_at=later))
            self.assertEqual(output.read_bytes(), original_bytes)
            self.assertEqual(output.stat().st_mtime_ns, original_mtime)

    def test_changed_source_timestamp_rewrites_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "timetable.json"
            update_snapshot(output, self.html, published_at=PUBLISHED)
            changed = self.html.replace(
                "Дата обновления 04.09.2026 15:51",
                "Дата обновления 04.09.2026 16:52",
            )

            self.assertTrue(update_snapshot(output, changed, published_at=PUBLISHED))
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(
                payload["source_updated_at"], "2026-09-04T16:52:00+03:00"
            )

    def test_parse_failure_preserves_existing_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "timetable.json"
            output.write_text('{"valid": true}\n', encoding="utf-8")
            before = output.read_bytes()

            with self.assertRaises(ParseError):
                update_snapshot(output, "<html>broken</html>")
            self.assertEqual(output.read_bytes(), before)

    def test_fetch_failure_preserves_existing_snapshot(self) -> None:
        calls = 0

        def failing_opener(*args: object, **kwargs: object) -> object:
            nonlocal calls
            calls += 1
            raise URLError("offline")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "timetable.json"
            output.write_text('{"valid": true}\n', encoding="utf-8")
            before = output.read_bytes()

            with self.assertRaises(FetchError):
                refresh_snapshot(
                    output,
                    opener=failing_opener,
                    attempts=3,
                    sleep=lambda _: None,
                )
            self.assertEqual(calls, 3)
            self.assertEqual(output.read_bytes(), before)


class FetchTests(unittest.TestCase):
    def test_fetch_retries_then_succeeds(self) -> None:
        attempts = 0
        delays: list[float] = []

        def opener(*args: object, **kwargs: object) -> FakeResponse:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise URLError("temporary failure")
            return FakeResponse("привет".encode())

        result = fetch_html(opener=opener, sleep=delays.append)
        self.assertEqual(result, "привет")
        self.assertEqual(attempts, 3)
        self.assertEqual(delays, [1, 2])

    def test_exhausted_fetch_includes_underlying_error(self) -> None:
        def opener(*args: object, **kwargs: object) -> object:
            raise URLError("certificate problem")

        with self.assertRaisesRegex(FetchError, "certificate problem"):
            fetch_html(opener=opener, attempts=1)

    def test_tls_protocol_failure_uses_secure_html_relay(self) -> None:
        urls: list[str] = []
        delays: list[float] = []

        def opener(request: object, **kwargs: object) -> FakeResponse:
            url = request.full_url  # type: ignore[attr-defined]
            urls.append(url)
            if url.startswith("https://sb.bsu.by/"):
                tls_error = ssl.SSLError(
                    ssl.SSL_ERROR_SSL,
                    "[SSL: WRONG_VERSION_NUMBER] wrong version number",
                )
                raise URLError(tls_error)
            self.assertEqual(
                request.get_header("X-return-format"),  # type: ignore[attr-defined]
                "html",
            )
            return FakeResponse("расписание".encode())

        result = fetch_html(opener=opener, attempts=1, sleep=delays.append)

        self.assertEqual(result, "расписание")
        self.assertEqual(
            urls,
            [
                "https://sb.bsu.by/raspisanie/map-653__engl_.xml",
                "https://r.jina.ai/https://sb.bsu.by/raspisanie/map-653__engl_.xml",
            ],
        )
        self.assertEqual(delays, [])

    def test_regular_network_failures_do_not_use_relay(self) -> None:
        urls: list[str] = []

        def opener(request: object, **kwargs: object) -> object:
            urls.append(request.full_url)  # type: ignore[attr-defined]
            raise URLError("offline")

        with self.assertRaises(FetchError):
            fetch_html(opener=opener, attempts=2, sleep=lambda _: None)

        self.assertEqual(
            urls,
            ["https://sb.bsu.by/raspisanie/map-653__engl_.xml"] * 2,
        )


if __name__ == "__main__":
    unittest.main()
