from __future__ import annotations

"""可视化反馈层 v4.0 - 全屏呼吸灯 overlay + 可拖拽/可缩放 HUD。

v4.0 大改:
- 呼吸灯改 14px 细带, 整圈 360° 色相环旋转 + sin 呼吸 + 流光热点
- numpy 矢量化呼吸灯渲染 (目标 <15ms/帧)
- HUD 加右下角 resize grip, 内容随尺寸自适应
- 字体探测 + fallback 链 (Microsoft YaHei UI → 微软雅黑 → ...)
- 文本换行改用 Tk Canvas Text item 的 width 像素自动换行
- 点击波纹/键盘胶囊等动效统一 60fps

实现要点:
- 全屏 Toplevel 鼠标穿透 (WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)
- 截图隐形 (SetWindowDisplayAffinity / WDA_EXCLUDEFROMCAPTURE, Win10 19041+)
- 透明背景靠 transparentcolor=magenta
- Pillow + numpy 30fps 离屏渲染呼吸灯彩边
- HUD 半透明可拖拽 + 可 resize
"""

import ctypes
import math
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageTk


# ---------------------------------------------------------------------------
# 模块级工具函数
# ---------------------------------------------------------------------------

# Win32 常量
_GWL_EXSTYLE = -20
_WS_EX_LAYERED = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_NOACTIVATE = 0x08000000
_WS_EX_TOOLWINDOW = 0x00000080
_WDA_NONE = 0x00
_WDA_EXCLUDEFROMCAPTURE = 0x11  # Win10 19041+


def check_windows_build() -> int:
    """返回 Windows build 号; < 19041 时 WDA_EXCLUDEFROMCAPTURE 不可用。

    非 Windows 平台返回 0。
    """
    if sys.platform != "win32":
        return 0
    try:
        ver = sys.getwindowsversion()  # type: ignore[attr-defined]
        return int(getattr(ver, "build", 0))
    except Exception:
        return 0


def set_clickthrough(hwnd: int) -> None:
    """ctypes 设 WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE。

    用于让全屏 overlay 完全不接收任何鼠标/键盘事件,事件穿透到下层窗口。
    """
    if sys.platform != "win32" or not hwnd:
        return
    try:
        user32 = ctypes.windll.user32
        try:
            get_long = user32.GetWindowLongPtrW
            set_long = user32.SetWindowLongPtrW
            get_long.restype = ctypes.c_ssize_t
            set_long.restype = ctypes.c_ssize_t
            set_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
            get_long.argtypes = [ctypes.c_void_p, ctypes.c_int]
        except AttributeError:
            get_long = user32.GetWindowLongW
            set_long = user32.SetWindowLongW
            get_long.restype = ctypes.c_long
            set_long.restype = ctypes.c_long

        cur = get_long(hwnd, _GWL_EXSTYLE)
        new = cur | _WS_EX_LAYERED | _WS_EX_TRANSPARENT | _WS_EX_NOACTIVATE | _WS_EX_TOOLWINDOW
        set_long(hwnd, _GWL_EXSTYLE, new)
    except Exception:
        pass


def set_exclude_from_capture(hwnd: int) -> bool:
    """ctypes 调 SetWindowDisplayAffinity; 返回是否成功。

    成功后该窗口对所有截图 API 隐形。需要 Win10 Build 19041+。
    """
    if sys.platform != "win32" or not hwnd:
        return False
    if check_windows_build() < 19041:
        return False
    try:
        user32 = ctypes.windll.user32
        user32.SetWindowDisplayAffinity.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        user32.SetWindowDisplayAffinity.restype = ctypes.c_int
        ok = user32.SetWindowDisplayAffinity(hwnd, _WDA_EXCLUDEFROMCAPTURE)
        return bool(ok)
    except Exception:
        return False


def hsv_to_rgb(h: float, s: float, v: float) -> tuple[int, int, int]:
    """HSV(度,0-1,0-1) → RGB(0-255 各分量),标量版。"""
    h = h % 360.0
    if h < 0:
        h += 360.0
    s = max(0.0, min(1.0, s))
    v = max(0.0, min(1.0, v))

    c = v * s
    hh = h / 60.0
    x = c * (1.0 - abs((hh % 2.0) - 1.0))
    if hh < 1:
        r1, g1, b1 = c, x, 0.0
    elif hh < 2:
        r1, g1, b1 = x, c, 0.0
    elif hh < 3:
        r1, g1, b1 = 0.0, c, x
    elif hh < 4:
        r1, g1, b1 = 0.0, x, c
    elif hh < 5:
        r1, g1, b1 = x, 0.0, c
    else:
        r1, g1, b1 = c, 0.0, x
    m = v - c
    return (
        max(0, min(255, int(round((r1 + m) * 255)))),
        max(0, min(255, int(round((g1 + m) * 255)))),
        max(0, min(255, int(round((b1 + m) * 255)))),
    )


