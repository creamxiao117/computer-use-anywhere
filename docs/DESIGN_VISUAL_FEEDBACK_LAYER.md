# 设计方案:Computer Use Anywhere 可视化反馈层 v1

> 本文档由 brainstorm Claude 输出,交给执行 Claude 落地实施。  
> 关联文档:`BRIEFING_UX_BRAINSTORM.md`(需求背景)、`README.md`(项目本体)。  
> 目标:解决"主窗口隐藏后用户无法判断 agent 状态"的黑盒问题,加入呼吸灯、操作光效、点击波纹、可拖拽悬浮窗。

---

## 0. 关键技术决定(影响一切)

### 0.1 用 `WDA_EXCLUDEFROMCAPTURE` 解决截图穿帮

Windows 10 Build 19041(Version 2004)及以上提供 Win32 API:

```python
ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x11)
```

设置后该窗口对**所有截图 API**(BitBlt、PrintWindow、Desktop Duplication API、Zoom/Teams 屏幕共享、PrintScreen 键)完全隐形,但用户肉眼可见。

**意味着**:不需要在 agent 每次截图前手动 `withdraw()` overlay 再 `deiconify()`,系统级解决,零延迟,零闪烁。

**前提**:Windows 10 Build 19041+(2020 年 5 月发布,现在用户机器基本都有)。启动时检测 build 号,版本不够则降级到"截图前 100ms 隐藏 overlay"的兼容路径。

### 0.2 选型:方案 A(单全屏 overlay)+ 独立悬浮窗

```
┌─ 物理屏幕(单显示器) ────────────────────────────┐
│                                                  │
│  Toplevel #1:全屏 overlay                       │
│  ・WS_EX_LAYERED | WS_EX_TRANSPARENT(鼠标穿透) │
│  ・WDA_EXCLUDEFROMCAPTURE(截图隐形)             │
│  ・transparentcolor=magenta(背景透明)           │
│  ・WS_EX_NOACTIVATE(不抢焦点)                  │
│  ・topmost、overrideredirect                    │
│  ・职责:呼吸灯 + 点击波纹 + 操作光效            │
│                                                  │
│                                ┌───────────────┐ │
│                                │ Toplevel #2: │ │
│                                │ 悬浮窗(HUD)│ │
│                                │ 可拖拽、可点 │ │
│                                │ 也设         │ │
│                                │ EXCLUDEFROM- │ │
│                                │ CAPTURE       │ │
│                                └───────────────┘ │
└──────────────────────────────────────────────────┘
```

**理由**:
- 呼吸灯+点击波纹合并到一个 overlay,确保视觉连贯(波纹色相可"渗"到呼吸灯里)。
- 悬浮窗必须独立,因为用户要拖拽——overlay 是鼠标穿透的,无法接收事件。
- 两个窗口都贴 `WDA_EXCLUDEFROMCAPTURE`,agent 截图都看不到。
- 比方案 B(4 个边条+临时波纹窗+悬浮窗)少 4~N 个窗口,任务栏闪烁风险更低,坐标系统一。

---

## 1. 呼吸灯色彩状态机

| 状态 | 触发条件 | 主色相 (HSV) | 动画风格 | 周期 |
|---|---|---|---|---|
| **启动** | overlay 创建后第一帧 | 青→蓝→紫 横扫一圈 | 一次性 sweep | 1.5s |
| **主模型待机** | `kind=status` 且 `agent_state=main_idle` | 蓝紫 (210°→280°) | 慢呼吸 | 3.0s |
| **主模型执行** | `kind=tool` 期间 | 青→品红 (180°→320°) 流光顺时针 | 流动+中速呼吸 | 1.5s |
| **顾问待机** | `agent_state=advisor_idle` | 橙金 (30°→50°) | 慢呼吸 | 3.0s |
| **顾问执行** | 顾问介入期间 + tool | 橙红→金黄 (15°→55°) 流光 | 流动+中速呼吸 | 1.5s |
| **警告** | `kind=warning` | 橙 #FF8800 | 2 次快闪后回到上一态 | 200ms × 2 |
| **错误** | `kind=error` | 红 #FF2222 | 3 次急促闪烁 | 150ms × 3 |
| **完成** | `kind=finished` | 绿 #22FF66 | 沿边一圈"扫过" + 渐隐 | 1.5s 后熄灭 |

