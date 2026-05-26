# Computer Use Anywhere v3

Windows 本地版 Computer Use 框架。项目目标是在 Windows 桌面上直接运行一个可用的 GUI Agent 执行器，不依赖 Docker/X11，支持截图、鼠标、键盘、窗口切换、浏览器 DOM 辅助、执行复盘等能力。

它可以对接以下几类模型服务：

- OpenAI-compatible 的中转站或模型服务
- Anthropic Messages API 格式的 Claude 服务
- 支持视觉输入和工具调用的其它模型服务
- 本地或远程 agent，通过 MCP Server 调用本机桌面能力

> 注意：本项目是桌面自动化工具，模型会实际控制鼠标、键盘和窗口。不要在含有隐私、支付、账号密码、生产环境后台的桌面上直接运行。建议使用测试机、虚拟机、单独浏览器配置或低权限账户运行。

---

## 启动方式

本项目提供两种启动方式：

1. 下载 Release 版 EXE，双击运行；
2. 从源码运行 `python run.py`。

普通用户推荐使用 Release 版 EXE，不需要配置 Python 环境。

### 方式一：下载 Release 版 EXE（推荐）

适合只想直接使用的用户。

1. 打开本项目 GitHub Releases 页面；
2. 下载最新版本的 `ComputerUseAnywhere-portable.zip`；
3. 解压压缩包；
4. 进入解压后的目录；
5. 双击运行：

```powershell
ComputerUseAnywhere.exe
```

如果你是从源码打包后的目录运行，EXE 路径一般是：

```powershell
dist\ComputerUseAnywhere\ComputerUseAnywhere.exe
```

### 方式二：从源码运行

适合开发者，或者需要修改源码后再运行的人。

```powershell
python run.py
```

如果提示缺少依赖，请先安装项目依赖后再运行。

## 快速上手

启动程序后，按下面步骤配置：

1. **接口地址**  
   填你的中转站地址或官方 API 地址。  
   末尾 `/` 可加可不加，`/v1`、`/chat/completions`、`/messages`、`/models` 会自动适配。

2. **API Key**  
   填你的 API Key。  
   中转站一般使用 Bearer Token；官方 Anthropic 直连使用 `x-api-key`。

3. **模型**  
   可以手动填写模型名，也可以点击输入框右侧的 **⟳** 按钮拉取可用模型列表。

4. **运行模式**  
   - 中转站只有 `/chat/completions`：优先用 **兼容模式**
   - 中转站支持 `/messages`：优先用 **半官方模式 v3**
   - 直连 `api.anthropic.com`：使用 **官方模式**

5. **目标分辨率**  
   推荐保持默认：

```text
1280x720
```

这是最稳定的通用配置。

6. **填写任务**

示例：

```text
打开记事本，输入 hello world，并保存到桌面。
```

7. 点击 **开始运行**

## 推荐配置

普通中转站用户推荐：

```text
模式：半官方模式 v3（如果中转站支持 /messages）
目标分辨率：1280x720
max_tokens：4096
最大步数：30
浏览器 DOM：网页任务时开启
```

如果中转站不支持 `/messages`，就改用：

```text
模式：兼容模式
接口：/chat/completions
```
## 运行模式

| 模式 | 请求协议 | 认证方式 | 适用场景 |
|---|---|---|---|
| **兼容模式** | OpenAI `chat/completions` | Bearer | 普通中转站、OpenRouter、OpenAI-compatible 服务 |
| **官方体验兼容** | OpenAI `chat/completions`，单 computer 工具映射 | Bearer | 想在兼容接口里测试 Computer Use 风格工作流 |
| **半官方模式 v3** | Anthropic Messages API 请求体 | Bearer | 中转站支持 `/v1/messages`，但认证仍走 Bearer 的场景 |
| **官方模式** | Anthropic Messages API | `x-api-key` | 直连 `api.anthropic.com` |

### 半官方模式 v3

