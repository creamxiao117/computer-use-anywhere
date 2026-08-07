from __future__ import annotations

import contextlib
import json
import logging
import os
import shlex
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


def _resolve_sandbox_root() -> Path:
    """稳定的沙箱根锚点，不随进程启动目录（cwd）漂移。

    优先级：CUA_SANDBOX_ROOT > CUA_FILE_SANDBOX_ROOT（历史名，保留兼容）
    > %LOCALAPPDATA%/computer-use-anywhere/sandbox > ~/computer-use-anywhere/sandbox。
    """
    override = os.environ.get("CUA_SANDBOX_ROOT") or os.environ.get("CUA_FILE_SANDBOX_ROOT")
    if override:
        return Path(override).resolve()
    local_appdata = os.environ.get("LOCALAPPDATA") or ""
    anchor = Path(local_appdata) if local_appdata else Path.home()
    return (anchor / "computer-use-anywhere" / "sandbox").resolve()


# 文件读写与 shell 执行强制限制在此沙箱根目录内；可通过环境变量覆盖（必须为绝对路径）。
_SANDBOX_ROOT = _resolve_sandbox_root()


def _ensure_sandbox_root() -> Path:
    """确保沙箱根存在（subprocess 的 cwd 必须真实存在，否则 Popen 直接失败）。"""
    try:
        os.makedirs(_SANDBOX_ROOT, exist_ok=True)
    except Exception:  # 目录不可创建时不应阻断模块导入
        logger.warning("sandbox root not creatable: %s", _SANDBOX_ROOT, exc_info=True)
    return _SANDBOX_ROOT


_ensure_sandbox_root()

# 传给子进程的环境变量白名单（顺序即优先级，用于大小写去重）。
# 只复制运行时必需的系统变量；任何密钥类变量（*_API_KEY / TOKEN / SECRET ...）
# 一律不透传 —— 全量继承 os.environ 会把 ANTHROPIC_API_KEY 等泄露给被执行命令。
_SAFE_ENV_KEYS: tuple[str, ...] = (
    "PATH", "PATHEXT", "ComSpec", "OS", "LANG", "LC_ALL",
    "SYSTEMROOT", "SystemRoot", "SystemDrive", "WINDIR",
    "TEMP", "TMP", "TMPDIR", "LOCALAPPDATA",
    "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_IDENTIFIER", "PROCESSOR_LEVEL", "PROCESSOR_REVISION",
)

# 超时后回收进程树的宽限秒数。
_KILL_GRACE_SECONDS = 5

# 聚簇附着式选项体（如 -flinky.txt 的 "flinky.txt"）的最大长度：超过即视为非真实
# 用法直接拒绝，避免逐后缀校验退化成 O(n) 次路径 resolve。
_MAX_ATTACHED_OPTION_BODY = 256

# 仅允许这些可执行名运行；其余命令一律拒绝（最小权限 + 白名单）。
_SHELL_ALLOWLIST = frozenset(
    {
        "ls", "cat", "echo", "pwd", "date", "whoami", "dir",
        "python", "python3", "pip", "git", "node", "npm", "npx",
        "find", "grep", "head", "tail", "wc", "sort", "uniq", "type",
        "tasklist", "ipconfig", "ping", "curl", "wget",
    }
)

# 需要人工确认的危险命令（即便在白名单内，也要求 confirmed=True）。
_SHELL_CONFIRM_REQUIRED = frozenset(
    {
        "git", "pip", "python", "python3", "node", "npm", "npx", "curl", "wget",
    }
)

# shell 元字符/操作符：出现即视为试图绕过，拒绝执行（纵深防御）。
_SHELL_METACHARACTERS = frozenset(";&|`$><\n(){}*?~!")


SkillHandler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(slots=True)
class SkillDefinition:
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    handler: SkillHandler | None = None
    mcp_server_command: list[str] | None = None
    enabled: bool = True