**色彩转换原则**:
- 状态切换有 300ms 缓动(HSV 线性插值),避免硬切。
- 流光速度:8 秒/圈(顺时针),为彩带某位置增加 1.5× 亮度的"热点",随时间滑动。
- 呼吸曲线:`alpha = 0.5 + 0.5 * sin(t * 2π / period)`,亮度区间 [0.4, 1.0]。
- 状态优先级:错误 > 警告 > 完成 > 执行 > 待机。警告/错误显示完毕后回到上一态。

---

## 2. 视觉规范

### 2.1 呼吸灯

| 参数 | 值 | 备注 |
|---|---|---|
| 厚度 | 48 px(可配置) | 屏幕内侧,4 条边均匀 |
| 羽化 | 外侧 100% alpha → 内侧 0% alpha | 做出"光晕从屏幕边发出"的霓虹感 |
| 帧率 | 30 fps | Tkinter + Pillow 能稳跑 |
| 渲染方式 | Pillow 离屏生成 RGBA 图 → ImageTk.PhotoImage → Canvas 单 item 切换 | **不要**每帧创建几百个 Canvas item,会卡 |
| 流光宽度 | 20% 周长(高亮带宽度) | |
| 转角处理 | 4 个边条之间柔性过渡,不要硬切 | 用同一张 PIL 图覆盖整圈 |

**实现思路**:
- 离屏画布尺寸:`screen_width × screen_height`(全屏 RGBA,但只在 48px 边缘非透明,中间纯透明)。
- 每帧:用 Pillow `Image.new("RGBA")` + `ImageDraw` 画 4 条带羽化的彩色边,转 `ImageTk.PhotoImage`,Canvas `itemconfig` 替换。
- 性能预算:每帧渲染 <16ms,Pillow 在 1920×1080 边缘 48px(实际需绘制像素 ~37 万)上做 HSV→RGB 数组运算用 numpy 加速;不引入 numpy 也可,纯 Pillow `Image.alpha_composite` 几个预渲染层即可。
- **优化路径(可选)**:预渲染 60 帧色相循环 PNG,运行时只切帧。内存 ~30MB,但 CPU 几乎零开销。

### 2.2 点击波纹与操作光效(每个动作类型独立视觉)

| 动作 (`action_kind`) | 视觉 | 颜色 | 时长 |
|---|---|---|---|
| `left_click` | 同心 2 圈涟漪,中心→外扩散到 80 px,外圈淡内圈亮 | 青 #00DDFF | 450 ms |
| `right_click` | 菱形扩散波纹 | 品红 #FF44AA | 450 ms |
| `double_click` | 双重同心圆叠加,第二圈延迟 100 ms | 青 #00DDFF | 600 ms |
| `middle_click` | 旋转十字星 4 角 | 绿 #66FF66 | 500 ms |
| `drag_start` | 实心圆 + 向外箭头 | 黄 #FFCC00 | 400 ms,然后保持发光直到 drag_end |
| `drag_move` | 起点到当前光标位置的发光虚线,渐淡尾迹 | 黄 #FFCC00 | 实时跟随 |
| `drag_end` | 目标圆环 + 向内箭头,起点同时熄灭 | 黄 #FFCC00 | 400 ms |
| `scroll_up` | 指针位置 ↑↑↑ 三连闪向上飞 | 浅蓝 #88DDFF | 300 ms |
| `scroll_down` | 指针位置 ↓↓↓ 三连闪向下飞 | 浅蓝 #88DDFF | 300 ms |
| `key_press` | 屏幕底部居中弹出键名胶囊(如 "Ctrl+C") | 白底 + 1px 青边 + 黑字 | 600 ms |
| `type_text` | 焦点附近飘出键盘符号 + 打出来的文本预览(>20 字截断) | 半透明白胶囊 | 800 ms |

