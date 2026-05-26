# Computer Use Anywhere v3

Windows 本地版 `computer use` 框架。不需要 Docker/X11，直接控制你的桌面，兼容中转站、Anthropic 官方协议和任何支持视觉+工具调用的模型。

## 快速上手

1. 双击 `dist\ComputerUseAnywhere\ComputerUseAnywhere.exe`
2. **接口地址**填你的中转站地址(末尾 `/` 可加可不加,`/v1`、`/chat/completions`、`/messages`、`/models` 都会自动适配)
3. **API Key** 填你的 key
4. **模型**手填,或点输入框右侧 **⟳** 按钮拉取中转站可用模型列表后从弹窗里选
5. **目标分辨率**保持 `1280x720`
6. **任务**里写需求,比如 `打开记事本,输入"hello world",保存到桌面`
7. 点 **开始运行**

## 四种运行模式

| 模式 | 协议 | 认证 | 适用场景 |
|---|---|---|---|
| **兼容模式** | OpenAI chat/completions | Bearer | 中转站、OpenRouter、任意视觉+tool calling 服务 |
| 官方体验兼容 | OpenAI chat/completions（单 computer 工具） | Bearer | 中转站测试 Anthropic 官方工作流 |
| **半官方模式 v3** | Anthropic Messages API 请求体 | Bearer | 中转站端到端 Claude（最佳效果） |
| 官方模式 | Anthropic Messages API | x-api-key | 直连 api.anthropic.com |

### 半官方模式 — v3 创新

专门为**中转站用户想要 Claude 原生效果**设计：
- 请求体走 Anthropic Messages API 格式（含 `computer_20251124` 工具定义）
- 认证用 Bearer Token（兼容中转站）
- beta 头可选填（中转站透传就填，不透传可不填）
- Claude 模型收到原生 computer use 协议后表现远超 OpenAI-compatible 模式

> 适用于接口路径为 `/v1/messages` 的中转站。如果路径里只有 `/chat/completions`，先用兼容模式试试。

## v3 新功能

### 固定分辨率截图（Anthropic 2026.5 最佳实践）

| 选项 | 说明 |
|---|---|
| `1280x720` | 通用推荐，最稳定 |
| `1920x1080` | Opus 4.7 可用，点击更精细 |
| `max_api_fit` | 自动算最优尺寸 |
| `scale` | 传统比例缩放（legacy） |

### 双模型顾问策略
主模型执行,步骤失败(越界/前台不安全/执行异常/无效果)时自动切换更强的顾问模型介入修正。

顾问 UI 按主模式自动分支:

| 主模式 | 顾问可选范围 | UI 表现 |
|---|---|---|
| 官方模式(直连 api.anthropic.com) | 只能也走官方,共用主 API Key | 预设下拉:`claude-opus-4-7` / `claude-sonnet-4-6` / `claude-haiku-4-5-20251001` |
| 兼容 / 官方体验 / 半官方(中转站) | 任意:同站、异站、混搭官方直连 | 单选「继承主连接」(默认)或「自定义」;两种都有 ⟳ 拉模型列表 |

### 模型列表自动拉取
任意输入框旁的 **⟳** 按钮会调 `/v1/models` 拉中转站当前可用模型,弹出紧凑下拉供选择。
- 自动套 Chrome User-Agent + Origin/Referer(根据 base_url 自动推),规避 Cloudflare WAF 1010 拦截
- 会复用用户在「附加请求设置」里配的 `extra_headers`(优先级最高)
- 顾问 ⟳ 在「继承」模式下用主 base_url + key,「自定义」模式下用顾问自己的

### 长对话自动压缩（compact-2026-01-12）
长对话自动压缩历史防 token 超限。半官方模式或官方模式下可用。

### 内置技能
`file_read` / `file_write` / `shell` —— 模型可调用本地能力。

### 独立 MCP Server
```powershell
python -m computer_use_anywhere.mcp_server --target-resolution=1280x720
```

## 中转站用户指南

### 怎么选模式

```
中转站接口带 /chat/completions → 兼容模式
中转站接口带 /messages 且透传 beta → 半官方模式（效果最好）
中转站接口带 /messages 但不透传 beta → 半官方模式（beta 头留空）
直连 api.anthropic.com → 官方模式
```

### 推荐配置

```
模式：半官方模式 v3（如果中转站支持 /messages）或 兼容模式
目标分辨率：1280x720
max_tokens：4096
最大步数：30
浏览器 DOM：网页任务时开启
```

### 任务怎么写

