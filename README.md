#### Anthropic 2026-05 官方 Computer Use 实践对照

本节用于说明本项目和公开 Computer Use / GUI Agent 实践之间的关系。以下内容基于当前可检索到的公开资料整理，主要用于帮助读者理解本项目的设计取舍，不代表对所有研究工作的完整覆盖。

#### Anthropic 官方实践中提到的能力

| Anthropic 官方实践 / 文档方向 | 本项目对应实现 | 说明 |
|---|---|---|
| 固定或受控的截图分辨率，例如 1280×720 作为稳定基线 | v3 默认支持固定分辨率 | 用于减少截图缩放导致的坐标偏移 |
| `display_width_px` / `display_height_px` 与实际截图尺寸保持一致 | v2 起已有相关实现，v3 进一步固定化 | 让模型返回坐标和实际截图像素尽量一一对应 |
| 高分辨率屏幕需要处理坐标缩放问题 | v2 有比例缩放，v3 默认固定分辨率 | v3 避免在 2K/4K 屏上直接把大图丢给模型 |
| `computer_20251124` 工具类型 | v2 / v3 均支持相关协议字段 | 用于 Anthropic Computer Use 新版工具协议 |
| `computer-use-2025-11-24` beta header | 官方模式 / 半官方模式按需配置 | 直连官方时必填，中转站是否透传取决于服务商 |
| Thinking effort 建议 | v3 提供 thinking 强度配置 | 用于长任务、复杂 UI 判断或多步工具调用 |
| 截图前放置清晰文字任务说明 | v2 起已有类似内容顺序处理 | 减少模型误解任务目标 |
| 顾问模型 / 更强模型辅助 | v3 新增双模型顾问策略 | 主模型失败时可切换顾问模型进行修正 |
| 长上下文压缩 | v3 新增长对话 compact 支持 | 用于减少长任务中历史消息导致的 token 压力 |

#### 本项目侧的工程化能力

下面这些能力更多是本项目围绕 Windows 本地桌面控制做的工程化增强。它们不一定对应某一篇论文的原始概念，而是为了解决实际使用中常见的失败模式。

| 能力 | 本项目落地版本 | 主要作用 |
|---|---|---|
| 越界坐标拦截 | v2 起 | 防止模型返回屏幕外坐标后继续误操作 |
| 前台窗口安全检查 | v2 起 | 防止输入内容打到错误窗口 |
| 遮挡区域检测 | v2 起 | 防止目标区域被代理窗口或其它窗口遮住 |
| 截图变化检测 | v2 起，v3.2.2 强化 | 判断点击、输入、滚动后画面是否真的发生变化 |
| `activate_window` 按标题切窗口 | v2 起 | 多窗口任务中提高切换稳定性 |
| browser DOM 辅助 | v2 起 | 网页任务优先使用 DOM，失败后回退截图 |
| replay.jsonl + replay.html 复盘 | v2 起 | 方便回看每一步模型动作和执行结果 |
| 启动前静态诊断 / 本机自检 | v2 起 | 提前发现截图、桌面 API、浏览器调试端口等问题 |
| Windows 本地控制，无需 Docker/X11 | v1 起 | 面向 Windows 桌面直接运行 |

#### v3.2.2 与公开 GUI Agent 研究方向的关系

v3.2.2 的部分设计和近年的 GUI Agent / GUI Grounding 研究方向存在思路上的对应关系。这里不写成“谁完全提出 / 谁完全首创”，只说明公开方向和本项目实现之间的大致关系。

| 方向 | 公开资料中的相近工作 | 本项目实现 | 关系说明 |
|---|---|---|---|
| 局部区域放大重定位 | RegionFocus / Visual Test-time Scaling for GUI Agent Grounding | v3.2.2 RegionFocus 放大图 | 思路相近：在复杂 GUI 中放大局部区域，帮助模型重新定位 |
| 点击失败后的恢复机制 | VLAA-GUI 的 Recover / Loop Breaker 思路 | v3.2.2 逐动作微验证 + 顾问修正 | 思路相近：发现重复失败或无效动作后，强制调整策略 |
| 置信度 / 不确定性相关的 GUI grounding | HyperClick 等 GUI grounding 校准方向 | v3.2.2 对“几乎无变化”等失败反馈做差异化提示 | 目标相近：降低模型对错误点击的过度自信 |
| 后置状态验证 | 多类 GUI Agent / RPA 实践中常见的执行后验证 | v2 起截图变化检测，v3.2.2 强化 | 工程实现：通过截图差异判断动作是否可能失败 |
| 自动化过程复盘 | GUI Agent / RPA 工具常见日志能力 | replay.jsonl + replay.html | 工程实现：便于调试和复现失败链路 |