**渲染**:每个波纹是 overlay 上的一个临时 Canvas item 集合,带 `after()` 调度的渐隐 + 销毁。`flash_action()` 是无阻塞 API,可叠加多个并行波纹。

**坐标系**:`physical_coord` 由 `windows_control.py` 计算后通过 `AgentEvent.payload` 传入 overlay,overlay 直接用物理坐标画。

### 2.3 悬浮窗(HUD)

**尺寸**:280 × 140 px(可拖拽,不可缩放)

**位置**:
- 默认右下角,边距 20 px(`screen_w-300, screen_h-160`)
- 用户拖拽后,关闭/退出时位置写入 `config.json` 的 `visual_feedback.hud_position` 字段
- 下次启动从 config 恢复;config 损坏时回退默认

**外观**:
- 背景:半透明黑 #1A1A1A,`-alpha 0.88`
- 边框:1 px,颜色随呼吸灯主色(状态联动)
- 圆角:8 px(用 `wm_overrideredirect(True)` 拿掉系统边框,Canvas 自绘圆角矩形 + bg=magenta + transparentcolor)
- 字体:Microsoft YaHei UI 9pt normal,标签用 8pt 灰色

**布局**:

```
┌─[ ◉ Step 3/30 ] ━━━━━━━━━━━━━━━━━ [⏸] [□] [✕]─┐  ← 拖拽区(24px 高)
│                                                  │
│ 思考: 我看到登录界面,需要先点击邮箱输入框        │  ← assistant 最新 1 条(2 行)
│                                                  │
│ 动作: 左键单击 @ (597, 558)                       │  ← tool 最新 1 条(1 行)
└──────────────────────────────────────────────────┘
```

**第一行(状态条 24 px 高,拖拽区)**:
- 左:状态彩点(同呼吸灯色,8 px 圆)
- 中:`Step N / M`(M 来自 `SessionConfig.max_steps`)
- 右:三个图标按钮 [暂停] [恢复主窗] [关闭悬浮窗]
  - 暂停:发 signal 给 agent 主循环(可后续实现,先占位)
  - 恢复主窗:调用 `_restore_window_if_hidden()`
  - 关闭悬浮窗:只关 HUD,呼吸灯保留

**第二/三行(内容区)**:
- "思考:" 灰色标签 + assistant 最近一条公开思考,超出 2 行截断 + "..."
- "动作:" 灰色标签 + tool 事件 summary,1 行截断

**emoji 兼容**:Microsoft YaHei UI 不一定有 ◉/⏸/□/✕,如显示为方框,降级到 ASCII(`*` / `||` / `[]` / `X`)。执行 Claude 实测后选择最稳的字符集。

**交互**:
- 鼠标按下顶部 24 px → 拖拽窗口(`<Button-1>` + `<B1-Motion>` 计算 delta)
- 拖拽时呼吸灯继续动画,HUD 透明度临时降到 0.6 提示"拖拽中"

---

## 3. 数据契约改动

### 3.1 `models.py` — 扩展 `AgentEvent.payload`

```python
# kind="tool" 时新增:
payload["action_kind"]: str  # 见下方枚举
payload["physical_coord"]: tuple[int, int] | None  # 物理屏幕坐标
payload["action_extra"]: dict[str, Any]  # 如 {"keys": "Ctrl+C", "text": "hello", "drag_to": (x,y)}

# kind="status" 时新增:
payload["agent_state"]: str  # "main_idle" | "main_running" | "advisor_idle" | "advisor_running"
payload["step_n"]: int
payload["step_total"]: int
```

