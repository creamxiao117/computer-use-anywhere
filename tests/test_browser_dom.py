from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from computer_use_anywhere.browser_dom import BrowserDomController


class BrowserDomTests(unittest.TestCase):
    def test_timeout_seconds_is_clamped(self) -> None:
        self.assertEqual(BrowserDomController._timeout_seconds({"timeout_seconds": 0}), 0.5)
        self.assertEqual(BrowserDomController._timeout_seconds({"timeout_seconds": 99}), 60.0)
        self.assertEqual(BrowserDomController._timeout_seconds({"timeout_seconds": 3}), 3.0)

    def test_format_json_result_parses_json_string(self) -> None:
        result = BrowserDomController._format_json_result("结果", "{\"ok\": true}")
        self.assertIn('"ok": true', result)


if __name__ == "__main__":
    unittest.main()
