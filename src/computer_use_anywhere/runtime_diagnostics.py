from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .models import SessionConfig


DiagnosticProbe = Callable[[SessionConfig], "RuntimeDiagnostic"]


@dataclass(slots=True)
class RuntimeDiagnostic:
    name: str
    level: str
    message: str

    @property
    def ok(self) -> bool:
        return self.level == "ok"


def run_local_preflight(
    settings: SessionConfig,
    *,
    screenshot_probe: DiagnosticProbe | None = None,
    desktop_probe: DiagnosticProbe | None = None,
    browser_dom_probe: DiagnosticProbe | None = None,
) -> list[RuntimeDiagnostic]:
    checks = [
        (screenshot_probe or check_screenshot_capture)(settings),
        (desktop_probe or check_desktop_api)(settings),
    ]
    if settings.browser_dom_enabled:
        checks.append((browser_dom_probe or check_browser_dom)(settings))
    else:
        checks.append(RuntimeDiagnostic("浏览器 DOM", "skip", "浏览器 DOM 工具已关闭，网页任务会只走截图操作。"))
    return checks


def check_screenshot_capture(settings: SessionConfig) -> RuntimeDiagnostic:
    try:
        from PIL import ImageGrab

        image = ImageGrab.grab()
        actual_width, actual_height = image.size
        if actual_width <= 0 or actual_height <= 0:
            raise RuntimeError(f"截图尺寸异常：{actual_width}x{actual_height}")
        capture_width = max(1, int(round(actual_width * settings.scale)))
        capture_height = max(1, int(round(actual_height * settings.scale)))
        return RuntimeDiagnostic(
            "截图",
            "ok",
            f"截图可用：真实屏幕 {actual_width}x{actual_height}，发送给模型约 {capture_width}x{capture_height}。",
        )
    except Exception as exc:
        return RuntimeDiagnostic("截图", "error", f"截图失败：{exc}")


def check_desktop_api(settings: SessionConfig) -> RuntimeDiagnostic:
    try:
        from .windows_control import WindowsDesktopController

        title = WindowsDesktopController.get_foreground_window_title() or "未知窗口"
        visible_count = len(WindowsDesktopController._visible_windows())
        return RuntimeDiagnostic(
            "桌面控制",
            "ok",
            f"Windows 桌面 API 可用：当前前台窗口“{title}”，可见窗口 {visible_count} 个。",
        )
    except Exception as exc:
        return RuntimeDiagnostic("桌面控制", "error", f"Windows 桌面 API 不可用：{exc}")


def check_browser_dom(settings: SessionConfig) -> RuntimeDiagnostic:
    try:
        from .browser_dom import BrowserDomController

        result = BrowserDomController(settings).execute({"action": "status"})
        first_line = result.message.splitlines()[0] if result.message else "已连接"
        return RuntimeDiagnostic("浏览器 DOM", "ok", first_line)
    except Exception as exc:
        return RuntimeDiagnostic(
            "浏览器 DOM",
            "warn",
            f"浏览器 DOM 当前不可用：{exc}。这不是致命错误，桌面截图模式仍可继续；网页任务建议先点“启动调试 Edge”。",
        )


def format_runtime_diagnostic(item: RuntimeDiagnostic) -> str:
    labels = {
        "ok": "通过",
        "warn": "提示",
        "error": "失败",
        "skip": "跳过",
    }
    label = labels.get(item.level, item.level or "未知")
    return f"{item.name}｜{label}｜{item.message}"
