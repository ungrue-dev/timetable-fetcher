from __future__ import annotations

import os
import unittest

from timetable_fetcher import fetch_html, parse_timetable


@unittest.skipUnless(
    os.environ.get("RUN_LIVE_TESTS") == "1",
    "set RUN_LIVE_TESTS=1 to query the BSU website",
)
class LiveSiteTests(unittest.TestCase):
    def test_live_timetable_is_parseable(self) -> None:
        payload = parse_timetable(fetch_html())
        self.assertEqual(payload["group"]["id"], "653")
        self.assertGreater(len(payload["weeks"]), 0)


if __name__ == "__main__":
    unittest.main()
