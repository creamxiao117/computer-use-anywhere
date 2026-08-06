from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from computer_use_anywhere.models import SessionConfig
from computer_use_anywhere.runtime_diagnostics import (
    RuntimeDiagnostic,
    format_runtime_diagnostic,
    run_local_preflight,
)


class RuntimeDiagnosticsTests(unittest.TestCase):
    def test_preflight_runs_injected_probes(self) -> None:
        settings = SessionConfig(browser_dom_enabled=True)
        checks = run_local_preflight(
            settings,
            screenshot_probe=lambda _settings: RuntimeDiagnostic("截图", "ok", "ok"),
            desktop_probe=lambda _settings: RuntimeDiagnostic("桌面控制", "ok", "ok"),
            browser_dom_probe=lambda _settings: RuntimeDiagnostic(
                "浏览器 DOM", "warn", "not connected"
            ),
        )
        self.assertEqual([item.name for item in checks], ["截图", "桌面控制", "浏览器 DOM"])
        self.assertEqual(checks[-1].level, "warn")

    def test_preflight_skips_browser_dom_when_disabled(self) -> None:
        settings = SessionConfig(browser_dom_enabled=False)
        checks = run_local_preflight(
            settings,
            screenshot_probe=lambda _settings: RuntimeDiagnostic("截图", "ok", "ok"),
            desktop_probe=lambda _settings: RuntimeDiagnostic("桌面控制", "ok", "ok"),
            browser_dom_probe=lambda _settings: RuntimeDiagnostic(
                "浏览器 DOM", "error", "should not run"
            ),
        )
        self.assertEqual(checks[-1].name, "浏览器 DOM")
        self.assertEqual(checks[-1].level, "skip")
        self.assertIn("已关闭", checks[-1].message)

    def test_format_runtime_diagnostic_uses_chinese_status(self) -> None:
        text = format_runtime_diagnostic(RuntimeDiagnostic("截图", "ok", "截图可用"))
        self.assertEqual(text, "截图｜通过｜截图可用")


if __name__ == "__main__":
    unittest.main()
