from __future__ import annotations

import base64
import ctypes
import io
import math
import time
from pathlib import Path
from typing import Any, Iterable

from ctypes import wintypes

from PIL import Image, ImageChops, ImageDraw, ImageGrab, ImageStat

from .models import (
    ACTION_DOUBLE_CLICK,
    ACTION_DRAG_END,
    ACTION_KEY_PRESS,
    ACTION_LEFT_CLICK,
    ACTION_MIDDLE_CLICK,
    ACTION_MOVE_ONLY,
    ACTION_RIGHT_CLICK,
    ACTION_SCROLL_DOWN,
    ACTION_SCROLL_UP,
    ACTION_TYPE_TEXT,
    ActionResult,
    SessionConfig,
    Snapshot,
)


# Anthropic API limits per model family (May 2026 best practices)
CLAUDE_4_6_MAX_LONG_EDGE = 1568
CLAUDE_4_6_MAX_PIXELS = 1_150_000
OPUS_4_7_MAX_LONG_EDGE = 2576
OPUS_4_7_MAX_PIXELS = 3_750_000


def compute_max_api_fit(native_w: int, native_h: int, max_long_edge: int, max_pixels: int) -> tuple[int, int]:
    """Calculate the optimal downscaled resolution that uses full API pixel budget without distortion."""
    aspect = native_w / native_h
    h_from_pixels = math.sqrt(max_pixels / aspect)
    w_from_pixels = h_from_pixels * aspect
    if native_w >= native_h:
        w = min(w_from_pixels, max_long_edge)
        h = w / aspect
    else:
        h = min(h_from_pixels, max_long_edge)
        w = h * aspect
    w = min(w, native_w)
    h = min(h, native_h)
    return max(1, int(w)), max(1, int(h))


def resolve_capture_resolution(actual_w: int, actual_h: int, target_resolution: str, model_family: str = "auto") -> tuple[int, int]:
    """Resolve the final screenshot dimensions based on target resolution strategy."""
    if target_resolution == "1280x720":
        return 1280, 720
    if target_resolution == "1920x1080":
        return 1920, 1080
    if target_resolution == "max_api_fit":
        family = model_family.lower()
        if "opus_4_7" in family or "opus-4.7" in family or "opus 4.7" in family:
            return compute_max_api_fit(actual_w, actual_h, OPUS_4_7_MAX_LONG_EDGE, OPUS_4_7_MAX_PIXELS)
        # Default to Claude 4.6 family limits for safety
        return compute_max_api_fit(actual_w, actual_h, CLAUDE_4_6_MAX_LONG_EDGE, CLAUDE_4_6_MAX_PIXELS)
    # Legacy scale mode: keep proportional scaling
    scale = float(target_resolution) if target_resolution.replace(".", "").isdigit() else 0.8
    return max(1, int(round(actual_w * scale))), max(1, int(round(actual_h * scale)))


user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
kernel32.GlobalUnlock.restype = ctypes.c_int
kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
kernel32.GlobalFree.restype = ctypes.c_void_p
user32.OpenClipboard.argtypes = [ctypes.c_void_p]
user32.OpenClipboard.restype = ctypes.c_int
user32.EmptyClipboard.argtypes = []
user32.EmptyClipboard.restype = ctypes.c_int
user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
user32.SetClipboardData.restype = ctypes.c_void_p
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = ctypes.c_int
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.FindWindowW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
user32.FindWindowW.restype = wintypes.HWND
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.BringWindowToTop.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL

KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
SW_RESTORE = 9
SW_SHOW = 5

SPECIAL_KEYS = {
    "alt": 0x12,
    "apps": 0x5D,
    "backspace": 0x08,
    "capslock": 0x14,
    "ctrl": 0x11,
    "control": 0x11,
    "delete": 0x2E,
    "del": 0x2E,
    "down": 0x28,
    "end": 0x23,
    "enter": 0x0D,
    "return": 0x0D,
    "esc": 0x1B,
    "escape": 0x1B,
    "home": 0x24,
    "insert": 0x2D,
    "left": 0x25,
    "menu": 0x12,
    "pagedown": 0x22,
    "pageup": 0x21,
    "pgdn": 0x22,
    "pgup": 0x21,
    "right": 0x27,
    "shift": 0x10,
    "space": 0x20,
    "tab": 0x09,
    "up": 0x26,
    "win": 0x5B,
    "windows": 0x5B,
}
for index in range(1, 13):
    SPECIAL_KEYS[f"f{index}"] = 0x6F + index

SUPPORTED_COMPUTER_ACTIONS = {
    "screenshot",
    "mouse_move",
    "left_click",
    "double_click",
    "right_click",
    "middle_click",
    "left_click_drag",
    "type",
    "key",
    "scroll",
    "wait",
    "activate_window",
}


def _set_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


