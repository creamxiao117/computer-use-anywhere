# Contributing to Computer Use Anywhere

感谢贡献!这是一个 Windows 本地版 computer-use 框架,目标是让任何支持视觉 + tool calling 的模型(Claude / GPT / Gemini / 国产模型 / 中转站)都能直接控制 Windows 桌面。

## 开始之前

- Python 3.11+,Windows 10 Build 19041(2004)及以上
- 依赖只有 `Pillow` 和 `numpy`(见 `pyproject.toml`),不引入大型框架
- **绝不提交** `settings.json`、`sessions/`、API key、中转站地址、个人截图等隐私数据 — `.gitignore` 已经覆盖,提交前再扫一眼

## 本地跑起来

```powershell
git clone <your-fork>
cd computer-use-anywhere
python -m pip install -e .
python -m pip install pyinstaller  # 仅打 EXE 时需要

# 跑 UI
python run.py

# 跑测试
python -m pytest tests/ -v

# 打 EXE
powershell -ExecutionPolicy Bypass -File scripts/build_exe.ps1 -Zip
```

第一次运行 UI 时,程序不会自动创建 `settings.json` — 在 UI 里填好后会自动保存。或者参考 `settings.example.json` 手动建一份。

## 代码风格

- Python 3.11+ 语法(`from __future__ import annotations` + PEP 604 union 风格)
- 不引入额外格式化工具,沿用现有缩进/换行风格
- 注释只解释 **why**,不解释 **what**(命名应当自明)
- 中文注释 / 中文 UI 文案 OK,代码标识符全英文

## 提交规范

- commit message 用简体中文或英文都行,主题 < 50 字符,描述 "为什么"
- 一个 PR 一件事
- 改了 `provider.py` / `agent.py` / `windows_control.py` 这类核心文件请尽量带测试

## 不接受的 PR

- 把硬编码的中转站地址、API key、个人配置塞进代码或 README
- 把 `settings.json` 反向加进追踪
- 给某个特定中转站(包括官方 anthropic.com)绑死的特殊处理 — 必须保持"接口可替换"
- 没说明动机的纯重构

## 报 bug

issue 里请带:
- 操作系统版本(`winver`)
- Python 版本
- 截图 / 错误码 / 完整堆栈(脱敏后)
- 复现步骤
- 用的哪种运行模式(兼容 / 官方体验 / 半官方 / 官方)

## 安全

发现安全问题(比如可以让模型逃逸出沙盒做危险操作的路径),请优先用 GitHub Security Advisory 报告,不要直接开 public issue。