半官方模式是为了中转站用户准备的折中方案：

- 请求体尽量贴近 Anthropic Messages API 格式
- 支持 `computer_20251124` 工具定义
- 认证仍使用中转站常见的 Bearer Token
- beta header 可选填，是否生效取决于中转站是否透传
- 更适合支持 `/v1/messages` 的 Claude 中转站

适用判断：

```text
中转站接口支持 /v1/messages      → 优先试半官方模式
中转站只支持 /v1/chat/completions → 用兼容模式
直连 Anthropic 官方              → 用官方模式
```

半官方模式不保证所有中转站都能正常使用。不同中转站对 Messages API、工具调用、beta header、tool_result 顺序的透传质量差异很大，出问题时优先切换模式或更换中转站测试。

---

## 推荐配置

### 普通任务

```text
模式：半官方模式 v3 或兼容模式
目标分辨率：1280x720
max_tokens：4096
最大步数：30
运行时自动隐藏本窗口：开启
浏览器 DOM：网页任务时开启
```

### 网页任务

```text
浏览器 DOM：开启
目标分辨率：1280x720
任务描述：写清楚网址、目标按钮、最终验证方式
```

推荐写法：

```text
打开 Chrome，进入 baidu.com，在搜索框输入“今天天气”，点击搜索按钮，截图确认搜索结果已经出现。
```

不推荐写法：

```text
帮我搜一下天气。
```

原因是后者目标太模糊，模型容易自己脑补步骤，也不容易判断什么时候算完成。

---

## v3 主要功能

### 固定分辨率截图

v3 默认推荐固定分辨率，减少高分屏、缩放比例、截图尺寸和模型坐标之间的偏移问题。

| 选项 | 说明 |
|---|---|
| `1280x720` | 推荐起点，稳定性优先 |
| `1920x1080` | 高分辨率模式，适合更强模型和更精细 UI |
| `max_api_fit` | 自动按模型预算估算较合适的截图尺寸 |
| `scale` | 传统比例缩放，保留给旧流程使用 |

### 双模型顾问策略

主模型执行任务时，如果遇到以下情况，可以自动切换顾问模型辅助修正：

- 坐标越界
- 目标区域被遮挡
- 前台窗口不安全
- 执行动作抛异常
- 点击、输入、滚动后画面几乎没有变化

顾问模型可以继承主连接，也可以单独配置其它模型或其它中转站。

| 主模式 | 顾问可选范围 | UI 表现 |
|---|---|---|
| 官方模式 | 官方模型，共用主 API Key | 预设模型下拉 |
| 兼容 / 官方体验 / 半官方 | 可继承主连接，也可自定义 | 支持单独填写 base_url、key、模型名 |

### 模型列表自动拉取

模型输入框右侧的 **⟳** 会尝试请求 `/v1/models`，拉取当前中转站可用模型列表。

实现特点：

- 自动适配 base_url 末尾路径
- 自动处理 `/v1`、`/models` 等常见写法
- 请求时会带常见浏览器请求头，尽量减少部分中转站的 WAF 误拦截
- 会复用用户在「附加请求设置」里配置的 extra_headers

如果中转站没有开放 `/v1/models`，这个按钮可能不可用，模型名手填即可。

### 长对话压缩

v3 支持 Anthropic 的 compact 相关能力，用于降低长任务、多轮工具调用时的上下文压力。是否可用取决于当前模式、模型和接口是否支持对应参数。

### 内置技能

内置技能包括：

| 技能 | 功能 |
|---|---|
| `file_read` | 读取本地文件内容 |
| `file_write` | 写入本地文件 |
| `shell` | 执行 shell 命令并返回 stdout / stderr |

这些技能会扩大模型对本机的操作范围，建议只在可信任务和隔离环境中使用。

### MCP Server

本项目可以作为独立 MCP Server 运行，供外部 agent 调用本机桌面能力。