class WindowsDesktopController:
    def __init__(self, settings: SessionConfig) -> None:
        _set_dpi_awareness()
        self.settings = settings
        self.session_root = settings.session_root or Path.cwd() / "sessions" / time.strftime("%Y%m%d-%H%M%S")
        self.session_root.mkdir(parents=True, exist_ok=True)
        self._snapshot_index = 0
        self._last_masked_regions_actual: list[tuple[int, int, int, int]] = []
        self._last_masked_regions_capture: list[tuple[int, int, int, int]] = []
        self.actual_width = int(user32.GetSystemMetrics(0))
        self.actual_height = int(user32.GetSystemMetrics(1))
        self.capture_width, self.capture_height = resolve_capture_resolution(
            self.actual_width, self.actual_height, self.settings.target_resolution, self.settings.model_family_hint
        )
        # 可视化反馈层用：保存上一次 _perform() 期间记录的动作细节，
        # 供 execute() 组装 ActionResult 时取用。每次 execute() 开始前会清空。
        self._last_execution_details: dict[str, Any] = {}

    def capture_snapshot(self, label: str = "snapshot") -> Snapshot:
        image = ImageGrab.grab()
        actual_width, actual_height = image.size
        self.actual_width = actual_width
        self.actual_height = actual_height
        foreground_window_title = self.get_foreground_window_title()
        visible_window_titles = self.list_visible_window_titles()
        self._last_masked_regions_actual = []
        if self.settings.mask_own_window:
            masked_rect = self._mask_own_window(image)
            if masked_rect is not None:
                self._last_masked_regions_actual = [masked_rect]
        self.capture_width, self.capture_height = resolve_capture_resolution(
            actual_width, actual_height, self.settings.target_resolution, self.settings.model_family_hint
        )
        self._last_masked_regions_capture = []
        for rect in self._last_masked_regions_actual:
            capture_rect = self._capture_rect_from_actual(rect)
            if capture_rect is not None:
                self._last_masked_regions_capture.append(capture_rect)
        target_w, target_h = self.capture_width, self.capture_height
        if (actual_width, actual_height) != (target_w, target_h):
            image = image.resize((target_w, target_h), Image.Resampling.LANCZOS)

        self._snapshot_index += 1
        path = self.session_root / f"{self._snapshot_index:03d}_{label}.jpg"
        buffer = io.BytesIO()
        image.convert("RGB").save(path, format="JPEG", quality=self.settings.jpeg_quality, optimize=True)
        image.convert("RGB").save(buffer, format="JPEG", quality=self.settings.jpeg_quality, optimize=True)
        return Snapshot(
            path=path,
            data_url=f"data:image/jpeg;base64,{base64.b64encode(buffer.getvalue()).decode('ascii')}",
            width=image.size[0],
            height=image.size[1],
            actual_width=actual_width,
            actual_height=actual_height,
            foreground_window_title=foreground_window_title,
            visible_window_titles=visible_window_titles,
        )

    def execute(self, arguments: dict[str, Any]) -> ActionResult:
        action = str(arguments.get("action") or "").strip().lower()
        modifiers = self._normalize_keys(arguments.get("modifiers"))
        # 每次执行前清空上一次的可视化反馈细节，避免串值。
        self._last_execution_details = {}
        self._press_down(modifiers)
        try:
            message = self._perform(action, arguments)
        finally:
            self._release(modifiers)
        time.sleep(self._post_action_delay(action, arguments))
        snapshot = self.capture_snapshot(action or "action")
        return ActionResult(
            message=message,
            snapshot=snapshot,
            action_kind=self._last_execution_details.get("action_kind", ""),
            physical_coord=self._last_execution_details.get("physical_coord"),
            action_extra=self._last_execution_details.get("action_extra", {}),
        )

    def is_action_targeting_masked_region(self, arguments: dict[str, Any]) -> bool:
        action = str(arguments.get("action") or "").strip().lower()
        if action in {"left_click", "double_click", "right_click", "middle_click", "mouse_move", "scroll"}:
            coordinate = self._optional_coordinate(arguments.get("coordinate"))
            if coordinate != (None, None):
                return self._coordinate_in_masked_region(*coordinate)
        if action in {"left_click_drag", "drag"}:
            start = self._optional_coordinate(arguments.get("start_coordinate"))
            end = self._optional_coordinate(arguments.get("end_coordinate"))
            start_blocked = start != (None, None) and self._coordinate_in_masked_region(*start)
            end_blocked = end != (None, None) and self._coordinate_in_masked_region(*end)
            return start_blocked or end_blocked
        return False

    def is_supported_action(self, arguments: dict[str, Any]) -> bool:
        action = str(arguments.get("action") or "").strip().lower()
        return action in SUPPORTED_COMPUTER_ACTIONS

    def has_out_of_bounds_coordinate(self, arguments: dict[str, Any]) -> bool:
        return bool(self._out_of_bounds_points(arguments))

    def coordinate_bounds_message(self, arguments: dict[str, Any]) -> str:
        points = self._out_of_bounds_points(arguments)
        if not points:
            return ""
        point_text = "；".join(f"{name}={coordinate}" for name, coordinate in points)
        return (
            f"模型给出的坐标超出当前截图范围 {self.capture_width}x{self.capture_height}：{point_text}。"
            "本工具不会把越界坐标静默夹到屏幕边缘，以免误点。"
            "请只使用最新截图内的坐标重新选择动作。"
        )

    def unsupported_action_message(self, arguments: dict[str, Any]) -> str:
        action = str(arguments.get("action") or "").strip()
        supported = "、".join(sorted(SUPPORTED_COMPUTER_ACTIONS))
        if action:
            return f"当前不支持的 computer 操作是“{action}”。请改用以下基础动作之一：{supported}。"
        return f"你返回的 computer 动作缺少 action 字段。请改用以下基础动作之一：{supported}。"

    def masked_region_message(self, arguments: dict[str, Any]) -> str:
        return (
            f"你选择的操作目标位于“{self.settings.own_window_title}”窗口的遮挡区域内。"
            "该区域当前不可见，不能假设后方有什么页面或控件。"
            "请基于最新截图重新选择一个在可见区域内的动作。"
        )

    def requires_foreground_app(self, arguments: dict[str, Any]) -> bool:
        action = str(arguments.get("action") or "").strip().lower()
        if action == "type":
            return True
        if action == "key":
            keys = self._normalize_keys(arguments.get("keys"))
            if not keys:
                return False
            blocked = {"enter", "tab", "space", "backspace", "delete"}
            return any(token in blocked for token in keys)
        return False

    def is_own_window_foreground(self) -> bool:
        return self._titles_match(self.get_foreground_window_title(), self.settings.own_window_title)

    def is_foreground_safe_for_action(self, arguments: dict[str, Any]) -> bool:
        if not self.requires_foreground_app(arguments):
            return True
        current_title = self.get_foreground_window_title()
        if self._titles_match(current_title, self.settings.own_window_title):
            return False
        expected_title = self._expected_window_title(arguments)
        if expected_title and not self._title_contains(current_title, expected_title):
            return False
        return True

    def ensure_expected_window_foreground(self, arguments: dict[str, Any]) -> str:
        if not self.requires_foreground_app(arguments):
            return ""
        expected_title = self._expected_window_title(arguments)
        if not expected_title:
            return ""
        current_title = self.get_foreground_window_title()
        if self._title_contains(current_title, expected_title) and not self._titles_match(current_title, self.settings.own_window_title):
            return ""
        return self.activate_window_by_title(expected_title)

    def foreground_guard_message(self, arguments: dict[str, Any]) -> str:
        current_title = self.get_foreground_window_title() or "未知窗口"
        action_label = self.describe(arguments)
        expected_title = self._expected_window_title(arguments)
        if self._titles_match(current_title, self.settings.own_window_title):
            return (
                f"当前前台窗口仍然是“{current_title}”，这是代理工具自己的窗口。"
                f"因此不能继续执行“{action_label}”这种输入类动作。"
                "请先用 activate_window 激活真正的目标窗口，或基于最新截图选择可见区域内的动作。"
            )
        if expected_title:
            return (
                f"模型声明该动作应在“{expected_title}”里执行，但当前前台窗口是“{current_title}”。"
                f"为避免把“{action_label}”输入到错误窗口，本次操作已被拦截。"
                "请先用 activate_window 激活目标窗口，再根据最新截图重新决策。"
            )
        return (
            f"当前前台窗口是“{current_title}”，目标应用还没有被可靠确认。"
            f"因此不能继续执行“{action_label}”这种输入类动作。"
            "请先把真正的目标窗口切到前台，再根据最新截图重新决策。"
        )

    def describe(self, arguments: dict[str, Any]) -> str:
        action = str(arguments.get("action") or "").strip().lower()
        coordinate = arguments.get("coordinate")
        start_coordinate = arguments.get("start_coordinate")
        end_coordinate = arguments.get("end_coordinate")
        if action in {"left_click", "double_click", "right_click", "middle_click", "mouse_move"} and coordinate:
            action_name = {
                "left_click": "左键单击",
                "double_click": "左键双击",
                "right_click": "右键单击",
                "middle_click": "中键单击",
                "mouse_move": "移动鼠标",
            }.get(action, action)
            return f"{action_name} @ {coordinate}"
        if action == "left_click_drag" and start_coordinate and end_coordinate:
            return f"左键拖拽 {start_coordinate} -> {end_coordinate}"
        if action == "type":
            text = str(arguments.get("text") or "")
            suffix = "..." if len(text) > 80 else ""
            return f"输入文本 \"{text[:80]}{suffix}\""
        if action == "key":
            return f"按键 {arguments.get('keys')}"
        if action == "scroll":
            return f"滚轮 amount={arguments.get('scroll_amount')} @ {coordinate}"
        if action == "wait":
            delay = arguments.get("seconds") if arguments.get("seconds") is not None else arguments.get("duration_ms")
            unit = "秒" if arguments.get("seconds") is not None else "毫秒"
            return f"等待 {delay}{unit}"
        if action == "activate_window":
            return f"激活窗口 “{arguments.get('window_title') or arguments.get('title') or ''}”"
        return action or "未知操作"

    def _perform(self, action: str, arguments: dict[str, Any]) -> str:
        if action == "screenshot":
            # 不画波纹/不附带可视化反馈。
            self._last_execution_details = {}
            return "已捕获最新截图。"
        if action == "mouse_move":
            x, y = self._require_coordinate(arguments.get("coordinate"))
            self._move(x, y)
            self._last_execution_details = {
                "action_kind": ACTION_MOVE_ONLY,
                "physical_coord": self.to_real_coordinate(x, y),
                "action_extra": {},
            }
            return f"已将鼠标移动到截图坐标 ({x}, {y})。"
        if action == "left_click":
            x, y = self._require_coordinate(arguments.get("coordinate"))
            self._click(x, y, times=1, button="left")
            self._last_execution_details = {
                "action_kind": ACTION_LEFT_CLICK,
                "physical_coord": self.to_real_coordinate(x, y),
                "action_extra": {},
            }
            return f"已在 ({x}, {y}) 左键单击。"
        if action == "double_click":
            x, y = self._require_coordinate(arguments.get("coordinate"))
            self._click(x, y, times=2, button="left")
            self._last_execution_details = {
                "action_kind": ACTION_DOUBLE_CLICK,
                "physical_coord": self.to_real_coordinate(x, y),
                "action_extra": {},
            }
            return f"已在 ({x}, {y}) 左键双击。"
        if action == "right_click":
            x, y = self._require_coordinate(arguments.get("coordinate"))
            self._click(x, y, times=1, button="right")
            self._last_execution_details = {
                "action_kind": ACTION_RIGHT_CLICK,
                "physical_coord": self.to_real_coordinate(x, y),
                "action_extra": {},
            }
            return f"已在 ({x}, {y}) 右键单击。"
        if action == "middle_click":
            x, y = self._require_coordinate(arguments.get("coordinate"))
            self._click(x, y, times=1, button="middle")
            self._last_execution_details = {
                "action_kind": ACTION_MIDDLE_CLICK,
                "physical_coord": self.to_real_coordinate(x, y),
                "action_extra": {},
            }
            return f"已在 ({x}, {y}) 中键单击。"
        if action in {"left_click_drag", "drag"}:
            start_x, start_y = self._require_coordinate(arguments.get("start_coordinate"))
            end_x, end_y = self._require_coordinate(arguments.get("end_coordinate"))
            self._drag(start_x, start_y, end_x, end_y)
            physical_start = self.to_real_coordinate(start_x, start_y)
            physical_end = self.to_real_coordinate(end_x, end_y)
            self._last_execution_details = {
                "action_kind": ACTION_DRAG_END,
                "physical_coord": physical_end,
                "action_extra": {"start": physical_start, "end": physical_end},
            }
            return f"已从 ({start_x}, {start_y}) 拖拽到 ({end_x}, {end_y})。"
        if action == "type":
            text = str(arguments.get("text") or "")
            if not text:
                raise ValueError("type 操作需要提供 text。")
            self._paste_text(text)
            self._last_execution_details = {
                "action_kind": ACTION_TYPE_TEXT,
                "physical_coord": None,
                "action_extra": {"text": text},
            }
            return f"已输入 {len(text)} 个字符。"
        if action == "key":
            keys = self._normalize_keys(arguments.get("keys"))
            if not keys:
                raise ValueError("key 操作需要提供 keys。")
            self._hotkey(keys)
            self._last_execution_details = {
                "action_kind": ACTION_KEY_PRESS,
                "physical_coord": None,
                "action_extra": {"keys": list(keys)},
            }
            return f"已按下按键：{keys}。"
        if action == "scroll":
            amount = int(arguments.get("scroll_amount") or 0)
            x, y = self._optional_coordinate(arguments.get("coordinate"))
            self._scroll(amount, x, y)
            physical_coord: tuple[int, int] | None = None
            if x is not None and y is not None:
                physical_coord = self.to_real_coordinate(int(x), int(y))
            # amount > 0 视为向上滚动，amount < 0 视为向下滚动；amount == 0 默认归为向下。
            scroll_kind = ACTION_SCROLL_UP if amount > 0 else ACTION_SCROLL_DOWN
            self._last_execution_details = {
                "action_kind": scroll_kind,
                "physical_coord": physical_coord,
                "action_extra": {"amount": amount},
            }
            return f"已滚动 {amount} 格。"
        if action == "wait":
            seconds = self._seconds(arguments)
            time.sleep(seconds)
            # wait 不画波纹。
            self._last_execution_details = {}
            return f"已等待 {seconds:.2f} 秒。"
        if action == "activate_window":
            window_title = str(arguments.get("window_title") or arguments.get("title") or "").strip()
            if not window_title:
                raise ValueError("activate_window 操作需要提供 window_title。")
            # activate_window 不画波纹。
            self._last_execution_details = {}
            return self.activate_window_by_title(window_title)
        raise ValueError(f"不支持的 computer 操作：{action}")

    def activate_window_by_title(self, window_title: str) -> str:
        query = window_title.strip()
        if not query:
            raise ValueError("窗口标题不能为空。")
        match = self._find_window_by_title(query)
        if match is None:
            current_title = self.get_foreground_window_title() or "未知窗口"
            return f"没有找到标题包含“{query}”的可见窗口。当前前台窗口：{current_title}。"

        hwnd, matched_title = match
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        else:
            user32.ShowWindow(hwnd, SW_SHOW)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.25)
        current_title = self.get_foreground_window_title() or "未知窗口"
        if self._title_contains(current_title, query) or self._titles_match(current_title, matched_title):
            return f"已激活窗口“{current_title}”。"
        return f"已尝试激活窗口“{matched_title}”，但当前前台窗口是“{current_title}”。请根据最新截图确认。"

    def list_visible_window_titles(self, *, limit: int = 12) -> list[str]:
        titles: list[str] = []
        seen: set[str] = set()
        for _hwnd, title in self._visible_windows():
            if self._titles_match(title, self.settings.own_window_title):
                continue
            normalized = title.casefold()
            if normalized in seen:
                continue
            seen.add(normalized)
            titles.append(title)
            if len(titles) >= limit:
                break
        return titles

    @staticmethod
    def describe_snapshot_change(before: Snapshot, after: Snapshot) -> str:
        score = WindowsDesktopController._snapshot_difference_score(before.path, after.path)
        if score is None:
            return "截图变化：无法比较。"
        if score < 1.0:
            return (
                "⚠️ 截图变化：几乎没有变化。"
                "上一动作很可能没有命中目标 (点错位置 / 元素不可点击 / 焦点丢失 / 操作被屏蔽)。"
                "**请重新观察当前截图,核对目标元素的实际位置,不要假设上一动作已经成功**。"
                "若确认元素已正确点击但 UI 加载慢,可调用 wait()。"
            )
        if score < 4.0:
            return (
                "⚠️ 截图变化：非常微弱。请仔细比对前后截图,确认上一动作是否真正命中目标元素。"
                "如果目标元素仍然存在且未被激活,**很可能是点错位置,需要重新定位**。"
            )
        if score < 12.0:
            return "截图变化：有轻微变化。"
        return "截图变化：明显变化。"

    def make_region_focus_snapshot(
        self,
        snapshot: Snapshot,
        click_xy: tuple[int, int],
        *,
        region_size: int = 192,
        inset_size: int = 320,
    ) -> Snapshot:
        """围绕一次失败点击 (cx, cy) 生成 RegionFocus 复合截图。

        在原 snapshot 上画红框标出点击区域,并把该区域放大后拼到右下角,
        让模型既看到全局又看清局部。返回一个全新的 Snapshot,
        保留原 width/height/actual_*/title 元信息。

        借鉴 UI-TARS RegionFocus / VLAA-GUI Loop-Breaker 思路:点错位置时
        给模型一个"我应该看哪里"的提示,而不是让它对着同一张全局截图反复猜。
        """
        try:
            image = Image.open(snapshot.path).convert("RGB")
        except Exception:
            return snapshot
        canvas_w, canvas_h = image.size
        cx, cy = click_xy
        cx = max(0, min(canvas_w - 1, int(cx)))
        cy = max(0, min(canvas_h - 1, int(cy)))
        half = max(48, region_size // 2)
        left = max(0, cx - half)
        top = max(0, cy - half)
        right = min(canvas_w, cx + half)
        bottom = min(canvas_h, cy + half)
        if right <= left or bottom <= top:
            return snapshot
        crop = image.crop((left, top, right, bottom))
        target = max(160, inset_size)
        crop = crop.resize((target, target), Image.Resampling.LANCZOS)
        annotated = image.copy()
        draw = ImageDraw.Draw(annotated)
        # 1) 在原坐标处画红色十字 + 矩形,清晰标出"点击位置"和"参考观察范围"。
        draw.rectangle((left, top, right - 1, bottom - 1), outline=(220, 40, 40), width=3)
        cross_arm = 14
        draw.line((cx - cross_arm, cy, cx + cross_arm, cy), fill=(255, 200, 0), width=3)
        draw.line((cx, cy - cross_arm, cx, cy + cross_arm), fill=(255, 200, 0), width=3)
        # 2) 把放大的局部贴到右下角,留 12px 边距,带红色边框 + 标题条。
        inset_x = max(0, canvas_w - target - 12)
        inset_y = max(0, canvas_h - target - 12)
        annotated.paste(crop, (inset_x, inset_y))
        draw.rectangle(
            (inset_x - 2, inset_y - 2, inset_x + target + 1, inset_y + target + 1),
            outline=(220, 40, 40),
            width=3,
        )
        # 标题条 (黑底 + 黄色文字)
        label = f"RegionFocus  ({cx},{cy})  +-{half}px"
        bar_h = 18
        bar_top = max(0, inset_y - bar_h - 2)
        draw.rectangle(
            (inset_x - 2, bar_top, inset_x + target + 1, bar_top + bar_h),
            fill=(0, 0, 0),
        )
        try:
            draw.text((inset_x + 4, bar_top + 2), label, fill=(255, 220, 60))
        except Exception:
            pass
        self._snapshot_index += 1
        path = self.session_root / f"{self._snapshot_index:03d}_region_focus.jpg"
        buffer = io.BytesIO()
        annotated.save(path, format="JPEG", quality=self.settings.jpeg_quality, optimize=True)
        annotated.save(buffer, format="JPEG", quality=self.settings.jpeg_quality, optimize=True)
        return Snapshot(
            path=path,
            data_url=f"data:image/jpeg;base64,{base64.b64encode(buffer.getvalue()).decode('ascii')}",
            width=canvas_w,
            height=canvas_h,
            actual_width=snapshot.actual_width,
            actual_height=snapshot.actual_height,
            foreground_window_title=snapshot.foreground_window_title,
            visible_window_titles=list(snapshot.visible_window_titles),
        )

    def _mask_own_window(self, image) -> tuple[int, int, int, int] | None:
        title = (self.settings.own_window_title or "").strip()
        if not title:
            return None
        hwnd = user32.FindWindowW(None, title)
        if not hwnd:
            return None
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return None
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        clipped = self._clip_rect(rect.left, rect.top, rect.right, rect.bottom, image.size[0], image.size[1])
        if clipped is None:
            return None
        left, top, right, bottom = clipped
        draw = ImageDraw.Draw(image)
        draw.rectangle((left, top, right, bottom), fill=(236, 231, 223), outline=(176, 162, 148), width=2)
        pad = 18
        inner_left = min(right - 8, left + pad)
        inner_top = min(bottom - 8, top + pad)
        inner_right = max(inner_left + 20, right - pad)
        inner_bottom = max(inner_top + 20, bottom - pad)
        draw.rectangle((inner_left, inner_top, inner_right, inner_bottom), outline=(196, 182, 168), width=1)
        return clipped

    def _move(self, x: int, y: int) -> None:
        rx, ry = self.to_real_coordinate(x, y)
        user32.SetCursorPos(rx, ry)

    def _click(self, x: int, y: int, *, times: int, button: str) -> None:
        self._move(x, y)
        down_flag, up_flag = {
            "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
            "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
            "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
        }[button]
        for _ in range(times):
            user32.mouse_event(down_flag, 0, 0, 0, 0)
            time.sleep(0.03)
            user32.mouse_event(up_flag, 0, 0, 0, 0)
            time.sleep(0.08)

    def _drag(self, start_x: int, start_y: int, end_x: int, end_y: int) -> None:
        start_rx, start_ry = self.to_real_coordinate(start_x, start_y)
        end_rx, end_ry = self.to_real_coordinate(end_x, end_y)
        user32.SetCursorPos(start_rx, start_ry)
        time.sleep(0.04)
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.06)
        steps = 12
        for index in range(1, steps + 1):
            x = int(start_rx + (end_rx - start_rx) * index / steps)
            y = int(start_ry + (end_ry - start_ry) * index / steps)
            user32.SetCursorPos(x, y)
            time.sleep(0.01)
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

    def _scroll(self, amount: int, x: int | None, y: int | None) -> None:
        if x is not None and y is not None:
            self._move(x, y)
        user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, int(amount) * WHEEL_DELTA, 0)

    def _hotkey(self, keys: list[str]) -> None:
        self._press_down(keys)
        self._release(keys)

    def _paste_text(self, text: str) -> None:
        self._set_clipboard_text(text)
        self._hotkey(["ctrl", "v"])

    def _press_down(self, keys: Iterable[str]) -> None:
        for token in keys:
            vk = self._vk_code(token)
            user32.keybd_event(vk, user32.MapVirtualKeyW(vk, 0), 0, 0)
            time.sleep(0.01)

    def _release(self, keys: Iterable[str]) -> None:
        for token in reversed(list(keys)):
            vk = self._vk_code(token)
            user32.keybd_event(vk, user32.MapVirtualKeyW(vk, 0), KEYEVENTF_KEYUP, 0)
            time.sleep(0.01)

    def to_real_coordinate(self, x: int, y: int) -> tuple[int, int]:
        return (
            self.translate_coordinate(x, capture_size=self.capture_width, actual_size=self.actual_width),
            self.translate_coordinate(y, capture_size=self.capture_height, actual_size=self.actual_height),
        )

    def screenshot_to_physical(self, x: int, y: int) -> tuple[int, int]:
        """把截图坐标转回物理屏幕坐标，供 visual overlay 直接画波纹。

        这是 to_real_coordinate 的公开别名，按设计文档 3.2 节命名。
        """
        return self.to_real_coordinate(x, y)

    @staticmethod
    def get_foreground_window_title() -> str:
        hwnd = user32.GetForegroundWindow()
        return WindowsDesktopController._window_title(hwnd)

    def _find_window_by_title(self, query: str) -> tuple[int, str] | None:
        normalized_query = query.strip().casefold()
        if not normalized_query:
            return None
        for hwnd, title in self._visible_windows():
            if self._titles_match(title, self.settings.own_window_title):
                continue
            if normalized_query in title.casefold():
                return hwnd, title
        return None

    @staticmethod
    def _visible_windows() -> list[tuple[int, str]]:
        windows: list[tuple[int, str]] = []

        def callback(hwnd: int, _lparam: int) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            title = WindowsDesktopController._window_title(hwnd)
            if title:
                windows.append((hwnd, title))
            return True

        enum_proc = EnumWindowsProc(callback)
        user32.EnumWindows(enum_proc, 0)
        return windows

    @staticmethod
    def _window_title(hwnd: int) -> str:
        if not hwnd:
            return ""
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value.strip()

    @staticmethod
    def translate_coordinate(value: int, *, capture_size: int, actual_size: int) -> int:
        if capture_size <= 0:
            raise ValueError("capture_size must be positive.")
        clamped = max(0, min(int(value), capture_size - 1))
        return int(round((clamped / max(1, capture_size - 1)) * max(1, actual_size - 1)))

    @staticmethod
    def to_capture_coordinate(value: int, *, actual_size: int, capture_size: int) -> int:
        if actual_size <= 0:
            raise ValueError("actual_size must be positive.")
        clamped = max(0, min(int(value), actual_size - 1))
        return int(round((clamped / max(1, actual_size - 1)) * max(1, capture_size - 1)))

    @staticmethod
    def _clip_rect(left: int, top: int, right: int, bottom: int, width: int, height: int) -> tuple[int, int, int, int] | None:
        clipped_left = max(0, min(left, width))
        clipped_top = max(0, min(top, height))
        clipped_right = max(0, min(right, width))
        clipped_bottom = max(0, min(bottom, height))
        if clipped_right - clipped_left < 4 or clipped_bottom - clipped_top < 4:
            return None
        return clipped_left, clipped_top, clipped_right, clipped_bottom

    def _capture_rect_from_actual(self, rect: tuple[int, int, int, int]) -> tuple[int, int, int, int] | None:
        left, top, right, bottom = rect
        clipped = self._clip_rect(left, top, right, bottom, self.actual_width, self.actual_height)
        if clipped is None:
            return None
        clipped_left, clipped_top, clipped_right, clipped_bottom = clipped
        capture_left = self.to_capture_coordinate(clipped_left, actual_size=self.actual_width, capture_size=self.capture_width)
        capture_top = self.to_capture_coordinate(clipped_top, actual_size=self.actual_height, capture_size=self.capture_height)
        capture_right = self.to_capture_coordinate(max(clipped_left + 1, clipped_right - 1), actual_size=self.actual_width, capture_size=self.capture_width)
        capture_bottom = self.to_capture_coordinate(max(clipped_top + 1, clipped_bottom - 1), actual_size=self.actual_height, capture_size=self.capture_height)
        return capture_left, capture_top, capture_right, capture_bottom

    def _coordinate_in_masked_region(self, x: int | None, y: int | None) -> bool:
        if x is None or y is None:
            return False
        for left, top, right, bottom in self._last_masked_regions_capture:
            if left <= int(x) <= right and top <= int(y) <= bottom:
                return True
        return False

    def _out_of_bounds_points(self, arguments: dict[str, Any]) -> list[tuple[str, tuple[int, int]]]:
        action = str(arguments.get("action") or "").strip().lower()
        fields: tuple[str, ...]
        if action in {"left_click", "double_click", "right_click", "middle_click", "mouse_move", "scroll"}:
            fields = ("coordinate",)
        elif action in {"left_click_drag", "drag"}:
            fields = ("start_coordinate", "end_coordinate")
        else:
            fields = ()
        points: list[tuple[str, tuple[int, int]]] = []
        for field in fields:
            x, y = self._optional_coordinate(arguments.get(field))
            if x is None or y is None:
                continue
            if not self._coordinate_in_capture_bounds(x, y):
                points.append((field, (int(x), int(y))))
        return points

    def _coordinate_in_capture_bounds(self, x: int, y: int) -> bool:
        return 0 <= int(x) < self.capture_width and 0 <= int(y) < self.capture_height

    @staticmethod
    def _titles_match(left: str, right: str) -> bool:
        return (left or "").strip().casefold() == (right or "").strip().casefold()

    @staticmethod
    def _title_contains(title: str, query: str) -> bool:
        normalized_title = (title or "").strip().casefold()
        normalized_query = (query or "").strip().casefold()
        return bool(normalized_title and normalized_query and normalized_query in normalized_title)

    @staticmethod
    def _expected_window_title(arguments: dict[str, Any]) -> str:
        return str(arguments.get("expected_window_title") or arguments.get("target_window_title") or "").strip()

    @staticmethod
    def _snapshot_difference_score(before_path: Path, after_path: Path) -> float | None:
        try:
            with Image.open(before_path) as before_image, Image.open(after_path) as after_image:
                before_small = before_image.convert("L").resize((96, 54))
                after_small = after_image.convert("L").resize((96, 54))
                diff = ImageChops.difference(before_small, after_small)
                mean = ImageStat.Stat(diff).mean[0]
                return float(mean)
        except Exception:
            return None

    @staticmethod
    def _seconds(arguments: dict[str, Any]) -> float:
        if arguments.get("seconds") is not None:
            return max(0.0, float(arguments["seconds"]))
        if arguments.get("duration_ms") is not None:
            return max(0.0, float(arguments["duration_ms"]) / 1000.0)
        return 1.0

    @staticmethod
    def _post_action_delay(action: str, arguments: dict[str, Any]) -> float:
        if action == "wait":
            return 0.0
        if action == "activate_window":
            return 0.35
        if action == "key":
            keys = WindowsDesktopController._normalize_keys(arguments.get("keys"))
            if any(key in {"enter", "return"} for key in keys):
                return 0.8
            if any(key in {"tab", "space"} for key in keys):
                return 0.35
        if action in {"left_click", "double_click", "right_click", "middle_click"}:
            return 0.35
        if action == "type":
            return 0.25
        return 0.2

    @staticmethod
    def _normalize_keys(raw: Any) -> list[str]:
        if raw is None:
            return []
        if isinstance(raw, str):
            return [chunk.strip().lower() for chunk in raw.split("+") if chunk.strip()]
        if isinstance(raw, list):
            return [str(item).strip().lower() for item in raw if str(item).strip()]
        return []

    @staticmethod
    def _require_coordinate(raw: Any) -> tuple[int, int]:
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            raise ValueError("Expected a coordinate [x, y].")
        return int(raw[0]), int(raw[1])

    @staticmethod
    def _optional_coordinate(raw: Any) -> tuple[int | None, int | None]:
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            return None, None
        return int(raw[0]), int(raw[1])

    @staticmethod
    def _vk_code(token: str) -> int:
        token = token.strip().lower()
        if token in SPECIAL_KEYS:
            return SPECIAL_KEYS[token]
        if len(token) == 1:
            if token.isalnum():
                return ord(token.upper())
            code = user32.VkKeyScanW(ord(token))
            return int(code & 0xFF)
        raise ValueError(f"Unsupported key token: {token}")

    @staticmethod
    def _set_clipboard_text(text: str) -> None:
        payload = text.encode("utf-16-le") + b"\x00\x00"
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(payload))
        if not handle:
            raise ctypes.WinError()
        locked = kernel32.GlobalLock(handle)
        if not locked:
            kernel32.GlobalFree(handle)
            raise ctypes.WinError()
        ctypes.memmove(locked, payload, len(payload))
        kernel32.GlobalUnlock(handle)
        opened = False
        for _ in range(8):
            if user32.OpenClipboard(None):
                opened = True
                break
            time.sleep(0.05)
        if not opened:
            kernel32.GlobalFree(handle)
            raise ctypes.WinError()
        keep_handle = True
        try:
            user32.EmptyClipboard()
            if not user32.SetClipboardData(CF_UNICODETEXT, handle):
                raise ctypes.WinError()
            keep_handle = False
        finally:
            user32.CloseClipboard()
            if keep_handle:
                kernel32.GlobalFree(handle)
