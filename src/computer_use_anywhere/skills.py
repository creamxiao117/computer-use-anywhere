from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# 文件读写强制限制在此沙箱根目录内；可通过环境变量覆盖（必须为绝对路径）。
_ENV_ROOT = os.environ.get("CUA_FILE_SANDBOX_ROOT", "")
_SANDBOX_ROOT = Path(_ENV_ROOT).resolve() if _ENV_ROOT else (Path.cwd() / ".cua_sandbox")

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


def _safe_path(path: str) -> Path:
    """将 path 约束在沙箱根目录内，阻止路径穿越（例如 ../../etc/passwd）。"""
    raw = Path(path)
    candidate = raw if raw.is_absolute() else (_SANDBOX_ROOT / raw)
    resolved = candidate.resolve()
    try:
        resolved.relative_to(_SANDBOX_ROOT)
    except ValueError as exc:
        raise ValueError(
            f"path '{path}' escapes sandbox root '{_SANDBOX_ROOT}'; refused"
        ) from exc
    return resolved


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
    # 5) 执行（shell=False，无 shell 解释）
    try:
        proc = subprocess.run(  # noqa: S603  # argv allowlisted + shlex.split, shell=False
            argv, shell=False, capture_output=True, text=True, timeout=timeout
        )
        logger.info("shell.done rc=%s", proc.returncode)
        return {
            "returncode": proc.returncode,
            "stdout": proc.stdout[:4000],
            "stderr": proc.stderr[:2000],
        }
    except Exception as exc:
        return {"error": str(exc)}