class SkillRegistry:
    """Registry for skills and MCP-based tools."""

    def __init__(self) -> None:
        self._skills: dict[str, SkillDefinition] = {}
        self._mcp_processes: dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()

    def register(self, skill: SkillDefinition) -> None:
        with self._lock:
            self._skills[skill.name] = skill

    def unregister(self, name: str) -> None:
        with self._lock:
            self._skills.pop(name, None)

    def list_skills(self) -> list[SkillDefinition]:
        with self._lock:
            return [s for s in self._skills.values() if s.enabled]

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        schemas: list[dict[str, Any]] = []
        for skill in self.list_skills():
            schema = {
                "type": "function",
                "function": {
                    "name": skill.name,
                    "description": skill.description,
                    "parameters": skill.parameters,
                },
            }
            schemas.append(schema)
        return schemas

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            skill = self._skills.get(name)
        if skill is None:
            return {"error": f"Skill '{name}' not found."}
        if skill.handler is not None:
            try:
                return skill.handler(arguments)
            except Exception as exc:
                return {"error": f"{type(exc).__name__}: {exc}"}
        if skill.mcp_server_command is not None:
            return self._execute_mcp(skill, arguments)
        return {"error": f"Skill '{name}' has no handler or MCP command."}

    def _execute_mcp(self, skill: SkillDefinition, arguments: dict[str, Any]) -> dict[str, Any]:
        proc = self._mcp_processes.get(skill.name)
        if proc is None or proc.poll() is not None:
            try:
                proc = subprocess.Popen(
                    skill.mcp_server_command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                self._mcp_processes[skill.name] = proc
                # Initialize
                init_req = (
                    json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}})
                    + "\n"
                )
                proc.stdin.write(init_req)  # type: ignore[union-attr]
                proc.stdin.flush()  # type: ignore[union-attr]
                # Read init response
                line = proc.stdout.readline()  # type: ignore[union-attr]
            except Exception as exc:
                return {"error": f"Failed to start MCP server for {skill.name}: {exc}"}
        try:
            request = {
                "jsonrpc": "2.0",
                "id": int(time.time() * 1000),
                "method": "tools/call",
                "params": {"name": skill.name, "arguments": arguments},
            }
            proc.stdin.write(json.dumps(request) + "\n")  # type: ignore[union-attr]
            proc.stdin.flush()  # type: ignore[union-attr]
            line = proc.stdout.readline()  # type: ignore[union-attr]
            response = json.loads(line)
            if "error" in response:
                return {"error": response["error"].get("message", "MCP error")}
            return {"result": response.get("result", {})}
        except Exception as exc:
            return {"error": f"MCP communication error: {exc}"}

    def shutdown(self) -> None:
        for proc in self._mcp_processes.values():
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                pass


def built_in_skills() -> SkillRegistry:
    """Return a registry with built-in skills pre-registered."""
    registry = SkillRegistry()
    # Example: file read/write skill (placeholder implementation)
    registry.register(
        SkillDefinition(
            name="file_read",
            description="Read a text file from the local filesystem.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute file path."},
                },
                "required": ["path"],
            },
            handler=lambda args: {"content": _read_file(args.get("path", ""))},
        )
    )
    registry.register(
        SkillDefinition(
            name="file_write",
            description="Write text to a file on the local filesystem.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            handler=lambda args: _write_file(args.get("path", ""), args.get("content", "")),
        )
    )
    registry.register(
        SkillDefinition(
            name="shell",
            description=(
                "Run a shell command and return stdout/stderr. Restricted to an "
                "allowlist; destructive/remote commands require confirmed=true "
                "(explicit human confirmation)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer", "default": 30},
                    "confirmed": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Set true ONLY after explicit human confirmation; "
                            "required for destructive/remote commands."
                        ),
                    },
                },
                "required": ["command"],
            },
            handler=lambda args: _run_shell(
                args.get("command", ""),
                args.get("timeout", 30),
                args.get("confirmed", False),
            ),
        )
    )
    return registry


def _safe_subprocess_env() -> dict[str, str]:
    """构造最小环境变量集合；绝不把密钥类变量透传给子进程（G2）。"""
    env: dict[str, str] = {}
    seen: set[str] = set()
    for key in _SAFE_ENV_KEYS:
        # Windows 环境变量大小写不敏感，SYSTEMROOT/SystemRoot 只保留先出现的那个。
        norm = key.upper() if os.name == "nt" else key
        if norm in seen:
            continue
        value = os.environ.get(key)
        if value is None:
            continue
        seen.add(norm)
        env[key] = value
    if "PATH" not in env:
        env["PATH"] = os.defpath
    return env


def _is_within_sandbox(resolved: Path) -> bool:
    """边界安全的包含判断：/sandbox 不应匹配 /sandbox_evil。

    用 normcase 归一化（Windows 下大小写不敏感且 / -> \\），比 Path.relative_to
    更贴合真实文件系统语义；任何异常都判定为"不在沙箱内"（fail-closed）。
    """
    try:
        root = os.path.normcase(os.path.abspath(str(_SANDBOX_ROOT)))
        cand = os.path.normcase(os.path.abspath(str(resolved)))
    except Exception:  # 无法判定即视为越界
        return False
    return cand == root or cand.startswith(root.rstrip(os.sep) + os.sep)


