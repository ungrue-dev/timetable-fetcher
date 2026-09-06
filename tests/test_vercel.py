from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from http import HTTPStatus
from http.server import ThreadingHTTPServer
import json
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from api import refresh as api
from timetable_fetcher.core import FetchError, ParseError


class VercelTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = json.loads(api.SNAPSHOT_PATH.read_text())
        api._snapshot = None
        api._checked_at = 0
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), api.handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        api._snapshot = None
        api._checked_at = 0

    def request(self, method="POST", origin=None):
        request = Request(f"{self.origin}/api/refresh", method=method,
                          headers={"Origin": origin or self.origin})
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            return response.status, response.headers, json.load(response)

    def test_refresh_preserves_contract_without_writing_deployment(self):
        updated = deepcopy(self.snapshot)
        updated["source_updated_at"] = "2026-09-06T12:00:00+03:00"
        with patch.object(api, "fetch_html", return_value="html"), \
             patch.object(api, "parse_timetable", return_value=updated), \
             patch("pathlib.Path.write_text", side_effect=AssertionError("read-only")):
            status, headers, body = self.request()
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertTrue(body["changed"])
        self.assertEqual(body["timetable"], updated)

    def test_successful_concurrent_refreshes_share_one_fetch(self):
        with patch.object(api, "fetch_html", return_value="html") as fetch, \
             patch.object(api, "parse_timetable", return_value=self.snapshot):
            with ThreadPoolExecutor(max_workers=4) as executor:
                results = list(executor.map(lambda _: api.refresh(), range(4)))
        self.assertEqual(fetch.call_count, 1)
        self.assertTrue(all(result["timetable"] == self.snapshot for result in results))
        self.assertTrue(all(not result["changed"] for result in results))

    def test_failed_fetch_or_parse_keeps_last_success(self):
        for error, code in [(FetchError("offline"), "upstream_unavailable"),
                            (ParseError("invalid"), "invalid_upstream_response")]:
            api._snapshot = self.snapshot
            api._checked_at = 0
            with patch.object(api, "fetch_html", side_effect=error):
                status, _, body = self.request()
            self.assertEqual(status, HTTPStatus.BAD_GATEWAY)
            self.assertEqual(body["code"], code)
            self.assertEqual(api._snapshot, self.snapshot)
            self.assertEqual(api._checked_at, 0)

    def test_cross_origin_requests_do_not_fetch(self):
        with patch.object(api, "refresh") as refresh:
            status, _, _ = self.request(origin="https://unrelated.example")
        self.assertEqual(status, HTTPStatus.FORBIDDEN)
        refresh.assert_not_called()
        self.assertTrue(api.origin_is_allowed("https://timetable.vercel.app", "timetable.vercel.app"))
        self.assertFalse(api.origin_is_allowed("https://timetable.vercel.app.evil", "timetable.vercel.app"))
        self.assertFalse(api.origin_is_allowed(None, "timetable.vercel.app", "cross-site"))
        self.assertFalse(api.origin_is_allowed("null", "timetable.vercel.app"))

    def test_get_does_not_trigger_a_refresh(self):
        with patch.object(api, "refresh") as refresh:
            status, headers, _ = self.request(method="GET")
        self.assertEqual(status, HTTPStatus.METHOD_NOT_ALLOWED)
        self.assertEqual(headers["Allow"], "POST")
        refresh.assert_not_called()