**好**：打开 Chrome，导航到 baidu.com，在搜索框输入"今天天气"，点击搜索按钮，截图确认结果。

**差**：帮我在网上搜一下天气。

### 常见问题

1. **坐标准不准** → 分辨率固定 1280x720,不要用 scale
2. **输入打错窗口** → 勾选"运行时自动隐藏本窗口"
3. **模型不调工具** → 换模型／任务里强调"必须调用工具"
4. **步数不够** → 最大步数拉到 50

### 错误码速查 / 故障排查

遇到运行出错弹窗,先看 HTTP 状态码再对照下表:

| 状态码 / 错误码 | 根因 | 解决方案 |
|---|---|---|
| **HTTP 403 + Cloudflare 1010** (`browser_signature_banned`) | 中转站套了 Cloudflare WAF,识别出 Python urllib 默认 UA 直接 ban | v3.1 起 provider 已自动注入 Chrome UA + Origin + Referer;若仍触发,在「附加请求设置 → 额外请求头 JSON」里手动加 `{"User-Agent":"Mozilla/5.0 ... Chrome/...","Origin":"https://你的域名","Referer":"https://你的域名/"}` |
| **HTTP 403 + Cloudflare 1015** (`rate_limited`) | 你的 IP 在中转站 Cloudflare 限频名单 | 等几分钟;或挂代理;或换 IP |
| **HTTP 401 / 403** (非 Cloudflare) | API Key 无效 / 余额不足 / 模型未授权 | 检查 key、充值、确认模型在你的套餐里 |
| **HTTP 404 (`/v1/messages` 或 `/v1/chat/completions`)** | 中转站路径不支持当前模式协议 | 半官方/官方模式需要 `/messages`;兼容模式需要 `/chat/completions`。切换模式或换中转站 |
| **HTTP 404 (`/v1/models`)** | 中转站没暴露模型列表端点 | ⟳ 按钮无法用,模型名手填即可 |
| **HTTP 429** | 速率限制 / 余额不足 | 等待 / 充值 / 在请求体里加 `max_tokens` 限制 |
| **HTTP 400 `messages.*: image exceeds 5 MB`** | 截图太大(2K/4K 屏 PNG 过大) | 目标分辨率改 `1280x720`;JPEG 质量调到 60-70 |
| **HTTP 400 `tool_use ids ... did not have tool_result blocks immediately after`** | 对话历史顺序坏掉(中转站非透传) | 用半官方模式而非兼容模式;或开启 compact 长对话压缩 |
| **HTTP 500 / 502 / 504** (含 `upstream error: do_request_failed` / `new_api_error`) | 中转站后端转发上游失败 / Anthropic 上游故障 / 中转站 socket 超时 | 重试;换模型(部分模型在某些中转站尚未配通);切换其它中转站(各家中转站对 `/v1/messages` 协议透传的稳定性差异较大);确认账户余额和限速;**这是中转站本身的 bug,不是本工具的代码问题** |
| **`MissingPostHeader: x-api-key`** | 半官方模式中转站强制要求 `x-api-key` 而不是 Bearer | 切到官方模式;或在 extra_headers 手动塞 `x-api-key` |
| **`Provider returned non-JSON response`** | 中转站返回 HTML 错误页(通常是 Cloudflare 拦截或网关错误) | 看完整响应,通常归 1010/1015/502;参照上面对应行 |
| **`'App' object has no attribute '_xxx'`** | 你跑的是旧版本 EXE | 双击 `dist\ComputerUseAnywhere\ComputerUseAnywhere.exe` 是最新打包的版本 |
| **坐标全偏 / 点不到目标** | 主模型没做 GUI grounding 训练 | 换模型:Claude Sonnet 4.6 / Opus 4.7 / Qwen-VL 系列 |
| **`几乎没有变化`警告反复出现** | 模型给了错位坐标但执行器还是点了 | 启用顾问模型(中转站模式下选「继承主连接」+ 顾问模型选 Opus 4.7) |
| **呼吸灯/HUD 出现在 agent 截图里(穿帮)** | Win10 < Build 19041,`WDA_EXCLUDEFROMCAPTURE` 不可用 | 升级到 Win10 2004(19041)及以上,或在运行参数里关掉「启用可视化反馈」 |
| **呼吸灯/HUD 完全不出现** | 主窗口没隐藏(可视化反馈只在隐藏模式启用),或全局开关已关 | 勾「运行时自动隐藏本窗口」+ 勾「启用可视化反馈」 |
| **HUD 被遮住 / 位置不对** | 上次保存的 hud_position 出了屏 | 删除 settings.json 里 `hud_position` 字段重启,会回到右下角默认位置 |
| **2K/4K 屏上点击位置波纹错位** | DPI 校正未生效 | v3.2 已在启动早期调 `SetProcessDpiAwareness(2)`,如仍错位,检查显示设置缩放是否 100% |