**ActionKind 枚举**(建议作为 `models.py` 顶层常量):

```
LEFT_CLICK, RIGHT_CLICK, DOUBLE_CLICK, MIDDLE_CLICK,
DRAG_START, DRAG_MOVE, DRAG_END,
SCROLL_UP, SCROLL_DOWN,
KEY_PRESS, TYPE_TEXT,
MOVE_ONLY  # 仅移动鼠标不点击,默认不画波纹
```

### 3.2 `windows_control.py` — 新增能力

```python
@dataclass
class ActionResult:
    action_kind: str
    physical_coord: tuple[int, int] | None
    extra: dict[str, Any]

def screenshot_to_physical(coord: tuple[int, int]) -> tuple[int, int]:
    """把截图坐标(可能被 resize 过)转回物理屏幕坐标。
    考虑当前截图分辨率 vs 物理分辨率的比例。"""
```

让 `_click / _move / _drag / _type / _hotkey / _scroll` 等底层函数执行完后返回 `ActionResult`。

### 3.3 `agent.py` — emit 时补字段

- 调 windows_control 拿到 `ActionResult` 后,在 `self._emit("tool", ..., payload={...})` 时填入 `action_kind / physical_coord / action_extra`。
- 在 `self._emit("status", ...)` 时判断当前是否顾问介入,填入 `agent_state / step_n / step_total`。
- 不需要新增事件 kind,沿用现有的就够。

---

## 4. 实施步骤(给执行 Claude 的逐步清单)

### 步骤 1:新建 `src/computer_use_anywhere/visual_overlay.py`

包含:

- `class BreathingOverlay`
  - `__init__(parent_root: tk.Tk)`:创建 `Toplevel`,设 `overrideredirect / topmost / -alpha 1.0 / -transparentcolor magenta`,然后 ctypes 调:
    - `SetWindowLongW(hwnd, GWL_EXSTYLE, current | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)`
    - `SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)`
  - `set_state(state: str)`:切色彩状态机
  - `flash_action(action_kind: str, physical_coord, extra: dict)`:画波纹
  - `_tick()`:每 33ms 调用 `root.after(33, self._tick)`,重画呼吸灯帧
  - `_render_breath_frame(t: float) -> ImageTk.PhotoImage`:Pillow 生成当前帧
  - `destroy()`

- `class FloatingHUD`
  - `__init__(parent_root, config: dict)`:创建可拖拽 `Toplevel`,同样设 `WDA_EXCLUDEFROMCAPTURE`(但**不**设 `WS_EX_TRANSPARENT`,要可点击)
  - `update_status(state, step_n, step_total)`
  - `update_thinking(text)`
  - `update_action(text)`
  - `_on_drag_press / _on_drag_motion`:拖拽实现
  - `_save_position()`:写 config
  - `_load_position()`:读 config

- 工具函数:
  - `_set_clickthrough(hwnd)`:ctypes 设 WS_EX_LAYERED | WS_EX_TRANSPARENT
  - `_set_exclude_from_capture(hwnd) -> bool`:返回是否成功,失败时 caller 启用降级路径
  - `_check_windows_build() -> int`:获取 build 号判断 WDA 是否可用
  - `_hsv_to_rgb(h, s, v)`:HSV 转 RGB,用于色彩状态机

### 步骤 2:改 `models.py`

- 扩展 `AgentEvent.payload` 文档注释,加 schema 说明
- 新增 `ActionKind` 常量
- 新增 `ActionResult` dataclass(可放这里也可放 windows_control.py)

### 步骤 3:改 `windows_control.py`

- 新增 `screenshot_to_physical(coord)` 函数
- 修改 `_click / _move / _drag / _type / _hotkey / _scroll` 返回 `ActionResult`
- 注意 DPI:确保 `ctypes.windll.shcore.SetProcessDpiAwareness(2)` 在 ui.py 启动早期调用过(如果没调,加上)