```powershell
python -m computer_use_anywhere.mcp_server --target-resolution=1280x720
```

---

## 中转站用户指南

### 接口地址怎么填

接口地址末尾的 `/` 可加可不加。常见写法都可以尝试：

```text
https://example.com
https://example.com/v1
https://example.com/v1/chat/completions
https://example.com/v1/messages
```

项目会尽量自动适配实际请求路径。

### 模式怎么选

```text
中转站只有 /chat/completions     → 兼容模式
中转站支持 /messages             → 半官方模式 v3
中转站 /messages 不透传 beta     → 半官方模式，beta 头留空再试
直连 api.anthropic.com           → 官方模式
```

### 模型怎么选

优先选择具备以下能力的模型：

- 视觉输入能力
- 工具调用能力
- 较强的 GUI grounding 能力
- 能稳定输出坐标和动作参数

Claude、Qwen-VL、部分 Gemini / OpenAI-compatible 多模态模型都可以尝试，但不同中转站的工具调用兼容性差别很大。

---

## 常见问题

### 坐标准不准

优先尝试：

1. 目标分辨率改为 `1280x720`
2. 不要使用 `scale` 模式
3. 检查 Windows 显示缩放比例
4. 换 GUI grounding 更强的模型
5. 打开顾问模型

### 输入打错窗口

建议开启：

```text
运行时自动隐藏本窗口
前台窗口安全检查
```

输入类动作前，项目会尽量检查当前前台窗口是否符合预期，降低打错窗口的概率。

### 模型不调用工具

可以在任务里明确写：

```text
必须通过工具操作电脑，不要只给文字回答。每一步操作后截图确认结果。
```

同时确认当前模型和中转站确实支持 tool calling。

### 步数不够

把最大步数从 30 调到 50 或更高。长任务建议拆成多个小任务执行。

---

## 错误码速查

| 状态码 / 错误信息 | 常见原因 | 处理建议 |
|---|---|---|
| **HTTP 401 / 403**，非 Cloudflare | API Key 无效、余额不足、模型未授权 | 检查 key、余额、模型权限 |
| **HTTP 403 + Cloudflare 1010** | 中转站 WAF 拦截请求特征 | 尝试配置 Chrome User-Agent、Origin、Referer，或更换中转站 |
| **HTTP 403 + Cloudflare 1015** | IP 被限频 | 等待、换 IP、换网络、降低请求频率 |
| **HTTP 404 `/v1/messages`** | 当前中转站不支持 Messages API | 切到兼容模式或换支持 `/messages` 的中转站 |
| **HTTP 404 `/v1/chat/completions`** | 当前中转站不支持兼容接口 | 切换到半官方 / 官方模式 |
| **HTTP 404 `/v1/models`** | 中转站未开放模型列表 | 模型名手填即可 |
| **HTTP 429** | 速率限制、额度限制、并发限制 | 等待、充值、降低并发或减少 max_tokens |
| **HTTP 400 image exceeds 5 MB** | 截图太大 | 改 `1280x720`，降低截图质量或避免 2K/4K 原图 |
| **tool_use ids did not have tool_result blocks immediately after** | 工具调用历史顺序不符合接口要求，或中转站非完整透传 | 优先尝试半官方模式，必要时开启 compact 或更换中转站 |
| **HTTP 500 / 502 / 504** | 中转站上游失败、超时或模型未配通 | 重试、换模型、换中转站、确认余额和限速 |
| **MissingPostHeader: x-api-key** | 接口要求 `x-api-key` 而不是 Bearer | 切官方模式，或在 extra_headers 中手动补充 |
| **Provider returned non-JSON response** | 返回了 HTML 错误页，常见于 WAF 或网关错误 | 查看完整响应内容，对照 1010 / 1015 / 502 处理 |
| **'App' object has no attribute '_xxx'** | 运行了旧版本 EXE | 确认启动的是最新 `dist\ComputerUseAnywhere\ComputerUseAnywhere.exe` |
| **几乎没有变化** 反复出现 | 模型点击位置不准、元素不可点、焦点不对、页面无响应 | 开启顾问模型，或改用 DOM 辅助 |
| **HUD / 呼吸灯出现在截图里** | 系统版本或截图 API 不支持排除窗口捕获 | 升级 Windows，或关闭可视化反馈 |
| **HUD 不出现** | 主窗口没有隐藏，或可视化反馈开关关闭 | 开启自动隐藏主窗口和可视化反馈 |
| **HUD 位置异常** | 保存的位置跑出屏幕 | 删除 `settings.json` 中的 `hud_position` 后重启 |