def _safe_path(path: str) -> Path:
    """将 path 约束在沙箱根目录内，阻止路径穿越（例如 ../../etc/passwd）。

    用 os.path.join(沙箱根, path) 而非 pathlib 的 `/` 运算符：前者精确复刻操作系统
    解析语义（Windows 下 '/etc/x' 是"当前盘根相对"、'C:x' 是"盘符相对"），与子进程
    在 cwd=沙箱根 下的真实解析结果一致，避免校验器与子进程之间出现解析差异。
    """
    resolved = Path(os.path.join(str(_SANDBOX_ROOT), path)).resolve()
    if not _is_within_sandbox(resolved):
        raise ValueError(f"path '{path}' escapes sandbox root '{_SANDBOX_ROOT}'; refused")
    return resolved


def _first_path_indicator(s: str) -> int | None:
    """返回 s 中第一个"路径指示符"的下标；没有则返回 None。

    路径指示符 = `/`、`\\`、`~`，或以 `./`、`..` 开头的 `.`，或盘符根（如 `C:`）。
    选项字母之后的第一个路径指示符即"附着式选项值"的起点。
    """
    for i, ch in enumerate(s):
        if ch in ("/", "\\", "~"):
            return i
        if ch == "." and (i + 1 >= len(s) or s[i + 1] in "/\\" or s[i : i + 2] == ".."):
            return i
        if ch.isalpha() and i + 1 < len(s) and s[i + 1] == ":":
            return i  # 盘符根，例如 C:
    return None


def _is_bare_filename(s: str) -> bool:
    """选项体后缀能否作为一个相对 cwd 打开的单段文件名（P1 第三变体后缀扫描用）。

    程序真正打开的文件名必然是选项体的某个后缀；但只有"可能是真实文件名"的后缀才
    送 `_safe_path` 校验，其余（多段路径 / 盘符相对）要么已由附着值分支覆盖，要么
    根本不是文件名、校验只会误杀。

    关键不变量：合法文件名**可以包含 `..` 与 `~`**（`a..b.txt`、`a~b.txt` 在
    Windows/Linux 都是合法文件名，只有单独的 `..`/`~` 成分才有导航含义）。因此这里
    **不能**用 `_first_path_indicator` 过滤——那会把"文件名里含 `..`"的情况一并豁免，
    使攻击者把软链命名为 `a..b.txt` 即可绕过整个后缀扫描（即第三变体逃逸）。真正需要
    排除的只是含冒号 `:` 的**盘符相对**片段（如 `t:%H`）：它既不是文件名，又是
    `--pretty=format:%H` 这类真实用法的后缀，扫描它只会误杀。
    """
    if s in (".", ".."):
        return False  # 纯导航成分，不是文件名
    if "/" in s or "\\" in s:
        return False  # 多段路径，已由附着值分支覆盖
    return ":" not in s  # 盘符相对（t:%H）不是文件名


def _attached_path_value(flag_arg: str) -> str | None:
    """从 -f/path 这类"选项字母+附着值"中提取可能逃逸的路径值（Task #8）。

    例：`-f/etc/passwd` 的 `-f` 是选项字母、`/etc/passwd` 是附着值；`-IC:/x`、
    `-o../escape` 同理。若参数整体是纯选项（如 `-la`、`--color`、`-rf`）则无可提取
    的路径值，返回 None，交由调用方跳过。以失败封闭为准：拿不准的一律返回子串，
    交给 `_safe_path` 判定。
    """
    body = flag_arg[1:]  # 去掉首个 '-'
    if body.startswith("-"):
        body = body[1:]  # 去掉第二个 '-'（--long 形式）
    if not body:
        return None
    idx = _first_path_indicator(body)
    if idx is None:
        return None
    return body[idx:]