#### v3 全版本能力对照

| 能力 | 本项目落地版本 | 说明 |
|---|---|---|
| 固定分辨率 1280×720 截图 | v3.0 | 默认推荐配置，减少坐标偏移 |
| 1920×1080 / max_api_fit / scale 多策略 | v3.0 | 兼顾不同模型和不同屏幕环境 |
| display 尺寸与截图尺寸对齐 | v2 起 | v3 中更明确地产品化 |
| Anthropic Messages API 请求体 | v3.0 半官方模式 / 官方模式 | 用于 Claude 原生 Computer Use 工作流 |
| Bearer Token + Messages API 半官方模式 | v3.0 | 面向中转站用户的兼容设计 |
| OpenAI-compatible chat/completions 兼容模式 | v1 / v2 起 | 面向普通中转站和其它视觉 + tool calling 模型 |
| 双模型顾问策略 | v3.0 | 主模型失败时使用顾问模型修正 |
| 长对话 compact 压缩 | v3.0 | 降低长任务 token 压力 |
| 内置 Skill：file_read / file_write / shell | v3.0 | 允许模型在授权范围内读写文件和执行命令 |
| 独立 MCP Server | v3.0 | 供外部 agent 通过 stdio 调用本地桌面能力 |
| 可视化反馈层：呼吸灯 / 点击波纹 / HUD | v3.2 | 让本地执行过程不再是黑盒 |
| 截图穿帮免疫 | v3.2 | 尽量避免 HUD / overlay 被 agent 截图看到 |
| HUD 可拉伸 / 位置保存 | v3.2.1 | 改善长任务中的观察体验 |
| RegionFocus 放大重定位 | v3.2.2 | 点击失败或画面无变化时辅助模型重新判断 |
| 逐动作微验证 | v3.2.2 | 针对点击、输入、滚动、按键给出更具体的失败反馈 |
| HUD 红色中止按钮 | v3.2.2 | 主窗口隐藏后仍可快速停止任务 |

#### 设计取舍总结

1. 本项目的核心目标不是复刻官方 Docker/X11 参考实现，而是在 Windows 本地环境里提供一个可以直接运行的 Computer Use 框架。
2. v3 重点补齐了 Anthropic 新版 Computer Use 协议、中转站半官方模式、固定分辨率、双模型顾问、长对话压缩等能力。
3. v2 起已经存在一批偏工程安全阀的能力，例如越界拦截、前台窗口校验、遮挡检测、截图变化检测、DOM 辅助和 replay 复盘。
4. v3.2.2 中的 RegionFocus、逐动作微验证、失败恢复提示等设计，与 RegionFocus、VLAA-GUI、HyperClick 等公开研究方向存在思路上的对应关系，但这里仅表示工程实现上的参考和相似，不宣称完全等同。
5. 可视化反馈层，包括呼吸灯、HUD、点击波纹、截图穿帮免疫等，是本项目为 Windows 本地 Computer Use 场景做的工程化设计。后续如果发现已有公开项目或研究覆盖相同能力，可以继续补充引用和说明。

#### 参考资料

- Anthropic Computer Use Tool 官方文档：`https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool`
- RegionFocus / Visual Test-time Scaling for GUI Agent Grounding：`https://arxiv.org/abs/2505.00684`
- VLAA-GUI: Knowing When to Stop, Recover, and Search：`https://arxiv.org/abs/2604.21375`
- HyperClick: Advancing Reliable GUI Grounding via Uncertainty Calibration：`https://arxiv.org/abs/2510.27266`

> 以上对照基于当前可检索的公开资料整理，主要用于说明本项目设计思路和相关公开方向之间的关系。如发现已有研究或项目覆盖相同能力，欢迎提交 issue 补充或修正。