## 当前能力

- 截图、点击、双击、右键、拖拽、滚轮、按键、输入、等待
- activate_window 按标题切窗口
- 输入/回车前台窗口安全检查
- 越界坐标拦截、遮挡区域检测
- 截图变化检测（"几乎没有变化"提醒）
- 坐标格式兼容：[x,y] / {x,y} / 字符串
- 执行出错不中断，回传错误让模型修正
- browser_dom：读 DOM、导航、点选择器、填表单
- 网页任务自动优先 DOM，失败回退截图
- 一键启动调试 Edge / 本机自检
- 启动前静态诊断
- replay.jsonl + replay.html 复盘
- 固定分辨率（1280x720/1920x1080/max_api_fit）
- 双模型顾问策略
- compact 长对话压缩
- 内置 Skill：file_read / file_write / shell

## 运行

```powershell
python run.py
```

启用浏览器 DOM：
```powershell
Start-Process msedge.exe -ArgumentList '--remote-debugging-port=9222 --user-data-dir=%TEMP%\computer-use-edge'
```

## 打包 EXE

```powershell
python -m pip install pyinstaller
powershell.exe -ExecutionPolicy Bypass -File scripts\build_exe.ps1 -Zip
```

产物：
```
dist\ComputerUseAnywhere\ComputerUseAnywhere.exe
dist\ComputerUseAnywhere-portable.zip
```

## MCP Server 模式

```powershell
python -m computer_use_anywhere.mcp_server --target-resolution=1280x720
```

## 更新日志

### v3.2.2 视觉反馈精修 + RegionFocus

跟着 UI-TARS / VLAA-GUI / HyperClick / Fazm 几篇研究做了一轮"点击失败也得让模型知道"的强化:

- **RegionFocus 放大重定位**: 点击类动作 + 验证"画面几乎无变化" + 有合法坐标时,在新截图右下角拼一块该点周围 ±96px 的放大区域,红框 + 黄十字标出上次点击位置,顺便在 followup 文本里明确告诉模型"看清楚那里到底有没有可点击元素"。借鉴 UI-TARS RegionFocus + VLAA-GUI Loop-Breaker 思路。`windows_control.make_region_focus_snapshot()` 实现
- **逐动作微验证**: `replay.verify_action_result` 提取 `click_xy`,对 click / type / scroll / key 分别给出差异化的"无变化"提示(点击说"看 ±40px 范围 / 是否真有元素",输入说"焦点错窗口",滚动说"已到顶底")。再不会让模型对着"几乎没有变化"这种笼统反馈反复猜
- **截图变化阈值的措辞加重**: 0~1.0 分的 score 之前提示模糊;现在直接打"⚠️ 上一动作很可能没有命中目标 (点错位置 / 元素不可点击 / 焦点丢失 / 操作被屏蔽)",把"假装成功"的概率压下来
- **HUD 红色中止按钮**: 之前只有主窗有"停止"按钮,主窗一隐藏就没法中止;现在 HUD 右上角加红色 ■ 按钮,任何状态下都能一键停。配 `_lift_hud()` 周期性 `SetWindowPos(HWND_TOPMOST)`,确保 HUD 永远在 overlay 之上不被吞点击
- **HUD resize 文字立即重排**: `_relayout` 改 width 时同步 `itemconfig(text=...)`,Tk 当下重新换行,不用等下一帧
- **呼吸灯美化**: 待机改 `solid_breath`(主青/顾问橘单色慢呼吸,6 秒/圈),只在运行时启动彩环旋转 + 流光热点;流光高斯衰减改 σ=0.15 系数 0.35(原 σ=0.08 系数 0.5,过尖锐);`TRANSPARENT_COLOR=#FE00FE` 避开 HSV(300°,1.0,1.0) 钻穿色带

### v3.2.1 调优