def _reject_escaping_path_args(argv: list[str]) -> str | None:
    """校验命令参数中的路径不逃逸沙箱；返回错误串表示拒绝，None 表示放行（G1）。

    argv[0]（可执行名）不校验：解释器/工具本身本就在沙箱外，由白名单负责。

    重要：这里对**每一个非选项 token** 都做校验，绝不按"看起来像不像路径"预筛。
    曾经的预筛（只校验绝对/根相对/含 `..` 的参数）会放过沙箱内指向沙箱外的
    符号链接/junction —— 例如攻击者先用 `python` 在沙箱内埋一个 `escape_link`
    junction，之后用免确认的 `cat escape_link/secret` 即可读取全盘任意文件。
    `_safe_path()` 内部的 `.resolve()` 会跟穿链接，从而把这类逃逸挡在门外。

    非路径 token 无害：`echo hello` 的 `hello` 解析为 <沙箱>/hello，仍在沙箱内，放行。

    附着式选项值（`-f/path`、`-IC:/x`、`-o../escape`）同样校验（Task #8）：剥离前导
    `-` 后，从第一个路径指示符起视为路径值。

    附着值**不含**路径指示符时（`-la`、`-rf`、`-flinky.txt`）也不能跳过（P1）：
    `grep -flinky.txt` 真正打开的是 `linky.txt`，它可以是攻击者先埋在沙箱内、指向
    沙箱外的符号链接 —— 跳过即逃逸。聚簇短选项无法静态确定吃掉了几个选项字母
    （`-f<值>`、`-rf<值>`、`--flag<值>` 都合法），故把选项体的**每个后缀**都当作候选
    路径送 `_safe_path`（fail-closed）：纯选项的各后缀都解析在沙箱内被放行，只有真实
    存在且被 `.resolve()` 跟穿到沙箱外的符号链接/junction 会被拒。

    `=` 值与后缀扫描必须取**并集**而非互斥：`=` 未必是 `--key=value` 分隔符，也可能
    只是文件名的一个字符。`grep -feq=link.txt` 打开的是 `eq=link.txt`，在第一个 `=`
    处截断只会校验 `link.txt`（沙箱内不存在的扁平名，判在内）而放过真正的软链；
    `grep -ftrail.txt=` 的 `=` 后为空更会整条跳过。两者都靠后缀扫描兜住。

    后缀候选必须是"可能是真实文件名"的片段（判定见 `_is_bare_filename`）。注意 `..`
    与 `~` 是**合法文件名字符**，不能排除——`a..b.txt`/`a~b.txt` 可作软链名，若按路径
    指示符过滤就会把它们整个豁免出扫描、复活 P1 逃逸（第三变体）。真正要排除的只是含
    冒号 `:` 的盘符相对片段（如 `t:%H`）：它既非文件名、又是 `--pretty=format:%H`
    的真实后缀，扫描只会误杀 git 等常用调用。多段路径（`/etc/passwd`）已由附着值分支
    覆盖，也不重复入选。
    """
    for arg in argv[1:]:
        candidates = [arg]
        if arg.startswith("-"):
            body = arg[1:]
            if body.startswith("-"):
                body = body[1:]  # --long 形式
            if not body:
                continue  # 纯 "-" 或 "--"
            if len(body) > _MAX_ATTACHED_OPTION_BODY:
                # 长度封顶必须在 '=' 判断之前，否则超长参数可借 '=' 绕过后缀扫描。
                # 超长选项体不是真实用法；直接拒绝，避免为它做 O(n) 次 resolve
                # （每次约 0.1ms）而被拖成 CPU 消耗点。
                return f"argument '{arg}' is not a resolvable path; refused"
            candidates = []
            if "=" in arg:
                value = arg.split("=", 1)[1]  # --file=/etc/passwd
                if value:
                    candidates.append(value)
            else:
                # 附着式选项值：-f/path、-IC:/x、-o../escape。
                attached = _attached_path_value(arg)
                if attached is not None:
                    candidates.append(attached)
            # 无论有无 '='，都补扫选项体的裸后缀：程序真正打开的文件名必然是
            # body 的某个后缀（-flinky.txt / -rflinky.txt / -feq=link.txt）。
            candidates += [
                body[i:] for i in range(len(body)) if _is_bare_filename(body[i:])
            ]
            if not candidates:
                continue
        for candidate in candidates:
            try:
                _safe_path(candidate)
            except ValueError:
                return f"argument '{arg}' resolves outside sandbox root '{_SANDBOX_ROOT}'; refused"
            except Exception:  # 无法解析的路径同样拒绝（fail-closed）
                return f"argument '{arg}' is not a resolvable path; refused"
    return None