---

## 当前能力

- 截图
- 点击、双击、右键、中键
- 拖拽、滚轮
- 按键、组合键、输入文本
- 等待
- 按窗口标题激活窗口
- 输入 / 回车前台窗口安全检查
- 越界坐标拦截
- 遮挡区域检测
- 执行后截图变化检测
- 坐标格式兼容：`[x,y]`、`{x,y}`、字符串坐标
- 执行出错不中断，将错误回传给模型修正
- 浏览器 DOM：读 DOM、导航、点选择器、填表单
- 网页任务自动优先 DOM，失败回退截图
- 一键启动调试 Edge
- 启动前静态诊断
- replay.jsonl + replay.html 复盘
- 固定分辨率：`1280x720`、`1920x1080`、`max_api_fit`
- 双模型顾问策略
- 长对话 compact 支持
- 内置 Skill：`file_read` / `file_write` / `shell`
- 独立 MCP Server

---

## 运行

源码运行：

```powershell
python run.py
```

启用浏览器 DOM：

```powershell
Start-Process msedge.exe -ArgumentList '--remote-debugging-port=9222 --user-data-dir=%TEMP%\computer-use-edge'
```

打包 EXE：

```powershell
python -m pip install pyinstaller
powershell.exe -ExecutionPolicy Bypass -File scripts\build_exe.ps1 -Zip
```

产物：

```text
dist\ComputerUseAnywhere\ComputerUseAnywhere.exe
dist\ComputerUseAnywhere-portable.zip
```

MCP Server 模式：

```powershell
python -m computer_use_anywhere.mcp_server --target-resolution=1280x720
```

---

## 更新日志

### v3.2.2 视觉反馈精修 + RegionFocus

这一版重点处理“模型点错了还以为成功”的问题，增强失败反馈和恢复能力。

- **RegionFocus 放大重定位**：点击类动作后，如果画面几乎无变化，并且存在合法点击坐标，会在新截图中拼接点击点附近的局部放大区域，帮助模型重新判断目标位置。
- **逐动作微验证**：针对 click / type / scroll / key 给出不同的失败反馈，不再只返回笼统的“几乎没有变化”。
- **点击失败提示增强**：当变化分数很低时，明确提示可能存在点错位置、元素不可点击、焦点丢失、操作被屏蔽等情况。
- **HUD 红色中止按钮**：主窗口隐藏后仍可通过 HUD 快速停止任务。
- **HUD resize 文字重排**：调整 HUD 宽度后，文本立即按新宽度换行。
- **呼吸灯美化**：待机状态改为更轻量的单色慢呼吸，运行状态保留彩环和流光效果。

这些设计与 RegionFocus、GUI Agent 失败恢复、GUI grounding 置信度校准等公开方向存在思路上的相似之处，但本项目实现主要面向 Windows 本地执行场景。

### v3.2.1 调优

- **可拉伸 sidebar**：主窗口左右区域改为可拖动分隔条，避免左侧宽度硬编码。
- **呼吸灯改 14px 色相环**：从厚光带改成更细的全彩色相环。
- **numpy 矢量化呼吸灯**：降低单帧渲染开销，提高动画流畅度。
- **HUD 可拉伸**：右下角增加 resize grip，尺寸和位置自动保存。
- **中文字体探测 fallback**：按系统已安装字体选择可用中文字体，减少乱码。
- **点击波纹 60fps**：优化点击、拖拽、滚轮、键盘等反馈动画。
- **bug 修复**：修复透明图层黑屏、窗口句柄错误导致穿透失效等问题。
- **新增依赖**：引入 `numpy>=1.24`。