def hsv_array_to_rgb(h: np.ndarray, s: np.ndarray, v: np.ndarray) -> np.ndarray:
    """矢量化 HSV → RGB,输入 (N,) 数组 (h 度数, s/v 0-1),返回 (N, 3) uint8。"""
    h = (h % 360.0) / 60.0  # 0..6
    i = np.floor(h).astype(np.int32) % 6
    f = h - np.floor(h)
    p = v * (1.0 - s)
    q = v * (1.0 - s * f)
    t = v * (1.0 - s * (1.0 - f))

    r = np.where(i == 0, v, np.where(i == 1, q, np.where(i == 2, p, np.where(i == 3, p, np.where(i == 4, t, v)))))
    g = np.where(i == 0, t, np.where(i == 1, v, np.where(i == 2, v, np.where(i == 3, q, np.where(i == 4, p, p)))))
    b = np.where(i == 0, p, np.where(i == 1, p, np.where(i == 2, t, np.where(i == 3, v, np.where(i == 4, v, q)))))

    rgb = np.stack([r, g, b], axis=-1)
    return (rgb * 255.0).clip(0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# 字体探测
# ---------------------------------------------------------------------------

_FONT_CACHE: dict[str, str] = {}

_FONT_FALLBACK = [
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "微软雅黑",
    "PingFang SC",
    "SimHei",
    "黑体",
    "Microsoft Sans Serif",
    "Arial",
]


def get_chinese_font(root: tk.Tk) -> str:
    """探测系统可用中文字体,带 fallback 链。结果缓存到 _FONT_CACHE。"""
    if "main" in _FONT_CACHE:
        return _FONT_CACHE["main"]
    try:
        available = set(tkfont.families(root))
    except Exception:
        _FONT_CACHE["main"] = "TkDefaultFont"
        return "TkDefaultFont"
    for name in _FONT_FALLBACK:
        if name in available:
            _FONT_CACHE["main"] = name
            return name
    _FONT_CACHE["main"] = "TkDefaultFont"
    return "TkDefaultFont"


# ---------------------------------------------------------------------------
# 色彩状态机
# ---------------------------------------------------------------------------

_STATES = {
    "startup",
    "main_idle",
    "main_running",
    "advisor_idle",
    "advisor_running",
    "warning",
    "error",
    "done",
    "stale",
}


@dataclass
class _StateSpec:
    """状态对应的视觉参数。

    kind 标志渲染模式:
    - "rotating": 走通用色相环旋转 + sin 呼吸路径
    - "flash":    单色快闪 (warning/error)
    - "sweep":    单圈扫光 (done)
    - "startup":  扫光淡入
    - "stale":    单色慢呼吸
    """
    kind: str = "rotating"
    hue_range: tuple[float, float] = (0.0, 360.0)
    saturation: float = 0.95
    rotate_period: float = 60.0  # 色相旋转一圈秒数, 0 表示不旋转
    v_lo: float = 0.35
    v_hi: float = 0.70
    breath_period: float = 5.0
    flow: bool = False
    # 单色态用 hue (度) + 周期 / 次数
    solid_hue: float = 0.0
    flash_period: float = 0.4
    flash_cycles: int = 2


_STATE_TABLE: dict[str, _StateSpec] = {
    "startup": _StateSpec(
        kind="startup", hue_range=(0.0, 360.0), saturation=0.95,
        rotate_period=1.5, v_lo=0.0, v_hi=0.4, breath_period=1.5, flow=True,
    ),
    "main_idle": _StateSpec(
        kind="solid_breath", solid_hue=190.0, saturation=0.65,
        v_lo=0.35, v_hi=0.62, breath_period=6.0,
    ),
    "main_running": _StateSpec(
        kind="rotating", hue_range=(0.0, 360.0), saturation=1.00,
        rotate_period=25.0, v_lo=0.70, v_hi=1.00, breath_period=3.0, flow=True,
    ),
    "advisor_idle": _StateSpec(
        kind="solid_breath", solid_hue=38.0, saturation=0.70,
        v_lo=0.40, v_hi=0.68, breath_period=6.0,
    ),
    "advisor_running": _StateSpec(
        kind="rotating", hue_range=(20.0, 80.0), saturation=1.00,
        rotate_period=25.0, v_lo=0.75, v_hi=1.00, breath_period=3.0, flow=True,
    ),
    "warning": _StateSpec(
        kind="flash", solid_hue=32.0, saturation=1.0,
        v_lo=0.5, v_hi=1.0, flash_period=0.4, flash_cycles=2,
    ),
    "error": _StateSpec(
        kind="flash", solid_hue=0.0, saturation=1.0,
        v_lo=0.5, v_hi=1.0, flash_period=0.3, flash_cycles=3,
    ),
    "done": _StateSpec(
        kind="sweep", solid_hue=135.0, saturation=0.85,
        v_lo=0.4, v_hi=1.0, rotate_period=1.5,
    ),
    "stale": _StateSpec(
        kind="stale", saturation=0.0,
        v_lo=0.20, v_hi=0.45, breath_period=4.0,
    ),
}


# 不同 color_scheme 的色相位移 / 饱和度倍率
_SCHEME_TWEAKS: dict[str, dict[str, float]] = {
    "default":    {"hue_shift": 0.0, "sat_mul": 1.0},
    "cyber":      {"hue_shift": -30.0, "sat_mul": 1.1},
    "subtle":     {"hue_shift": 0.0, "sat_mul": 0.55},
    "monochrome": {"hue_shift": 0.0, "sat_mul": 0.0},
}


# ---------------------------------------------------------------------------
# BreathingOverlay
# ---------------------------------------------------------------------------


class BreathingOverlay:
    """全屏 Toplevel: 屏幕四边发光的呼吸灯 + 点击波纹光效。

    所有公开方法均线程安全 - 内部用 root.after(0, ...) 切回主线程。
    """

    TRANSPARENT_COLOR = "#FE00FE"  # v4.1: 不用纯 #FF00FF — HSV(300°,1.0,1.0) 会产出 magenta 把光带钻穿
    TRANSPARENT_RGB = (254, 0, 254)
    EDGE_THICKNESS = 18  # v4.0: 14px 太细看不清色相, 提到 18px (仍是 macOS 边框量级)
    FLOW_PERIOD_SEC = 15.0  # 流光绕一圈

    def __init__(self, parent_root: tk.Tk, *, color_scheme: str = "default") -> None:
        self.root = parent_root
        self.color_scheme = color_scheme if color_scheme in _SCHEME_TWEAKS else "default"

        self.screen_w = parent_root.winfo_screenwidth()
        self.screen_h = parent_root.winfo_screenheight()

        self._state: str = "startup"
        self._prev_state: str = "startup"
        self._state_started_at: float = time.time()
        self._fallback_state: str = "main_idle"

        self._t0: float = time.time()
        self._destroyed = False
        self._photo_breath: ImageTk.PhotoImage | None = None
        self._active_effects: list[int] = []
        self._drag_start_xy: tuple[int, int] | None = None
        self._drag_trail_ids: list[int] = []

        # 预算羽化曲线 (沿厚度方向), 缓存
        self._alpha_curve = self._build_alpha_curve(self.EDGE_THICKNESS)

        # 创建 Toplevel
        self.top = tk.Toplevel(parent_root)
        self.top.title("_cua_overlay")
        self.top.overrideredirect(True)
        try:
            self.top.attributes("-topmost", True)
            self.top.attributes("-fullscreen", False)
            self.top.attributes("-alpha", 1.0)
            self.top.attributes("-transparentcolor", self.TRANSPARENT_COLOR)
            try:
                self.top.attributes("-toolwindow", True)
            except tk.TclError:
                pass
        except tk.TclError:
            pass

        self.top.geometry(f"{self.screen_w}x{self.screen_h}+0+0")
        self.top.configure(bg=self.TRANSPARENT_COLOR)

        self.canvas = tk.Canvas(
            self.top,
            width=self.screen_w,
            height=self.screen_h,
            bg=self.TRANSPARENT_COLOR,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True)

        blank = Image.new("RGB", (self.screen_w, self.screen_h), self.TRANSPARENT_RGB)
        self._photo_breath = ImageTk.PhotoImage(blank)
        self._breath_item = self.canvas.create_image(
            0, 0, anchor="nw", image=self._photo_breath
        )

        self.top.update_idletasks()
        try:
            self.top.deiconify()
        except tk.TclError:
            pass

        self._hwnd = self._get_hwnd()
        if self._hwnd:
            set_clickthrough(self._hwnd)
            self._capture_safe = set_exclude_from_capture(self._hwnd)
        else:
            self._capture_safe = False

        self._tick()

    # --------------------------- 公开 API ----------------------------------

    def set_state(self, state: str) -> None:
        """切换呼吸灯状态。线程安全。"""
        if state not in _STATES:
            return
        self.root.after(0, lambda s=state: self._do_set_state(s))

    def flash_action(
        self,
        action_kind: str,
        physical_coord: tuple[int, int] | None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """触发一次动作光效。线程安全。"""
        ext = dict(extra) if extra else {}
        self.root.after(
            0,
            lambda k=action_kind, c=physical_coord, e=ext: self._do_flash(k, c, e),
        )

    def destroy(self) -> None:
        """销毁 overlay。线程安全。"""
        def _do():
            if self._destroyed:
                return
            self._destroyed = True
            try:
                self.top.destroy()
            except Exception:
                pass
        try:
            self.root.after(0, _do)
        except Exception:
            _do()

    # --------------------------- 内部实现 ----------------------------------

    def _get_hwnd(self) -> int:
        """winfo_id() 在 Windows 上返回 Tk 内部 client 子窗口 HWND,
        需要 GetParent 上溯一层拿到真正的 top-level frame HWND。"""
        try:
            child = int(self.top.winfo_id())
        except Exception:
            return 0
        try:
            parent = int(ctypes.windll.user32.GetParent(child))
            return parent if parent else child
        except Exception:
            return child

    def _do_set_state(self, state: str) -> None:
        if self._destroyed or state not in _STATES:
            return
        if state in ("warning", "error"):
            if self._state not in ("warning", "error"):
                self._fallback_state = self._state
            spec = _STATE_TABLE[state]
            back_ms = int(spec.flash_period * 1000 * spec.flash_cycles * 2)
            self.root.after(back_ms, self._do_revert_from_transient)
        self._prev_state = self._state
        self._state = state
        self._state_started_at = time.time()

    def _do_revert_from_transient(self) -> None:
        if self._destroyed:
            return
        if self._state in ("warning", "error"):
            self._state = self._fallback_state if self._fallback_state in _STATES else "main_idle"
            self._state_started_at = time.time()

    def _tick(self) -> None:
        if self._destroyed:
            return
        try:
            self._render_and_show()
        except Exception:
            pass
        try:
            self.root.after(33, self._tick)
        except Exception:
            pass

    def _render_and_show(self) -> None:
        t = time.time() - self._t0
        img = self._render_breath_frame(t)
        photo = ImageTk.PhotoImage(img)
        self._photo_breath = photo
        self.canvas.itemconfig(self._breath_item, image=photo)

    # ---- 呼吸灯帧渲染 ----

    def _current_spec(self) -> _StateSpec:
        return _STATE_TABLE.get(self._state, _STATE_TABLE["main_idle"])

    @staticmethod
    def _build_alpha_curve(thick: int) -> np.ndarray:
        """沿厚度方向的"颜色保留度"曲线 (thick,) float32, 外侧 1.0 → 内侧 0.0。

        v4.2: 把 core 收回到 50% 厚度,留出更宽的羽化区,光晕更柔和。
        前 50% 厚度 curve=1.0 (光带主体),中间 ~40% 平方衰减 (软光晕),
        最内 2px hard-cut magenta (透明键)。
        """
        ys = np.arange(thick, dtype=np.float32)
        core = max(1, int(thick * 0.50))
        denom = max(1, thick - core - 2)
        ramp = np.clip(1.0 - (ys - core) / denom, 0.0, 1.0)
        u = np.where(ys < core, 1.0, ramp)
        u = u ** 1.8  # 更柔和的衰减曲线
        u[-2:] = 0.0
        return u.astype(np.float32)

    def _render_breath_frame(self, t: float) -> Image.Image:
        """生成当前帧的全屏 RGB 图。中间纯 magenta (透明键),四边羽化彩光带。

        v4.1: 切回 RGB 模式 — RGBA 在 Tk transparentcolor 下会产生色染 bug。
        """
        w, h, thick = self.screen_w, self.screen_h, self.EDGE_THICKNESS
        spec = self._current_spec()
        state_t = t - (self._state_started_at - self._t0)

        img = Image.new("RGB", (w, h), self.TRANSPARENT_RGB)

        if spec.kind == "flash":
            self._render_flash(img, spec, state_t, thick, w, h)
        elif spec.kind == "sweep":
            self._render_sweep(img, spec, state_t, thick, w, h)
        elif spec.kind == "startup":
            self._render_startup(img, spec, state_t, thick, w, h)
        elif spec.kind == "stale":
            self._render_stale(img, spec, t, thick, w, h)
        elif spec.kind == "solid_breath":
            self._render_solid_breath(img, spec, t, thick, w, h)
        else:
            self._render_rotating(img, spec, t, thick, w, h)

        return img

    def _paste_four_bands(
        self,
        img: Image.Image,
        rgb_top: np.ndarray,
        rgb_right: np.ndarray,
        rgb_bottom: np.ndarray,
        rgb_left: np.ndarray,
        alpha_top: np.ndarray,
        alpha_right: np.ndarray,
        alpha_bottom: np.ndarray,
        alpha_left: np.ndarray,
        thick: int,
    ) -> None:
        """把 4 条边的 RGB 1D 数组组装成带状图并贴到 img。

        v4.1: RGB-only 模式。alpha_* 参数保留兼容旧调用,但实际用 curve 做
        颜色→magenta 的 lerp 来模拟羽化:
            外侧像素 = 原色
            内侧像素 = magenta (透明键)
        a1d 参数现在仅作"额外亮度增益"使用 (流光热点会让该位置 a1d > 255 也 ok)。
        """
        w, h = img.width, img.height
        curve = self._alpha_curve  # (thick,) float32 — 颜色亮度乘子 (前 65% 厚 1.0,后渐变到 0)
        magenta_rgb = np.array(list(self.TRANSPARENT_RGB), dtype=np.float32)
        magenta_row_mask = (curve < 0.02)  # bool (thick,) — 最内 2 行强制 magenta

        def _build(rgb1d: np.ndarray, a1d: np.ndarray, length: int) -> np.ndarray:
            # rgb1d (length, 3) uint8 — 已含 vs 亮度 + 流光 + 色相旋转
            # 输出: (thick, length, 3) uint8 RGB
            cur = curve.reshape(thick, 1, 1)  # (thick, 1, 1)
            rgb = rgb1d.reshape(1, length, 3).astype(np.float32)  # (1, length, 3)
            # 颜色按 curve 亮度衰减: curve=1 → 原色, curve→0 → 接近黑
            # 不经 magenta lerp,避免半路出现紫红色色染
            final = rgb * cur  # (thick, length, 3)
            band = np.clip(final, 0, 255).astype(np.uint8)
            # 最内 2 行 hard-cut 到 magenta (透明键)
            if magenta_row_mask.any():
                band[magenta_row_mask] = magenta_rgb.astype(np.uint8)
            return band

        top_band = _build(rgb_top, alpha_top, w)
        bottom_band = _build(rgb_bottom, alpha_bottom, w)
        left_band = _build(rgb_left, alpha_left, h)
        right_band = _build(rgb_right, alpha_right, h)

        top_img = Image.fromarray(top_band, "RGB")
        bottom_img = Image.fromarray(bottom_band[::-1, :, :], "RGB")
        left_arr = np.transpose(left_band, (1, 0, 2))  # (h, thick, 3)
        left_img = Image.fromarray(left_arr, "RGB")
        right_arr = np.transpose(right_band[::-1, :, :], (1, 0, 2))
        right_img = Image.fromarray(right_arr, "RGB")

        img.paste(top_img, (0, 0))
        img.paste(bottom_img, (0, h - thick))
        img.paste(left_img, (0, 0))
        img.paste(right_img, (w - thick, 0))

    def _perimeter_positions(self, w: int, h: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, float]:
        """各边像素在屏幕周长上的归一化位置 (0..1)。返回 (top, right, bottom, left, perim)。"""
        perim = 2.0 * (w + h)
        top = np.arange(w, dtype=np.float32) / perim
        right = (w + np.arange(h, dtype=np.float32)) / perim
        bottom = (w + h + np.arange(w, dtype=np.float32)) / perim
        left = (2 * w + h + np.arange(h, dtype=np.float32)) / perim
        return top, right, bottom, left, perim

    def _render_rotating(
        self,
        img: Image.Image,
        spec: _StateSpec,
        t: float,
        thick: int,
        w: int,
        h: int,
    ) -> None:
        """通用色相环旋转 + sin 呼吸 + (可选) 流光热点。"""
        tweak = _SCHEME_TWEAKS[self.color_scheme]
        hue_lo, hue_hi = spec.hue_range
        hue_span = hue_hi - hue_lo

        # 色相旋转 (顺时针)
        if spec.rotate_period > 0:
            hue_offset = (t / spec.rotate_period) * 360.0
        else:
            hue_offset = 0.0
        hue_offset += tweak["hue_shift"]
        sat = float(np.clip(spec.saturation * tweak["sat_mul"], 0.0, 1.0))

        # 呼吸亮度
        bp = max(0.1, spec.breath_period)
        breath = 0.5 - 0.5 * math.cos((t / bp) * 2.0 * math.pi)
        v_base = spec.v_lo + (spec.v_hi - spec.v_lo) * breath

        flow_cycle = (t / self.FLOW_PERIOD_SEC) % 1.0 if spec.flow else -1.0

        top_pos, right_pos, bottom_pos, left_pos, _ = self._perimeter_positions(w, h)

        def _band_rgb_alpha(positions: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
            # 色相沿该边位置展开 (位置 0..1, 色相覆盖 hue_range)
            hues = (hue_lo + hue_span * positions + hue_offset) % 360.0
            # 流光增益 (sigma=0.15 — 比 0.08 更柔和宽广)
            if flow_cycle >= 0:
                d = np.abs(positions - flow_cycle)
                d = np.minimum(d, 1.0 - d)
                flow_gain = 1.0 + 0.35 * np.exp(-(d * d) / (2.0 * 0.15 * 0.15))
            else:
                flow_gain = np.ones_like(positions)
            vs = np.clip(v_base * flow_gain, 0.0, 1.0).astype(np.float32)
            sats = np.full_like(hues, sat, dtype=np.float32)
            rgb = hsv_array_to_rgb(hues.astype(np.float32), sats, vs)
            alpha = (vs * 255.0).astype(np.uint8)
            return rgb, alpha

        rgb_t, a_t = _band_rgb_alpha(top_pos)
        rgb_r, a_r = _band_rgb_alpha(right_pos)
        rgb_b, a_b = _band_rgb_alpha(bottom_pos)
        rgb_l, a_l = _band_rgb_alpha(left_pos)

        self._paste_four_bands(img, rgb_t, rgb_r, rgb_b, rgb_l, a_t, a_r, a_b, a_l, thick)

    def _render_stale(
        self,
        img: Image.Image,
        spec: _StateSpec,
        t: float,
        thick: int,
        w: int,
        h: int,
    ) -> None:
        """单色灰慢呼吸,四边均匀。"""
        bp = max(0.1, spec.breath_period)
        breath = 0.5 - 0.5 * math.cos((t / bp) * 2.0 * math.pi)
        v_base = spec.v_lo + (spec.v_hi - spec.v_lo) * breath
        gray = int(np.clip(v_base * 255.0, 0, 255))
        rgb_color = np.array([gray, gray, gray], dtype=np.uint8)
        alpha_val = int(np.clip(v_base * 255.0, 0, 255))

        rgb_t = np.tile(rgb_color, (w, 1))
        rgb_b = np.tile(rgb_color, (w, 1))
        rgb_l = np.tile(rgb_color, (h, 1))
        rgb_r = np.tile(rgb_color, (h, 1))
        a_t = np.full(w, alpha_val, dtype=np.uint8)
        a_b = np.full(w, alpha_val, dtype=np.uint8)
        a_l = np.full(h, alpha_val, dtype=np.uint8)
        a_r = np.full(h, alpha_val, dtype=np.uint8)

        self._paste_four_bands(img, rgb_t, rgb_r, rgb_b, rgb_l, a_t, a_r, a_b, a_l, thick)

    def _render_solid_breath(
        self,
        img: Image.Image,
        spec: _StateSpec,
        t: float,
        thick: int,
        w: int,
        h: int,
    ) -> None:
        """单一色相整圈柔和慢呼吸 — 用于待机态。

        与 _render_stale 区别: 支持 hue/saturation, 不是纯灰。
        appearance: 整圈同色相 (暗青/暖橙等), V 慢呼吸, 没有流光和旋转。
        """
        tweak = _SCHEME_TWEAKS[self.color_scheme]
        bp = max(0.1, spec.breath_period)
        breath = 0.5 - 0.5 * math.cos((t / bp) * 2.0 * math.pi)
        v_base = spec.v_lo + (spec.v_hi - spec.v_lo) * breath
        hue = (spec.solid_hue + tweak["hue_shift"]) % 360.0
        sat = float(np.clip(spec.saturation * tweak["sat_mul"], 0.0, 1.0))

        hue_arr = np.array([hue], dtype=np.float32)
        sat_arr = np.array([sat], dtype=np.float32)
        v_arr = np.array([v_base], dtype=np.float32)
        rgb_color = hsv_array_to_rgb(hue_arr, sat_arr, v_arr)[0]  # (3,)
        alpha_val = int(np.clip(v_base * 255.0, 0, 255))

        rgb_t = np.tile(rgb_color, (w, 1))
        rgb_b = np.tile(rgb_color, (w, 1))
        rgb_l = np.tile(rgb_color, (h, 1))
        rgb_r = np.tile(rgb_color, (h, 1))
        a_t = np.full(w, alpha_val, dtype=np.uint8)
        a_b = np.full(w, alpha_val, dtype=np.uint8)
        a_l = np.full(h, alpha_val, dtype=np.uint8)
        a_r = np.full(h, alpha_val, dtype=np.uint8)

        self._paste_four_bands(img, rgb_t, rgb_r, rgb_b, rgb_l, a_t, a_r, a_b, a_l, thick)

    def _render_flash(
        self,
        img: Image.Image,
        spec: _StateSpec,
        state_t: float,
        thick: int,
        w: int,
        h: int,
    ) -> None:
        """单色快闪 (warning/error)。state_t 为进入该态以来的秒数。"""
        # 闪烁包络: 0..1 三角波 (上升 → 下降, 1 个周期 = 一闪)
        period = max(0.05, spec.flash_period)
        cycle_pos = (state_t / period) % 1.0
        env = 1.0 - abs(2.0 * cycle_pos - 1.0)  # 三角
        # 已超过指定次数的窗口?简单做法: 不衰减, 由 _do_revert_from_transient 切回
        v_base = spec.v_lo + (spec.v_hi - spec.v_lo) * env
        sat = spec.saturation

        # 单色全周长
        hue = spec.solid_hue
        hue_arr = np.array([hue], dtype=np.float32)
        sat_arr = np.array([sat], dtype=np.float32)
        v_arr = np.array([v_base], dtype=np.float32)
        rgb = hsv_array_to_rgb(hue_arr, sat_arr, v_arr)[0]  # (3,)
        alpha_val = int(np.clip(v_base * 255.0, 0, 255))

        rgb_t = np.tile(rgb, (w, 1))
        rgb_b = np.tile(rgb, (w, 1))
        rgb_l = np.tile(rgb, (h, 1))
        rgb_r = np.tile(rgb, (h, 1))
        a_t = np.full(w, alpha_val, dtype=np.uint8)
        a_b = np.full(w, alpha_val, dtype=np.uint8)
        a_l = np.full(h, alpha_val, dtype=np.uint8)
        a_r = np.full(h, alpha_val, dtype=np.uint8)

        self._paste_four_bands(img, rgb_t, rgb_r, rgb_b, rgb_l, a_t, a_r, a_b, a_l, thick)

    def _render_sweep(
        self,
        img: Image.Image,
        spec: _StateSpec,
        state_t: float,
        thick: int,
        w: int,
        h: int,
    ) -> None:
        """单圈扫光 (done)。绿光绕一圈, 同时整圈底光淡入淡出。"""
        period = max(0.1, spec.rotate_period)
        sweep_t = (state_t / period) % 1.0  # 0..1 一圈
        # 底光: 0..0.5 渐入, 0.5..1 渐出
        base_env = math.sin(sweep_t * math.pi)
        v_base = spec.v_lo + (spec.v_hi - spec.v_lo) * base_env
        sat = spec.saturation
        hue = spec.solid_hue

        top_pos, right_pos, bottom_pos, left_pos, _ = self._perimeter_positions(w, h)

        def _band(positions: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
            d = np.abs(positions - sweep_t)
            d = np.minimum(d, 1.0 - d)
            hot = np.exp(-(d * d) / (2.0 * 0.06 * 0.06))
            vs = np.clip(v_base + (1.0 - v_base) * hot, 0.0, 1.0).astype(np.float32)
            hues = np.full_like(positions, hue, dtype=np.float32)
            sats = np.full_like(positions, sat, dtype=np.float32)
            rgb = hsv_array_to_rgb(hues, sats, vs)
            alpha = (vs * 255.0).astype(np.uint8)
            return rgb, alpha

        rgb_t, a_t = _band(top_pos)
        rgb_r, a_r = _band(right_pos)
        rgb_b, a_b = _band(bottom_pos)
        rgb_l, a_l = _band(left_pos)

        self._paste_four_bands(img, rgb_t, rgb_r, rgb_b, rgb_l, a_t, a_r, a_b, a_l, thick)

    def _render_startup(
        self,
        img: Image.Image,
        spec: _StateSpec,
        state_t: float,
        thick: int,
        w: int,
        h: int,
    ) -> None:
        """启动扫光:1.5s 一次,亮度 0.4 → 0.0 衰减。"""
        # 第一圈用 spec.v_hi, 后续衰减
        period = max(0.1, spec.rotate_period)
        cycle = int(state_t / period)
        sweep_t = (state_t / period) % 1.0
        # 亮度从 v_hi → 0
        fade = max(0.0, 1.0 - cycle * 0.3)
        v_peak = spec.v_hi * fade
        if v_peak <= 0.01:
            return

        sat = spec.saturation
        top_pos, right_pos, bottom_pos, left_pos, _ = self._perimeter_positions(w, h)

        # 沿周长撒一个高亮热点 + 全彩色相环
        def _band(positions: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
            hues = (360.0 * positions + state_t * 240.0) % 360.0
            d = np.abs(positions - sweep_t)
            d = np.minimum(d, 1.0 - d)
            hot = np.exp(-(d * d) / (2.0 * 0.08 * 0.08))
            vs = np.clip(v_peak * (0.3 + hot), 0.0, 1.0).astype(np.float32)
            sats = np.full_like(positions, sat, dtype=np.float32)
            rgb = hsv_array_to_rgb(hues.astype(np.float32), sats, vs)
            alpha = (vs * 255.0).astype(np.uint8)
            return rgb, alpha

        rgb_t, a_t = _band(top_pos)
        rgb_r, a_r = _band(right_pos)
        rgb_b, a_b = _band(bottom_pos)
        rgb_l, a_l = _band(left_pos)

        self._paste_four_bands(img, rgb_t, rgb_r, rgb_b, rgb_l, a_t, a_r, a_b, a_l, thick)

    # ---- 动作光效 ----

    def _do_flash(
        self,
        action_kind: str,
        physical_coord: tuple[int, int] | None,
        extra: dict[str, Any],
    ) -> None:
        if self._destroyed:
            return
        try:
            if action_kind == "left_click" and physical_coord:
                self._effect_ripple(physical_coord, "#00DDFF", rings=2, ttl_ms=450)
            elif action_kind == "right_click" and physical_coord:
                self._effect_diamond(physical_coord, "#FF44AA", ttl_ms=450)
            elif action_kind == "double_click" and physical_coord:
                self._effect_ripple(physical_coord, "#00DDFF", rings=2, ttl_ms=600)
                self.root.after(
                    100,
                    lambda c=physical_coord: self._effect_ripple(c, "#00DDFF", rings=2, ttl_ms=500),
                )
            elif action_kind == "middle_click" and physical_coord:
                self._effect_cross_star(physical_coord, "#66FF66", ttl_ms=500)
            elif action_kind == "drag_start" and physical_coord:
                self._drag_start_xy = physical_coord
                self._effect_solid_dot(physical_coord, "#FFCC00", radius=12, ttl_ms=400)
            elif action_kind == "drag_move" and physical_coord and self._drag_start_xy:
                self._effect_drag_trail(self._drag_start_xy, physical_coord, "#FFCC00")
            elif action_kind == "drag_end" and physical_coord:
                self._effect_ring(physical_coord, "#FFCC00", ttl_ms=400)
                self._drag_start_xy = None
                self._clear_drag_trail()
            elif action_kind == "scroll_up" and physical_coord:
                self._effect_arrows(physical_coord, "#88DDFF", up=True, ttl_ms=300)
            elif action_kind == "scroll_down" and physical_coord:
                self._effect_arrows(physical_coord, "#88DDFF", up=False, ttl_ms=300)
            elif action_kind == "key_press":
                keys = str(extra.get("keys", "")) or "Key"
                self._effect_key_capsule(keys, ttl_ms=600)
            elif action_kind == "type_text":
                text = str(extra.get("text", ""))
                self._effect_text_capsule(text, ttl_ms=800)
        except Exception:
            pass

    def _schedule_remove(self, item_ids: list[int], ttl_ms: int) -> None:
        """ttl_ms 毫秒后销毁这些 canvas item。"""
        def _kill():
            for iid in item_ids:
                try:
                    self.canvas.delete(iid)
                except Exception:
                    pass
        try:
            self.root.after(ttl_ms, _kill)
        except Exception:
            pass

    def _effect_ripple(
        self,
        coord: tuple[int, int],
        color: str,
        rings: int = 2,
        ttl_ms: int = 450,
    ) -> None:
        """同心圆扩散涟漪。60fps (step_ms=16)。"""
        x, y = coord
        ids: list[int] = []
        step_ms = 16
        steps = max(8, ttl_ms // step_ms)
        for ring in range(rings):
            iid = self.canvas.create_oval(
                x - 4, y - 4, x + 4, y + 4, outline=color, width=3
            )
            ids.append(iid)

            def _grow(i=iid, base_delay=ring * 80, start_r=4):
                def _step(s):
                    if self._destroyed:
                        return
                    progress = s / steps
                    r = start_r + (80 - start_r) * progress
                    try:
                        self.canvas.coords(i, x - r, y - r, x + r, y + r)
                        width = max(1, int(3 * (1.0 - progress * 0.6)))
                        self.canvas.itemconfig(i, width=width)
                    except Exception:
                        return
                    if s < steps:
                        self.root.after(step_ms, lambda: _step(s + 1))
                self.root.after(base_delay, lambda: _step(1))

            _grow()
        self._schedule_remove(ids, ttl_ms + rings * 80 + 40)

    def _effect_diamond(self, coord: tuple[int, int], color: str, ttl_ms: int) -> None:
        x, y = coord
        ids: list[int] = []
        max_r = 70
        step_ms = 16
        steps = max(8, ttl_ms // step_ms)
        iid = self.canvas.create_polygon(
            x, y - 6, x + 6, y, x, y + 6, x - 6, y,
            outline=color, fill="", width=3,
        )
        ids.append(iid)

        def _step(s):
            if self._destroyed:
                return
            r = 6 + (max_r - 6) * (s / steps)
            try:
                self.canvas.coords(
                    iid, x, y - r, x + r, y, x, y + r, x - r, y
                )
                width = max(1, int(3 * (1.0 - (s / steps) * 0.7)))
                self.canvas.itemconfig(iid, width=width)
            except Exception:
                return
            if s < steps:
                self.root.after(step_ms, lambda: _step(s + 1))

        self.root.after(step_ms, lambda: _step(1))
        self._schedule_remove(ids, ttl_ms + 40)

    def _effect_cross_star(self, coord: tuple[int, int], color: str, ttl_ms: int) -> None:
        x, y = coord
        ids: list[int] = []
        L = 28
        for ang_deg in (0, 90, 180, 270):
            ang = math.radians(ang_deg)
            x2 = x + L * math.cos(ang)
            y2 = y + L * math.sin(ang)
            iid = self.canvas.create_line(x, y, x2, y2, fill=color, width=3, capstyle="round")
            ids.append(iid)
        step_ms = 16
        steps = max(8, ttl_ms // step_ms)

        def _step(s, rot_deg=0.0):
            if self._destroyed:
                return
            ratio = s / steps
            cur_L = L + 20 * ratio
            for i, ang_deg in enumerate((0, 90, 180, 270)):
                ang = math.radians(ang_deg + rot_deg + 45 * ratio)
                x2 = x + cur_L * math.cos(ang)
                y2 = y + cur_L * math.sin(ang)
                try:
                    self.canvas.coords(ids[i], x, y, x2, y2)
                except Exception:
                    return
            if s < steps:
                self.root.after(step_ms, lambda: _step(s + 1, rot_deg))

        self.root.after(step_ms, lambda: _step(1))
        self._schedule_remove(ids, ttl_ms + 40)

    def _effect_solid_dot(
        self,
        coord: tuple[int, int],
        color: str,
        radius: int = 10,
        ttl_ms: int = 400,
    ) -> None:
        x, y = coord
        iid = self.canvas.create_oval(
            x - radius, y - radius, x + radius, y + radius,
            fill=color, outline="",
        )
        self._schedule_remove([iid], ttl_ms)

    def _effect_ring(self, coord: tuple[int, int], color: str, ttl_ms: int = 400) -> None:
        x, y = coord
        iid = self.canvas.create_oval(
            x - 18, y - 18, x + 18, y + 18, outline=color, width=4
        )
        self._schedule_remove([iid], ttl_ms)

    def _effect_drag_trail(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        color: str,
    ) -> None:
        self._clear_drag_trail()
        x1, y1 = start
        x2, y2 = end
        iid = self.canvas.create_line(
            x1, y1, x2, y2, fill=color, width=3,
            dash=(8, 4), capstyle="round",
        )
        self._drag_trail_ids.append(iid)

    def _clear_drag_trail(self) -> None:
        for iid in self._drag_trail_ids:
            try:
                self.canvas.delete(iid)
            except Exception:
                pass
        self._drag_trail_ids.clear()

    def _effect_arrows(
        self,
        coord: tuple[int, int],
        color: str,
        up: bool,
        ttl_ms: int,
    ) -> None:
        x, y = coord
        direction = -1 if up else 1
        ids: list[int] = []
        for i in range(3):
            offset_y = direction * (10 + i * 18)
            tip_y = y + offset_y
            iid = self.canvas.create_polygon(
                x - 10, tip_y + direction * 8,
                x + 10, tip_y + direction * 8,
                x, tip_y,
                fill=color, outline="",
            )
            ids.append(iid)
        # 简单的位移动画 (60fps)
        step_ms = 16
        steps = max(8, ttl_ms // step_ms)

        def _step(s):
            if self._destroyed:
                return
            ratio = s / steps
            dy = int(direction * 8 * ratio)
            for i, iid in enumerate(ids):
                try:
                    # 重画 (用 itemconfig 改不了 polygon 坐标, 直接 coords)
                    offset_y = direction * (10 + i * 18)
                    tip_y = y + offset_y + dy
                    self.canvas.coords(
                        iid,
                        x - 10, tip_y + direction * 8,
                        x + 10, tip_y + direction * 8,
                        x, tip_y,
                    )
                except Exception:
                    return
            if s < steps:
                self.root.after(step_ms, lambda: _step(s + 1))

        self.root.after(step_ms, lambda: _step(1))
        self._schedule_remove(ids, ttl_ms + 40)

    def _effect_key_capsule(self, keys: str, ttl_ms: int) -> None:
        """屏幕底部居中弹出键名胶囊。"""
        text = keys if keys else "Key"
        cx = self.screen_w // 2
        cy = self.screen_h - 80
        font = (get_chinese_font(self.root), 16, "bold")
        t_id = self.canvas.create_text(
            cx, cy, text=text, fill="#222222", font=font, anchor="center"
        )
        try:
            x0, y0, x1, y1 = self.canvas.bbox(t_id)
        except Exception:
            x0, y0, x1, y1 = cx - 40, cy - 14, cx + 40, cy + 14
        pad_x, pad_y = 18, 8
        bg_id = self.canvas.create_rectangle(
            x0 - pad_x, y0 - pad_y, x1 + pad_x, y1 + pad_y,
            fill="#F8FBFE", outline="#00DDFF", width=1,
        )
        self.canvas.tag_raise(t_id, bg_id)
        self._schedule_remove([bg_id, t_id], ttl_ms)

    def _effect_text_capsule(self, text: str, ttl_ms: int) -> None:
        """飘出键盘符号 + 文本预览。"""
        if not text:
            text = "(empty)"
        if len(text) > 20:
            text = text[:20] + "..."
        display = f"⌨ {text}" if self._supports_keyboard_emoji() else f"[KB] {text}"
        cx = self.screen_w // 2
        cy = self.screen_h - 130
        font = (get_chinese_font(self.root), 14)
        t_id = self.canvas.create_text(
            cx, cy, text=display, fill="#FFFFFF", font=font, anchor="center"
        )
        try:
            x0, y0, x1, y1 = self.canvas.bbox(t_id)
        except Exception:
            x0, y0, x1, y1 = cx - 60, cy - 14, cx + 60, cy + 14
        pad_x, pad_y = 16, 8
        bg_id = self.canvas.create_rectangle(
            x0 - pad_x, y0 - pad_y, x1 + pad_x, y1 + pad_y,
            fill="#222831", outline="#88DDFF", width=1,
        )
        self.canvas.tag_raise(t_id, bg_id)
        self._schedule_remove([bg_id, t_id], ttl_ms)

    def _supports_keyboard_emoji(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# FloatingHUD
# ---------------------------------------------------------------------------


class FloatingHUD:
    """可拖拽 + 可缩放 HUD 悬浮窗, 显示状态/思考/动作。

    与 BreathingOverlay 不同, HUD 不鼠标穿透 (要可点击),
    但同样设 WDA_EXCLUDEFROMCAPTURE。
    v4.0: 右下角加 resize grip, 内容随尺寸自适应。
    """

    WIDTH = 280
    HEIGHT = 140
    MIN_WIDTH = 200
    MIN_HEIGHT = 100
    DRAG_BAR_H = 24
    GRIP_SIZE = 10
    BG_COLOR = "#1A1A1A"
    FG_COLOR = "#E8E8E8"
    LABEL_COLOR = "#888888"
    GRIP_COLOR = "#5A5F68"
    TRANSPARENT_KEY = "#FF00FF"  # magenta

    def __init__(
        self,
        parent_root: tk.Tk,
        *,
        position: tuple[int, int] | None = None,
        size: tuple[int, int] | None = None,
        on_close: Callable[[], None] | None = None,
        on_restore: Callable[[], None] | None = None,
        on_stop: Callable[[], None] | None = None,
        on_resize: Callable[[int, int], None] | None = None,
    ) -> None:
        self.root = parent_root
        self._on_close = on_close
        self._on_restore = on_restore
        self._on_stop = on_stop
        self._on_resize = on_resize
        self._destroyed = False

        screen_w = parent_root.winfo_screenwidth()
        screen_h = parent_root.winfo_screenheight()

        # 尺寸
        if size is None:
            self.w = self.WIDTH
            self.h = self.HEIGHT
        else:
            self.w = max(self.MIN_WIDTH, int(size[0]))
            self.h = max(self.MIN_HEIGHT, int(size[1]))

        if position is None:
            x = max(0, screen_w - self.w - 20)
            y = max(0, screen_h - self.h - 40)
        else:
            x, y = position
            x = max(0, min(x, screen_w - self.w))
            y = max(0, min(y, screen_h - self.h))

        self._pos: tuple[int, int] = (x, y)

        # 字体探测 (放最前面, 后面创建 Canvas item 用得到)
        self._font_main = get_chinese_font(parent_root)

        self.top = tk.Toplevel(parent_root)
        self.top.title("_cua_hud")
        self.top.overrideredirect(True)
        try:
            self.top.attributes("-topmost", True)
            self.top.attributes("-alpha", 0.88)
            self.top.attributes("-transparentcolor", self.TRANSPARENT_KEY)
            try:
                self.top.attributes("-toolwindow", True)
            except tk.TclError:
                pass
        except tk.TclError:
            pass

        self.top.geometry(f"{self.w}x{self.h}+{x}+{y}")
        self.top.configure(bg=self.TRANSPARENT_KEY)

        self.canvas = tk.Canvas(
            self.top,
            width=self.w,
            height=self.h,
            bg=self.TRANSPARENT_KEY,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True)

        # 圆角背景
        self._bg_id = self._draw_round_rect(
            0, 0, self.w, self.h, radius=10,
            fill=self.BG_COLOR, outline="#3A4050", width=1,
        )

        # 状态点 + Step 文本
        self._dot_id = self.canvas.create_oval(
            12, self.DRAG_BAR_H // 2 - 5,
            22, self.DRAG_BAR_H // 2 + 5,
            fill="#6688FF", outline="",
        )
        self.step_text_id = self.canvas.create_text(
            32, self.DRAG_BAR_H // 2,
            text="Step 0/0",
            fill=self.FG_COLOR,
            font=(self._font_main, 9, "bold"),
            anchor="w",
        )

        # 三按钮: 中止/还原主窗/关闭 HUD (位置 _relayout 会重算)
        self._btn_stop = self._make_button(
            "■", self.w - 78, self.DRAG_BAR_H // 2, self._on_btn_stop,
            fill="#A02828", outline="#D04040",
        )
        self._btn_restore = self._make_button("□", self.w - 52, self.DRAG_BAR_H // 2, self._on_btn_restore)
        self._btn_close = self._make_button("✕", self.w - 26, self.DRAG_BAR_H // 2, self._on_btn_close)

        # 分隔线
        self._sep_id = self.canvas.create_line(
            8, self.DRAG_BAR_H, self.w - 8, self.DRAG_BAR_H,
            fill="#2A2F38",
        )

        # 思考标签 + 文本 (用 Canvas Text 的 width 像素自动换行)
        self._thinking_label_id = self.canvas.create_text(
            12, self.DRAG_BAR_H + 8,
            text="思考:",
            fill=self.LABEL_COLOR,
            font=(self._font_main, 8),
            anchor="nw",
        )
        self.thinking_text_id = self.canvas.create_text(
            48, self.DRAG_BAR_H + 8,
            text="...",
            fill=self.FG_COLOR,
            font=(self._font_main, 9),
            anchor="nw",
            width=self.w - 58,
        )

        # 动作标签 + 文本
        self._action_label_id = self.canvas.create_text(
            12, self.h - 28,
            text="动作:",
            fill=self.LABEL_COLOR,
            font=(self._font_main, 8),
            anchor="nw",
        )
        self.action_text_id = self.canvas.create_text(
            48, self.h - 28,
            text="...",
            fill=self.FG_COLOR,
            font=(self._font_main, 9),
            anchor="nw",
            width=self.w - 58,
        )

        # 右下角 resize grip
        self._grip_id = self.canvas.create_rectangle(
            self.w - self.GRIP_SIZE - 2, self.h - self.GRIP_SIZE - 2,
            self.w - 2, self.h - 2,
            fill=self.GRIP_COLOR, outline="#888888", width=1,
        )
        # 给 grip 单独绑事件
        self.canvas.tag_bind(self._grip_id, "<Button-1>", self._on_resize_press)
        self.canvas.tag_bind(self._grip_id, "<B1-Motion>", self._on_resize_motion)
        self.canvas.tag_bind(self._grip_id, "<ButtonRelease-1>", self._on_resize_release)
        try:
            self.canvas.tag_bind(self._grip_id, "<Enter>", lambda e: self.top.configure(cursor="size_nw_se"))
            self.canvas.tag_bind(self._grip_id, "<Leave>", lambda e: self.top.configure(cursor=""))
        except tk.TclError:
            pass

        # 绑定拖拽 (整个 canvas, 但 _on_drag_press 内会判断区域)
        self.canvas.bind("<Button-1>", self._on_drag_press)
        self.canvas.bind("<B1-Motion>", self._on_drag_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_drag_release)

        self._drag_offset: tuple[int, int] | None = None
        self._dragging = False
        self._resizing = False
        self._resize_start: tuple[int, int, int, int] | None = None  # (root_x, root_y, w0, h0)

        # 截图隐形
        self.top.update_idletasks()
        try:
            self.top.deiconify()
        except tk.TclError:
            pass
        try:
            child = int(self.top.winfo_id())
            parent = int(ctypes.windll.user32.GetParent(child))
            self._hwnd = parent if parent else child
        except Exception:
            self._hwnd = 0
        if self._hwnd:
            set_exclude_from_capture(self._hwnd)
            try:
                user32 = ctypes.windll.user32
                try:
                    get_long = user32.GetWindowLongPtrW
                    set_long = user32.SetWindowLongPtrW
                    get_long.restype = ctypes.c_ssize_t
                    set_long.restype = ctypes.c_ssize_t
                    set_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
                    get_long.argtypes = [ctypes.c_void_p, ctypes.c_int]
                except AttributeError:
                    get_long = user32.GetWindowLongW
                    set_long = user32.SetWindowLongW
                cur = get_long(self._hwnd, _GWL_EXSTYLE)
                set_long(self._hwnd, _GWL_EXSTYLE, cur | _WS_EX_NOACTIVATE | _WS_EX_TOOLWINDOW)
            except Exception:
                pass
            # 强制 HUD z-order 在 BreathingOverlay 之上 — 防止 overlay click-through
            # 把 HUD 鼠标事件吞了。每 1.5 秒重新提一次防被 overlay 反超。
            self._lift_hud()
            self.root.after(1500, self._periodic_lift)

    # --------------------------- 公开 API ----------------------------------

    def update_status(self, state: str, step_n: int, step_total: int) -> None:
        """更新状态点颜色 + Step N/M。线程安全。"""
        self.root.after(
            0, lambda s=state, n=step_n, m=step_total: self._do_update_status(s, n, m)
        )

    def update_thinking(self, text: str) -> None:
        """更新思考行。Canvas 自动按像素宽度换行,过长才截。线程安全。"""
        self.root.after(0, lambda t=text: self._do_update_thinking(t))

    def update_action(self, text: str) -> None:
        """更新动作行。线程安全。"""
        self.root.after(0, lambda t=text: self._do_update_action(t))

    def get_position(self) -> tuple[int, int]:
        """返回当前左上角坐标 (x, y)。"""
        try:
            return (self.top.winfo_x(), self.top.winfo_y())
        except Exception:
            return self._pos

    def get_size(self) -> tuple[int, int]:
        """返回当前宽高。"""
        return (self.w, self.h)

    def destroy(self) -> None:
        """销毁 HUD。线程安全。"""
        def _do():
            if self._destroyed:
                return
            self._destroyed = True
            try:
                self.top.destroy()
            except Exception:
                pass
        try:
            self.root.after(0, _do)
        except Exception:
            _do()

    # --------------------------- 内部实现 ----------------------------------

    def _lift_hud(self) -> None:
        """用 SetWindowPos(HWND_TOPMOST) 把 HUD 强制提到所有 topmost 窗口的最前面。

        BreathingOverlay 也是 topmost 全屏覆盖,如果 z-order 在 HUD 之上, HUD 区域的
        点击会被 overlay 的 WS_EX_TRANSPARENT 穿透到下层而不是命中 HUD。这里显式提升。
        """
        if not self._hwnd or sys.platform != "win32":
            return
        try:
            HWND_TOPMOST = -1
            SWP_NOSIZE = 0x0001
            SWP_NOMOVE = 0x0002
            SWP_NOACTIVATE = 0x0010
            user32 = ctypes.windll.user32
            user32.SetWindowPos(
                self._hwnd, HWND_TOPMOST,
                0, 0, 0, 0,
                SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE,
            )
        except Exception:
            pass

    def _periodic_lift(self) -> None:
        if self._destroyed:
            return
        self._lift_hud()
        try:
            self.root.after(1500, self._periodic_lift)
        except Exception:
            pass

    def _do_update_status(self, state: str, step_n: int, step_total: int) -> None:
        if self._destroyed:
            return
        color_map = {
            "startup": "#22AAFF",
            "main_idle": "#6688FF",
            "main_running": "#FF44CC",
            "advisor_idle": "#FFAA44",
            "advisor_running": "#FF8822",
            "warning": "#FF8800",
            "error": "#FF3333",
            "done": "#33CC66",
            "stale": "#888888",
        }
        col = color_map.get(state, "#6688FF")
        try:
            self.canvas.itemconfig(self._dot_id, fill=col)
            self.canvas.itemconfig(self.step_text_id, text=f"Step {step_n}/{step_total}")
        except Exception:
            pass

    def _do_update_thinking(self, text: str) -> None:
        if self._destroyed:
            return
        # 长度兜底:5 行以上才省略
        clean = self._truncate_lines(text or "...", max_lines=8, max_chars_per_line=200)
        try:
            self.canvas.itemconfig(self.thinking_text_id, text=clean)
        except Exception:
            pass

    def _do_update_action(self, text: str) -> None:
        if self._destroyed:
            return
        # 动作只显示 1 行, 用 Tk 自动换行 + 1 行截断
        clean = (text or "...").replace("\r", " ").replace("\n", " ").strip()
        try:
            self.canvas.itemconfig(self.action_text_id, text=clean)
        except Exception:
            pass

    @staticmethod
    def _truncate_lines(text: str, max_lines: int, max_chars_per_line: int) -> str:
        """兜底硬截断 (Tk Canvas Text 已经在做按像素自动换行,
        这里只在极端长文本时收尾 "..." 防止 Canvas 卡死)。"""
        text = text.replace("\r", " ").strip()
        if not text:
            return "..."
        # 总字符数兜底
        soft_max = max_lines * max_chars_per_line
        if len(text) > soft_max:
            text = text[: soft_max - 3] + "..."
        return text

    def _draw_round_rect(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        radius: int,
        fill: str,
        outline: str,
        width: int = 1,
    ) -> int:
        """用 polygon + smooth 模拟圆角矩形,返回 polygon id。"""
        points = [
            x1 + radius, y1,
            x2 - radius, y1,
            x2, y1,
            x2, y1 + radius,
            x2, y2 - radius,
            x2, y2,
            x2 - radius, y2,
            x1 + radius, y2,
            x1, y2,
            x1, y2 - radius,
            x1, y1 + radius,
            x1, y1,
        ]
        return self.canvas.create_polygon(
            points, smooth=True, fill=fill, outline=outline, width=width,
        )

    def _round_rect_points(self, x1: int, y1: int, x2: int, y2: int, radius: int) -> list[int]:
        """计算圆角矩形 polygon 坐标列表 (12 个点, 给 canvas.coords 用)。"""
        return [
            x1 + radius, y1,
            x2 - radius, y1,
            x2, y1,
            x2, y1 + radius,
            x2, y2 - radius,
            x2, y2,
            x2 - radius, y2,
            x1 + radius, y2,
            x1, y2,
            x1, y2 - radius,
            x1, y1 + radius,
            x1, y1,
        ]

    def _make_button(
        self,
        label: str,
        cx: int,
        cy: int,
        callback: Callable[[], None],
        *,
        fill: str = "#2A2F38",
        outline: str = "#444A55",
    ) -> dict[str, int]:
        """画一个小方按钮 (背景矩形 + 文字), 返回 ids 字典。"""
        r = 10
        bg = self.canvas.create_rectangle(
            cx - r, cy - r, cx + r, cy + r,
            fill=fill, outline=outline, width=1,
        )
        txt = self.canvas.create_text(
            cx, cy, text=label, fill=self.FG_COLOR,
            font=(self._font_main, 9, "bold"),
        )
        for iid in (bg, txt):
            self.canvas.tag_bind(iid, "<Button-1>", lambda e, cb=callback: self._click_button(cb))
        return {"bg": bg, "txt": txt}

    def _click_button(self, callback: Callable[[], None]) -> None:
        self._drag_offset = None
        try:
            callback()
        except Exception:
            pass

    def _on_btn_stop(self) -> None:
        if self._on_stop:
            try:
                self._on_stop()
            except Exception:
                pass

    def _on_btn_restore(self) -> None:
        if self._on_restore:
            try:
                self._on_restore()
            except Exception:
                pass

    def _on_btn_close(self) -> None:
        if self._on_close:
            try:
                self._on_close()
            except Exception:
                pass
        else:
            self.destroy()

    # ---- 拖拽 ----

    def _on_drag_press(self, event: tk.Event) -> None:
        # 1) 落在按钮区域 (右上角三个 20px 方块) 时, 让 tag_bind 接管, 不进 drag
        btn_left = self.w - 88
        btn_right = self.w - 4
        if event.y <= self.DRAG_BAR_H and btn_left <= event.x <= btn_right:
            self._drag_offset = None
            return
        # 2) 只在拖拽条 (前 24 px) 才允许拖拽, 且不能落在 grip 区
        if event.y > self.DRAG_BAR_H:
            self._drag_offset = None
            return
        if self._resizing:
            return
        self._drag_offset = (event.x, event.y)
        self._dragging = False
        try:
            self.top.attributes("-alpha", 0.6)
        except tk.TclError:
            pass

    def _on_drag_motion(self, event: tk.Event) -> None:
        if self._drag_offset is None or self._resizing:
            return
        self._dragging = True
        dx = event.x - self._drag_offset[0]
        dy = event.y - self._drag_offset[1]
        try:
            new_x = self.top.winfo_x() + dx
            new_y = self.top.winfo_y() + dy
            self.top.geometry(f"+{new_x}+{new_y}")
        except Exception:
            pass

    def _on_drag_release(self, _event: tk.Event) -> None:
        self._drag_offset = None
        self._dragging = False
        try:
            self.top.attributes("-alpha", 0.88)
        except tk.TclError:
            pass
        try:
            self._pos = (self.top.winfo_x(), self.top.winfo_y())
        except Exception:
            pass

    # ---- Resize ----

    def _on_resize_press(self, event: tk.Event) -> None:
        # tag_bind 会优先于 canvas.bind, 所以这里独立处理 resize 流程
        self._resizing = True
        self._drag_offset = None  # 阻止拖拽
        try:
            self._resize_start = (event.x_root, event.y_root, self.w, self.h)
        except Exception:
            self._resize_start = None
        try:
            self.top.attributes("-alpha", 0.7)
        except tk.TclError:
            pass

    def _on_resize_motion(self, event: tk.Event) -> None:
        if not self._resizing or self._resize_start is None:
            return
        rx0, ry0, w0, h0 = self._resize_start
        new_w = max(self.MIN_WIDTH, w0 + (event.x_root - rx0))
        new_h = max(self.MIN_HEIGHT, h0 + (event.y_root - ry0))
        if new_w == self.w and new_h == self.h:
            return
        self.w = new_w
        self.h = new_h
        try:
            x = self.top.winfo_x()
            y = self.top.winfo_y()
            self.top.geometry(f"{self.w}x{self.h}+{x}+{y}")
            self.canvas.configure(width=self.w, height=self.h)
            self._relayout()
        except Exception:
            pass

    def _on_resize_release(self, _event: tk.Event) -> None:
        was_resizing = self._resizing
        self._resizing = False
        self._resize_start = None
        try:
            self.top.attributes("-alpha", 0.88)
        except tk.TclError:
            pass
        if was_resizing and self._on_resize:
            try:
                self._on_resize(self.w, self.h)
            except Exception:
                pass

    def _relayout(self) -> None:
        """根据 self.w / self.h 重算所有 Canvas item 位置/尺寸。"""
        try:
            # 圆角背景
            self.canvas.coords(
                self._bg_id,
                *self._round_rect_points(0, 0, self.w, self.h, radius=10),
            )
            # 三按钮
            for btn, off in (
                (self._btn_stop, self.w - 78),
                (self._btn_restore, self.w - 52),
                (self._btn_close, self.w - 26),
            ):
                r = 10
                cy = self.DRAG_BAR_H // 2
                self.canvas.coords(btn["bg"], off - r, cy - r, off + r, cy + r)
                self.canvas.coords(btn["txt"], off, cy)
            # 分隔线
            self.canvas.coords(self._sep_id, 8, self.DRAG_BAR_H, self.w - 8, self.DRAG_BAR_H)
            # 思考文本: 拿旧文本 + 重设 width + 重设 text → 强制 Tk 立刻 re-wrap
            try:
                old_thinking = self.canvas.itemcget(self.thinking_text_id, "text")
            except tk.TclError:
                old_thinking = ""
            self.canvas.itemconfig(
                self.thinking_text_id,
                width=max(20, self.w - 58),
                text=old_thinking or "...",
            )
            # 动作行位置 (HUD 底部上移)
            action_y = self.h - 28
            self.canvas.coords(self._action_label_id, 12, action_y)
            self.canvas.coords(self.action_text_id, 48, action_y)
            try:
                old_action = self.canvas.itemcget(self.action_text_id, "text")
            except tk.TclError:
                old_action = ""
            self.canvas.itemconfig(
                self.action_text_id,
                width=max(20, self.w - 58),
                text=old_action or "...",
            )
            # 右下角 grip
            self.canvas.coords(
                self._grip_id,
                self.w - self.GRIP_SIZE - 2, self.h - self.GRIP_SIZE - 2,
                self.w - 2, self.h - 2,
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 自检 / 演示入口 (仅手动调试用)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw()

    overlay = BreathingOverlay(root)
    hud = FloatingHUD(root)

    overlay.set_state("main_running")
    hud.update_status("main_running", 1, 30)
    hud.update_thinking("我看到登录界面,需要先点击邮箱输入框")
    hud.update_action("左键单击 @ (597, 558)")

    root.after(1000, lambda: overlay.flash_action("left_click", (600, 400), {}))
    root.after(2000, lambda: overlay.flash_action("key_press", None, {"keys": "Ctrl+C"}))
    root.after(5000, lambda: (overlay.destroy(), hud.destroy(), root.after(200, root.destroy)))

    root.mainloop()