### 步骤 4:改 `agent.py`

- 找到所有 `self._emit("tool", ...)` 调用点(README 提到 30+),把 `windows_control` 返回的 `ActionResult` 展开成 payload 字段
- 找到 `self._emit("status", ...)` 调用点,补 `agent_state / step_n / step_total`
- 顾问介入逻辑:agent 自己知道当前是否在顾问模式,直接在 emit 时填

### 步骤 5:改 `ui.py`

- 在 `_hide_window_for_run` 末尾启动 `BreathingOverlay` 和 `FloatingHUD`,保存引用
- 在 `_restore_window_if_hidden` 开头销毁两者
- 在 `event_callback` 里新增路由逻辑:
  ```
  if not self._overlay_enabled: return  # 全局开关
  if event.kind == "status":
      self._overlay.set_state(...)  # 根据 agent_state
      self._hud.update_status(...)
  elif event.kind == "assistant":
      self._hud.update_thinking(event.message)
  elif event.kind == "tool":
      self._hud.update_action(event.message)
      kind = event.payload.get("action_kind")
      coord = event.payload.get("physical_coord")
      if kind and coord:
          self._overlay.flash_action(kind, coord, event.payload.get("action_extra", {}))
  elif event.kind == "warning":
      self._overlay.set_state("warning")  # 自动 2 闪后回上一态
  elif event.kind == "error":
      self._overlay.set_state("error")
  elif event.kind == "finished":
      self._overlay.set_state("done")
  ```

### 步骤 6:UI 主界面加全局开关

在 `ui.py` 配置区加 `ttk.Checkbutton`:"启用可视化反馈"(默认勾选),变量写入 config。运行时根据 `visual_feedback.enabled` 决定是否实例化 overlay。

### 步骤 7:打包验证

- 用 `scripts/build_exe.ps1` 重新打包
- EXE 体积增量预算:< 1MB(因为只用了 stdlib + Pillow + ctypes,Pillow 已经依赖)
- portable.zip 增量预算:< 2MB

---

## 5. 风险与回退

| 风险 | 应对 |
|---|---|
| **WDA_EXCLUDEFROMCAPTURE 在 Win10 < Build 19041 失效** | 启动时 `_check_windows_build()`;不支持则降级:agent 在 `windows_control.capture()` 内部,截图前 `withdraw()` overlay + HUD,截完 `deiconify()`。延迟约 50ms,可接受。 |
| **高 DPI 下 Tkinter 坐标错位** | 在 `run.py` 最开始调 `ctypes.windll.shcore.SetProcessDpiAwareness(2)`(per-monitor V2)。若已有则跳过。 |
| **30 fps Pillow 渲染卡顿** | 第一版用 numpy 加速(可选);若仍卡,改用预渲染 60 帧 PNG 缓存方案。 |
| **overlay 抢前台焦点** | 必须设 `WS_EX_NOACTIVATE`,任何情况下不接管激活窗口。 |
| **任务栏出现 overlay 图标** | `overrideredirect(True)` + `-toolwindow True`(Windows) |
| **悬浮窗位置 config 损坏** | try/except 解析,回退默认右下角 |
| **多线程竞态**(agent 在另一个线程发 event,Tkinter 不允许跨线程更新 UI) | event_callback 用 `root.after(0, lambda: ...)` 包一层,确保 UI 操作在主线程 |
| **用户讨厌彩光** | 全局开关默认开,可在 UI 里关。关闭后行为 = v3.1 原样。 |
| **流光颜色对色盲不友好** | 提供 `color_scheme`:`default` / `cyber` / `subtle` / `monochrome`(纯单色呼吸不用色相变化),用户在 config 选 |
| **agent 截图分辨率与物理屏幕不一致**(常见,截图可能 1280×720,物理 1920×1080) | `screenshot_to_physical` 用 `screen_w / capture_w` 比例换算;agent 在 `windows_control.capture()` 时记录当前的截图分辨率 |
| **用户切换显示器(单屏 → 多屏)** | 暂不处理,文档声明仅支持单屏。检测到多屏时 overlay 只画主屏。 |
| **agent 卡死,overlay 不知道,继续呼吸** | overlay 维护一个 watchdog:5 秒没收到任何事件 → 状态变"stale"(灰色慢呼吸),提示用户 agent 可能挂了 |