### v3.2 可视化反馈层

主窗口隐藏后，运行过程不再完全黑盒。

- **屏幕边缘呼吸灯**：通过全屏 click-through overlay 显示不同运行状态。
- **点击波纹反馈**：每次模型动作在物理屏幕对应坐标显示短暂反馈。
- **悬浮 HUD**：显示当前步骤、模型公开思考摘要和即将执行的动作。
- **截图穿帮规避**：尽量避免 HUD / overlay 被 agent 截图捕获。不同 Windows 版本和截图 API 下效果可能不同。
- **DPI 校正**：启动早期设置 DPI awareness，降低高分屏坐标错位概率。
- **可关闭 / 可换主题**：可视化反馈可以关闭，也支持不同主题。
- **底层事件 schema**：补充 ActionResult 和 AgentEvent 的动作类型、物理坐标、状态字段。

### v3.1 增量改进

- **模型列表自动拉取**：主模型和顾问模型输入框支持点击 **⟳** 拉取 `/v1/models`。
- **URL 末尾路径自动适配**：自动处理 `/v1`、`/chat/completions`、`/messages`、`/models` 等常见路径。
- **顾问模型按主模式分支 UI**：官方模式走官方预设；中转站模式可继承主连接或自定义连接。
- **Cloudflare 友好请求头**：请求模型列表和聊天接口时自动补充常见浏览器请求头，减少部分 WAF 误拦截。
- **校验更严格**：启用顾问后，模型名、base_url、API Key 等关键字段会做更明确的校验。

### v3.0 重大更新

- 新增半官方模式 v3
- 新增固定分辨率策略
- 新增 `max_api_fit`
- 新增双模型顾问策略
- 新增长对话 compact 支持
- 新增 thinking 强度配置
- 新增 max_tokens UI 配置
- 新增模型家族自动推断
- 新增内置 Skill：`file_read` / `file_write` / `shell`
- 新增独立 MCP Server
- 包名改为 `computer_use_anywhere`
- EXE 名改为 `ComputerUseAnywhere.exe`
- 窗口标题改为 `Computer Use Anywhere`

---

## v3 vs v2

### 新增 / 重大重构

| 功能 | v2 | v3 |
|---|---|---|
| 运行模式 | 兼容 / 官方体验 / 官方 | 新增半官方模式 v3 |
| 半官方模式 | 无 | Bearer 认证 + Anthropic Messages API 请求体 |
| 分辨率策略 | 主要依赖比例缩放 | `1280x720` / `1920x1080` / `max_api_fit` / `scale` |
| 双模型顾问 | 无 | 失败时可切换顾问模型辅助修正 |
| 长对话压缩 | 无 | 支持 compact 相关能力 |
| Thinking 配置 | 较简单 | 支持 effort / 强度配置 |
| max_tokens | 固定或较少配置 | UI 可配置 |
| 内置 Skills | 无 | `file_read` / `file_write` / `shell` |
| MCP Server | 无 | 独立 stdio 模式 |
| 包名 | `claude_computer_use_proxy` | `computer_use_anywhere` |
| EXE 名 | `ClaudeComputerUseProxy.exe` | `ComputerUseAnywhere.exe` |

### 截图精度改进

v2 主要使用比例缩放，例如 `scale=0.8`。在 2K / 4K 屏幕或 Windows 缩放比例不是 100% 时，模型坐标和真实桌面坐标更容易出现偏差。

v3 默认推荐固定目标分辨率，例如 `1280x720`。这样可以让模型看到的截图尺寸、工具声明的 display 尺寸和执行器坐标转换更稳定。