- **可拉伸 sidebar**: 主窗口左右用 `tk.PanedWindow`,中间灰色分隔条可拖动,左侧最小 380px,右侧主区始终跟随窗口扩大 — 不再被 460px 硬编码挤压
- **呼吸灯改 14px 全彩色相环**: 从 48px 厚带变成 14px 细环;整圈始终是完整 360° 彩虹(顶蓝→右紫红→底红黄→左绿青),色相环顺时针缓慢旋转(待机 60s/圈,执行 20s/圈)
- **numpy 矢量化呼吸灯**: 替换之前的 bytearray Python 循环,单帧 3.8-4.7ms(原 ~12ms),从 30fps 提升到理论 60fps+,体感丝滑
- **HUD 可拉伸**: 右下角加 resize grip,鼠标拖动改 HUD 尺寸,思考文本按 Canvas 像素宽度自动换行(替代之前的硬字符切),长思考能完整看到。尺寸/位置都自动保存到 `hud_size` / `hud_position`
- **字体探测 fallback 链**: `get_chinese_font()` 探测系统已装中文字体,按 Microsoft YaHei UI → 微软雅黑 → SimHei → 黑体 → ... 顺序挑可用的,防止中文乱码
- **点击波纹 60fps**: 所有动效(涟漪/菱形/十字星/箭头/胶囊)从 `step_ms=45-55` 改成 `step_ms=16`,视觉丝滑
- **bug 修复**: 上一版的两个 P0(`Image.new(..., (0,0,0,0))` 黑屏 / `winfo_id` 拿错 HWND 导致鼠标穿透失效)在 v3.2.1 已彻底修
- **新依赖**: 引入 `numpy>=1.24`(呼吸灯矢量化必需,EXE 体积 +~15MB,换来稳定 60fps)

### v3.2 可视化反馈层

主窗口隐藏后不再是黑盒。新增:

- **屏幕边缘呼吸灯**:全屏 click-through overlay,在物理屏幕四边渲染 48px 厚的彩色羽化光带,9 种状态机(启动 sweep / 主待机 / 主执行 / 顾问待机 / 顾问执行 / 警告闪烁 / 错误急闪 / 完成扫尾 / 卡顿提示)
- **点击波纹反馈**:agent 每个动作在物理屏幕对应坐标弹出 11 种差异化视觉(左键涟漪 / 右键菱形 / 双击叠加圈 / 中键旋转十字 / 拖拽箭头尾迹 / 滚轮上下飞箭 / 键盘胶囊 / 文本胶囊)
- **悬浮 HUD**:280×140 可拖拽小窗,实时显示当前步骤进度 / 模型公开思考最新一条 / 即将执行的动作。位置自动保存,下次启动恢复
- **截图穿帮免疫**:`SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE=0x11)`,Win10 19041+ 起 overlay 和 HUD 对所有截图 API(BitBlt / PrintWindow / Desktop Duplication / 屏幕共享 / PrintScreen)完全隐形 — agent 截图看不到自己的视觉反馈
- **DPI 校正**:启动早期调 `SetProcessDpiAwareness(2)`(per-monitor V2),确保 overlay 在 2K/4K 屏上坐标对齐物理像素
- **可关 / 可换主题**:运行参数卡里"启用可视化反馈"开关 + 色彩主题(default / cyber / subtle / monochrome)下拉。关闭后行为完全回退到 v3.1
- **底层契约**:`models.py` 加 `ACTION_*` 常量 + `AGENT_STATE_*` 常量;`ActionResult` 扩展 `action_kind / physical_coord / action_extra`;`AgentEvent.payload` 文档化 schema

### v3.1 增量改进

- **模型列表自动拉取**:主模型 / 顾问模型输入框右侧加 **⟳** 按钮,调 `/v1/models` 拉中转站可用模型,弹出紧凑下拉选择
- **URL 末尾 `/` 自动适配**:`/v1`、`/chat/completions`、`/messages`、`/models` 都会自动拼接,接口地址不用纠结后缀
- **顾问模型按主模式分支 UI**:官方模式下顾问只能预设下拉(共用主 key 走 api.anthropic.com);中转站三模式下加单选「继承主模型连接」/「自定义」,自定义可跨厂商
- **Cloudflare 友好请求头(全链路)**:所有 provider(主聊天 + 顾问聊天)和 ⟳ 拉模型列表都自动套 Chrome UA + Origin/Referer(从 base_url 推导),规避 WAF 1010;同时复用 `extra_headers` 里用户配的头(用户的优先级最高);api.anthropic.com 自动跳过伪装
- **校验更严格**:启用顾问后模型名不能为空;自定义模式下顾问 base_url / API Key 不能为空

### v3 vs v2 完整对比

#### 新增 / 重大重构