---

## 6. 用户可调参数(写到 config.json 的 `visual_feedback` 段)

```json
{
  "visual_feedback": {
    "enabled": true,
    "edge_thickness": 48,
    "breath_period_idle": 3.0,
    "breath_period_active": 1.5,
    "ripple_size": 80,
    "hud_position": [1640, 940],
    "hud_alpha": 0.88,
    "color_scheme": "default",
    "show_keyboard_overlay": true,
    "show_mouse_ripple": true,
    "fps": 30
  }
}
```

UI 界面暂时不必暴露全部参数,只暴露主开关 + `color_scheme` 下拉就够。高级参数让用户改 config。

---

## 7. 验收清单(执行 Claude 跑完应该自测)

- [ ] 启动 agent,主窗口隐藏后立刻看到屏幕四边出现"启动 sweep"动画,1.5s 后进入蓝紫待机
- [ ] agent 开始第一步,呼吸灯切换到青→品红流光
- [ ] agent 点击屏幕某处,该位置出现青色双圈涟漪,持续 ~450ms
- [ ] agent 输入文本,屏幕底部出现键名胶囊
- [ ] 顾问介入,呼吸灯切换到橙金
- [ ] 触发警告,呼吸灯橙色快闪 2 次后回到上一态
- [ ] 截图(用 PrintScreen 键或 Snipping Tool)能看到屏幕内容但**看不到呼吸灯/HUD/波纹**
- [ ] HUD 显示"Step N/M / 思考 / 动作",内容随事件更新
- [ ] 拖拽 HUD 到任意位置,关闭重启后位置恢复
- [ ] 全局开关关闭 → 完全不显示任何 overlay/HUD,与 v3.1 行为一致
- [ ] EXE 重新打包成功,体积增量 < 3MB
- [ ] 在 Win10 Build 19041+ 上 WDA_EXCLUDEFROMCAPTURE 工作正常;在更老版本上自动降级

---

## 8. 工程文件改动总览

| 文件 | 改动类型 | 工作量 |
|---|---|---|
| `src/computer_use_anywhere/visual_overlay.py` | **新建** | ~600 行 |
| `src/computer_use_anywhere/models.py` | 扩展(枚举 + dataclass + payload schema 注释) | ~50 行新增 |
| `src/computer_use_anywhere/windows_control.py` | 修改返回值 + 新增 `screenshot_to_physical` | ~80 行改动 |
| `src/computer_use_anywhere/agent.py` | 补 `_emit` payload 字段 | ~40 行改动 |
| `src/computer_use_anywhere/ui.py` | 启动/销毁 overlay + 事件路由 + 全局开关 | ~80 行改动 |
| `run.py` | DPI awareness(如果还没设) | ~3 行 |
| `README.md` | 文档更新(更新日志、可视化反馈说明) | 增量记录 |

总计:新增 ~650 行,修改 ~250 行,预计 1~2 个 Claude 工作日。

---

## 9. 不在本次范围(后续可迭代)

- 多显示器支持
- 用户拖拽光带厚度 / 自定义色相
- 音效反馈
- HUD 内嵌截图缩略图(可看 agent 当前看到啥)
- 历史动作回看(右键 HUD → 弹出最近 N 步列表)
- agent 长时间无响应时的求救动画

---

## 文档版本

- 创建时间:2026-05-26
- 设计来源:对 `BRIEFING_UX_BRAINSTORM.md` 的回应
- 目标实施版本:v3.2