### 半官方模式核心思路

```text
中转站支持 /v1/messages
  → 请求体尽量使用 Anthropic Messages API 格式
  → 工具定义使用 computer_20251124
  → 认证仍兼容中转站常见 Bearer Token
  → 在支持透传的中转站上，尽量接近 Claude 原生 Computer Use 工作流
```

效果取决于模型能力和中转站透传质量，不保证所有服务商都可用。

### 顾问模型触发条件

v3 主模型执行每一步时，遇到以下情况可以触发顾问模型：

- 坐标越界
- 目标区域被遮挡
- 前台窗口不安全
- 执行器抛异常
- 动作执行后画面几乎无变化

---

## 与公开资料的关系说明

本项目参考了 Anthropic Computer Use 官方文档，以及 GUI Agent / GUI Grounding 相关公开研究方向。这里的对照只用于说明设计思路。
### Anthropic 官方 Computer Use 相关点

| 官方文档 / 实践方向 | 本项目对应实现 |
|---|---|
| Computer Use 需要应用侧实际执行截图、鼠标、键盘动作 | 本项目实现 Windows 本地执行器 |
| 工具定义包含 `computer_20251124`、`display_width_px`、`display_height_px` 等字段 | 官方模式 / 半官方模式支持相关字段 |
| `computer-use-2025-11-24` beta header | 官方模式支持；半官方模式视中转站透传情况而定 |
| 建议在截图前放置清晰文字任务说明 | 构造消息时保留任务说明和截图顺序 |
| 复杂任务可结合 thinking | v3 提供 thinking 强度配置 |
| 长任务可使用 compaction | v3 支持 compact 相关能力 |

### GUI Agent / GUI Grounding 方向

| 方向 | 公开资料中的相近工作 | 本项目实现 | 说明 |
|---|---|---|---|
| 局部区域放大重定位 | RegionFocus / Visual Test-time Scaling | v3.2.2 RegionFocus 放大图 | 思路相近：放大局部区域，降低背景干扰 |
| 重复失败后的恢复 | VLAA-GUI 的 Recover / Loop Breaker | v3.2.2 逐动作微验证 + 顾问修正 | 思路相近：发现失败后强制改变策略 |
| 点击置信度 / 不确定性 | HyperClick 等 GUI grounding 校准方向 | 对“几乎无变化”等失败状态做更明确反馈 | 目标相近：降低模型对错误点击的过度自信 |
| 执行后验证 | GUI Agent / RPA 中常见的状态检查 | v2 起截图变化检测，v3.2.2 强化 | 工程实现：动作后判断画面是否真的变化 |
| 执行过程复盘 | Agent / RPA 日志系统 | replay.jsonl + replay.html | 工程实现：便于调试和复现 |

### 设计取舍

1. 本项目不是官方 Docker/X11 参考实现的复刻，而是面向 Windows 本地桌面的 Computer Use 执行框架。
2. v3 重点增强了中转站适配、固定分辨率、双模型顾问、compact、MCP Server 和本地可视化反馈。
3. v2 起已经包含越界拦截、前台窗口校验、遮挡检测、截图变化检测、DOM 辅助、replay 复盘等工程安全阀。
4. v3.2.2 的 RegionFocus、逐动作微验证、失败恢复提示等设计，与公开 GUI Agent 研究方向存在思路上的对应关系，但这里仅表示工程实现上的参考和相似。
5. 呼吸灯、HUD、点击波纹、截图穿帮规避等可视化反馈，是本项目为 Windows 本地运行体验做的工程设计。后续如果发现已有公开项目或研究覆盖相同能力，可以继续补充引用和说明。
#### v2 与 Anthropic 官方实践的时间线说明

本项目 v2 在 2026 年 5 月之前已经实现了一批与 Anthropic 后续公开 Computer Use 实践相同或相近的工程能力。这里的意思不是宣称这些能力全部由本项目首创，而是说明：在后续官方实践被更多人关注之前，v2 已经围绕 Windows 本地 Computer Use 场景做了不少对应实现。