| 功能 | v2 | v3 |
|---|---|---|
| 运行模式 | 3种（兼容/官方体验/官方） | **4种（新增半官方 v3）** |
| 半官方模式 | 无 | **新增** — Bearer认证 + Messages API请求体，专为中转站+Claude原生效果设计 |
| 固定分辨率 | 比例缩放（scale=0.8） | **4种策略：1280x720 / 1920x1080 / max_api_fit / scale** |
| Max API Fit 算法 | 无 | **新增**，根据模型家族自动算最优像素预算 |
| 双模型顾问 | 无 | **新增**，失败自动切 Opus 4.7 兜底 |
| 长对话压缩 | 无 | **compact-2026-01-12 beta header** |
| Thinking 强度 | 仅开关 | **medium/high 两档 + 自动预算** |
| max_tokens | 写死 2048 | **UI 可配置** |
| 模型家族自动推断 | 无 | **auto/4.6/opus_4.7** |
| 内置 Skills | 无 | **file_read / file_write / shell** |
| MCP Server | 无 | **独立可运行 stdio 模式** |
| 窗口标题 | 写死中文 | **Computer Use Anywhere** |
| 包名 | claude_computer_use_proxy | computer_use_anywhere |

#### 截图精度改进

v2 用比例缩放（如 scale=0.8），在 2K/4K 屏幕上模型坐标会偏移。v3 默认固定 1280×720，坐标与截图像素一一对应，点击精度大幅提升。

#### 半官方模式核心原理

```
中转站透传 Bearer Token + /messages 路径
  → 请求体用 Anthropic Messages API 格式
  → 工具名用 computer_20251124
  → Claude 模型看到原生协议，computer use 训练记忆被激活
  → 效果远超 OpenAI-compatible 模式
```

#### 顾问模型触发条件

v3 主模型执行每一步，遇到以下情况自动切 Opus 4.7 顾问：
- 坐标越界（被安全阀拦截）
- 目标在遮挡区域
- 前台窗口不安全
- 执行器抛异常
- 动作验证"几乎无变化"

#### Skill 系统

| 技能 | 功能 |
|---|---|
| `file_read` | 读取本地文件内容 |
| `file_write` | 写入文件到本地 |
| `shell` | 执行 shell 命令并返回 stdout/stderr |
| MCP server | 外部 agent 可通过 stdio JSON-RPC 调用本工具 |

#### 废弃/改名

- 包名 `claude_computer_use_proxy` → `computer_use_anywhere`
- EXE 名 `ClaudeComputerUseProxy.exe` → `ComputerUseAnywhere.exe`
- 窗口标题 `Claude 电脑操作代理` → `Computer Use Anywhere`
- `scale=0.8` 比例缩放 → 仍支持但默认改为固定分辨率
- EXE 路径 `dist/ClaudeComputerUseProxy/` → `dist/ComputerUseAnywhere/`

#### Anthropic 2026.5 最佳实践 vs 本项目 v2 时间线

#### Anthropic 2026.5 研究中提到的 — 本项目 v2 已实现

| Anthropic 2026.5 最佳实践 | 本项目 v2 |
|---|---|
| 截图预缩放到固定分辨率（1280×720 / 1920×1080） | 自主实现了比例缩放 + 坐标还原，v3 升级为固定分辨率 |
| `display_width_px/height_px` 与截图尺寸匹配 | 已有实现 |
| 文字指令在截图之前（content ordering） | 已有实现，build_user_message 里 text 在 image 之前 |
| Sonnet 4.6 执行 + Opus 4.7 当顾问 | v2 未有，v3 新增双模型顾问策略 |
| computer_20251124 工具名 | 已有实现，provider.py 里常量定义 |
| compact-2026-01-12 长对话压缩 | v2 未有，v3 新增 |
| thinking 强度 medium/high | v2 仅有开关，v3 新增两档 |

#### Anthropic 2026.5 研究未提及 — 本项目 v2 自主实现

以下能力在 Anthropic 研究中未涉及，但本项目 v2 已有实现：

- **越界坐标安全阀** — 拦截超出截图范围的坐标，不静默夹到边缘
- **前台窗口安全检查** — 输入类动作前校验前台窗口，防止打错窗口
- **遮挡区域检测** — 自动检测代理窗口遮挡的目标区域
- **截图变化检测** — 执行后判断"几乎没有变化"并提醒模型
- **browser DOM 工具** — Chrome DevTools Protocol 直接读写网页 DOM
- **activate_window** — 按标题激活窗口
- **replay 复盘** — 每次会话生成 replay.jsonl + replay.html
- **本机自检** — 启动前检测截图/桌面 API/DOM 端口