def _kill_process_tree(proc: subprocess.Popen[str]) -> None:
    """尽力杀掉 proc 及其全部子孙进程；绝不向外抛异常。

    NOTE(P0 临时方案): taskkill /T 与 killpg 都不是确定性的树杀 —— 子进程可能
    已脱离进程组，taskkill 也可能因权限不足或 PID 复用而失败。P1 将改用
    Windows Job Object (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE) / POSIX cgroup
    来保证确定性回收，届时本函数可整体删除。
    """
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            # 用绝对路径调用 taskkill，避免 PATH 劫持。
            system_root = os.environ.get("SYSTEMROOT") or r"C:\Windows"
            taskkill = os.path.join(system_root, "System32", "taskkill.exe")
            subprocess.run(  # noqa: S603 # 固定 argv，无外部输入，shell=False
                [taskkill, "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=_KILL_GRACE_SECONDS,
                check=False,
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:  # 回收失败不得影响调用方
        logger.warning("shell.kill-tree failed pid=%s", proc.pid, exc_info=True)
    finally:
        with contextlib.suppress(Exception):
            proc.kill()  # 兜底：至少确保直接子进程已终止


def _read_file(path: str) -> str:
    try:
        safe = _safe_path(path)
    except ValueError as exc:
        return f"Error reading file: {exc}"
    try:
        with open(safe, encoding="utf-8") as f:
            return f.read()
    except Exception as exc:
        return f"Error reading file: {exc}"


def _write_file(path: str, content: str) -> dict[str, Any]:
    try:
        safe = _safe_path(path)
    except ValueError as exc:
        return {"error": str(exc)}
    try:
        safe.parent.mkdir(parents=True, exist_ok=True)
        with open(safe, "w", encoding="utf-8") as f:
            f.write(content)
        return {"message": f"Wrote {len(content)} chars to {safe}."}
    except Exception as exc:
        return {"error": str(exc)}


def _run_shell(command: str, timeout: int, confirmed: bool = False) -> dict[str, Any]:
    logger.info("shell.request command=%r confirmed=%s", command, confirmed)
    if not command.strip():
        return {"error": "empty command"}
    # 1) 拒绝任何 shell 元字符（纵深防御；shell=False 仍保留这层）
    if any(ch in _SHELL_METACHARACTERS for ch in command):
        logger.warning("shell.rejected metachar command=%r", command)
        return {"error": "command contains shell metacharacters; refused for safety"}
    # 2) 解析为参数列表，杜绝 shell 注入（绝不启用 shell=True）
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return {"error": f"cannot parse command: {exc}"}
    if not argv:
        return {"error": "empty command"}
    # 3) 白名单校验可执行名
    executable = argv[0]
    base = executable.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()
    if base not in _SHELL_ALLOWLIST:
        logger.warning("shell.rejected not-allowed executable=%r", executable)
        return {"error": f"command '{base}' is not in the allowlist; refused"}
    # 4) 危险命令需显式人工确认（confirmed=True）
    if base in _SHELL_CONFIRM_REQUIRED and not confirmed:
        logger.warning("shell.needs-confirmation executable=%r", executable)
        return {
            "error": f"command '{base}' requires explicit confirmation "
            f"(pass confirmed=true); refused"
        }
    # 5) 参数中的路径不得逃逸沙箱（G1）
    path_error = _reject_escaping_path_args(argv)
    if path_error is not None:
        logger.warning("shell.rejected path-escape command=%r", command)
        return {"error": path_error}
    # 6) 执行（shell=False，无 shell 解释；cwd 锁沙箱、env 最小化、超时杀进程树）
    sandbox_root = _ensure_sandbox_root()
    popen_kwargs: dict[str, Any] = {
        "cwd": str(sandbox_root),
        "env": _safe_subprocess_env(),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        # 非 UTF-8 代码页（如中文 Windows 的 936/GBK）下，命令输出可能含当前编码无法
        # 解码的字节；不设 errors 会让 subprocess 的 reader 线程抛 UnicodeDecodeError，
        # communicate() 返回 None，随后 stdout[:4000] 报 TypeError。ipconfig/tasklist
        # 这类白名单内的免确认命令会因此直接不可用。
        "errors": "replace",
        "shell": False,
    }
    if os.name != "nt":
        # 独立进程组，超时后才能用 killpg 回收整棵树。
        popen_kwargs["start_new_session"] = True
    proc: subprocess.Popen[str] | None = None
    try:
        proc = subprocess.Popen(argv, **popen_kwargs)  # noqa: S603 # argv 白名单 + shlex.split
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            _kill_process_tree(proc)
            with contextlib.suppress(Exception):
                proc.communicate(timeout=_KILL_GRACE_SECONDS)  # 排空管道，避免 fd 泄漏
            logger.warning("shell.timeout command=%r timeout=%s", command, timeout)
            return {"error": str(exc)}
        logger.info("shell.done rc=%s", proc.returncode)
        return {
            "returncode": proc.returncode,
            "stdout": stdout[:4000],
            "stderr": stderr[:2000],
        }
    except Exception as exc:
        return {"error": str(exc)}
    finally:
        if proc is not None:
            for stream in (proc.stdout, proc.stderr, proc.stdin):
                with contextlib.suppress(Exception):
                    if stream is not None and not stream.closed:
                        stream.close()