| 能力方向 | 本项目 v2 情况 | 说明 |
|---|---|---|
| 截图尺寸与坐标映射 | 已支持比例缩放 + 坐标还原 | v3 后进一步升级为固定分辨率 `1280x720` |
| `display_width_px` / `display_height_px` 对齐 | 已有相关实现 | 用于让模型返回坐标和截图像素保持对应关系 |
| 文本指令在截图前 | 已有实现 | 构造消息时先给任务说明，再给截图，减少模型误解 |
| Computer Use 工具协议字段 | 已有相关实现 | v2 已开始适配 Anthropic Computer Use 工具协议 |
| 越界坐标安全阀 | 已有实现 | 防止模型返回屏幕外坐标后继续误操作 |
| 前台窗口安全检查 | 已有实现 | 防止输入内容打到错误窗口 |
| 遮挡区域检测 | 已有实现 | 防止目标区域被代理窗口或其它窗口遮住 |
| 截图变化检测 | 已有实现 | 执行动作后判断画面是否真的发生变化 |
| browser DOM 工具 | 已有实现 | 网页任务可通过 Chrome DevTools Protocol 读写 DOM |
| activate_window | 已有实现 | 支持按窗口标题切换目标窗口 |
| replay 复盘 | 已有实现 | 每次会话生成 replay 记录，方便回看和调试 |
| 本机自检 | 已有实现 | 启动前检测截图、桌面 API、DOM 端口等环境问题 |

因此，v3 不是从零开始追随官方实践，而是在 v2 已有工程安全阀和桌面控制能力的基础上，继续补齐和强化了固定分辨率、半官方模式、双模型顾问、compact 长对话压缩、thinking 强度配置、可视化反馈层等能力。
#### 小结

- v2 已经实现了一批与 Anthropic 后续公开 Computer Use 实践方向相近的能力，例如截图尺寸/坐标处理、消息顺序、工具协议适配、动作后验证和安全阀机制。
- v3 的重点不是简单“跟进官方”，而是在 v2 的基础上进一步产品化：新增半官方模式、固定分辨率、双模型顾问、长对话压缩、模型列表拉取、MCP Server、内置 Skills 和可视化反馈层。
- 本节只用于说明项目演进时间线和工程设计思路，不宣称所有能力均为本项目首创。
- 
---

## 参考资料

- Anthropic Computer Use Tool 文档：`https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool`
- Anthropic Compaction 文档：`https://platform.claude.com/docs/en/build-with-claude/compaction`
- RegionFocus / Visual Test-time Scaling for GUI Agent Grounding：`https://arxiv.org/abs/2505.00684`
- VLAA-GUI: Knowing When to Stop, Recover, and Search：`https://arxiv.org/abs/2604.21375`
- HyperClick: Advancing Reliable GUI Grounding via Uncertainty Calibration：`https://arxiv.org/abs/2510.27266`

> 以上对照基于当前可检索的公开资料整理，主要用于说明本项目设计思路和相关公开方向之间的关系。如发现已有研究、项目或文档覆盖相同能力，欢迎提交 issue 补充或修正。

---

## 项目结构

```text
computer-use-anywhere/
├── src/computer_use_anywhere/
│   ├── agent.py             # 主循环 + 双模型顾问
│   ├── browser_dom.py       # Chrome DevTools Protocol
│   ├── models.py            # 数据模型
│   ├── mcp_server.py        # MCP Server 独立入口
│   ├── provider.py          # 协议提供者
│   ├── skills.py            # Skill 注册表 + 内置技能
│   ├── ui.py                # Tkinter 界面
│   ├── windows_control.py   # 桌面控制 + 截图
│   └── ...
├── dist/ComputerUseAnywhere/
│   └── ComputerUseAnywhere.exe
├── README.md
└── pyproject.toml
```