> 本项目 v2（2026.5 之前）在 Anthropic 发布最佳实践前已自主实现了上述大部分能力。v3 在此基础上新增了 Anthropic 研究中的推荐特性（固定分辨率、顾问模型、compact 压缩、thinking 强度等），同时保留了 v2 已有的安全阀体系。

#### v3 全版本 vs 学界研究 全景对照表

把项目跟几篇关键研究 (Anthropic 2026.5 best practices / UI-TARS / VLAA-GUI / HyperClick / Fazm) 摆在一起,标出**谁先谁后**:

| 能力 | 学界来源 | 本项目落地版本 | 时序 |
|---|---|---|---|
| 固定分辨率 1280×720 截图 | Anthropic 2026.5 | v3.0 | **同期** (v2 已有比例缩放) |
| display_width_px/height_px 对齐截图 | Anthropic 2026.5 | v2 起一直有 | **早于** Anthropic 公开 |
| 文字指令在截图前 (content ordering) | Anthropic 2026.5 | v2 起一直有 | **早于** Anthropic 公开 |
| 主模型 + 顾问模型 (Sonnet + Opus) | Anthropic 2026.5 | v3.0 | **同期** |
| computer_20251124 工具名 | Anthropic 2026.5 | v2 起一直有 | **早于** Anthropic 公开 |
| compact-2026-01-12 长对话压缩 | Anthropic 2026.5 | v3.0 | **同期** |
| thinking medium/high 两档 | Anthropic 2026.5 | v3.0 | **同期** |
| RegionFocus 放大重定位 | UI-TARS (2025.10) | v3.2.2 | **晚于**, 借鉴落地 |
| 点击失败 Loop-Breaker | VLAA-GUI (2025.11) | v3.2.2 | **晚于**, 借鉴落地 |
| 点击置信度差异化提示 | HyperClick (2025.11) | v3.2.2 (微验证) | **晚于**, 概念借鉴 |
| 后置截图 diff 验证 "假成功" | Fazm (2025.11) | v2 已有 + v3.2.2 强化 | **早于** Fazm |
| 越界坐标安全阀 | — | v2 起 | **学界未提及** |
| 前台窗口安全检查 | — | v2 起 | **学界未提及** |
| 遮挡区域检测 | — | v2 起 | **学界未提及** |
| activate_window 按标题切窗口 | — | v2 起 | **学界未提及** |
| browser_dom (Chrome DevTools 协议) | — | v2 起 | **学界未提及** |
| replay.jsonl + replay.html 复盘 | — | v2 起 | **学界未提及** |
| 截图穿帮免疫 (WDA_EXCLUDEFROMCAPTURE) | — | v3.2 | **学界未提及** |
| 屏幕边缘呼吸灯 + HUD 可视化反馈 | — | v3.2 / v3.2.1 / v3.2.2 | **学界未提及** |
| RegionFocus 自动触发 (无需 advisor 主动 prompt) | — | v3.2.2 | **学界未提及** (UI-TARS 需 advisor 显式重 ground) |

**结论**:
1. 安全阀类能力(越界拦截 / 前台校验 / 遮挡 / DOM / 复盘) 在 v2 (2026.5 之前) 就有,学界研究里至今没有任何一家覆盖
2. Anthropic 2026.5 best practices 提到的能力一半是 v2 已有的,另一半在 v3.0 同期补齐
3. UI-TARS / VLAA-GUI / Fazm 这一波 2025.10-11 的研究在 v3.2.2 借鉴落地(RegionFocus / Loop-Breaker / "假成功" 验证)
4. 可视化反馈层(呼吸灯/HUD/截图穿帮免疫) 是项目自主创新,学界研究里没有对应工作

## 项目结构 v3

```
computer-use-anywhere/
├── src/computer_use_anywhere/
│   ├── agent.py          # 主循环 + 双模型顾问
│   ├── browser_dom.py    # Chrome DevTools Protocol
│   ├── models.py        # 数据模型
│   ├── mcp_server.py     # MCP Server 独立入口
│   ├── provider.py       # 四种协议提供者
│   ├── skills.py         # Skill 注册表 + 内置技能
│   ├── ui.py            # Tkinter 界面
│   ├── windows_control.py # 桌面控制 + 截图
│   └── ...
├── dist/ComputerUseAnywhere/
│   └── ComputerUseAnywhere.exe
├── README.md  ← 你在这
└── pyproject.toml
```