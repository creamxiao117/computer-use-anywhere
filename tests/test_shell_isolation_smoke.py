"""shell OS 级隔离 烟雾回归（pytest 入口）。

设计要点
--------
`skills` 模块的沙箱根在**导入时**解析并落盘为模块级常量；`test_skills_sandbox_p0.py`
已在 module 级设置 CUA_SANDBOX_ROOT 并 import 同一模块。为避免同进程内两个测试
互相踩沙箱根（命中模块缓存导致路径判定错位），本文件的真实烟雾逻辑在**独立子进程**
中运行（子进程内 import 是全新解释器，无缓存冲突），pytest 仅断言子进程退出码。

逻辑移植自项目根 `tools/smoke_shell_isolation.py`，已搬入 cua_src 仓库内，使其自包含、
可被 fork 的 CI 直接 `pytest tests/` 跑。

运行：
    cd cua_src && python -m pytest tests/test_shell_isolation_smoke.py -v
或直接独立运行（与 pytest 解耦）：
    python tests/test_shell_isolation_smoke.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_THIS = Path(__file__).resolve()


def test_shell_isolation_smoke():
    """真实软链 + 真实守卫逻辑：8 类逃逸全部被拒、6 类合法命令零误杀、端到端零泄露。"""
    import pytest  # 延迟导入：独立运行（__main__）时不依赖 pytest

    proc = subprocess.run(
        [sys.executable, str(_THIS)],
        capture_output=True,
        text=True,
    )
    print(proc.stdout)
    if proc.returncode == 2:
        # 环境无法实质验证（Windows 符号链接权限/开发者模式未开，软链建不了）
        pytest.skip("环境无法建软链（Windows 符号链接权限/开发者模式未开），烟雾验证跳过")
    if proc.returncode != 0:
        print(proc.stderr)
        assert proc.returncode == 0, f"shell 隔离烟雾测试失败，退出码 {proc.returncode}"


if __name__ == "__main__":
    # ---- 独立运行时的真实烟雾逻辑（移植自 tools/smoke_shell_isolation.py）----
    import shutil
    import tempfile

    # 本文件位于 cua_src/tests/，parents[1] 即 cua_src 仓根，其下 src 为包根
    CUA_SRC_SRC = _THIS.parents[1] / "src"
    if not CUA_SRC_SRC.exists():
        print(f"[FATAL] 找不到 cua_src/src: {CUA_SRC_SRC}")
        sys.exit(2)

    # 自建临时沙箱，import skills 前通过环境变量重定向守卫用的沙箱根
    _tmp = tempfile.mkdtemp(prefix="cua_smoke_")
    SANDBOX = Path(_tmp) / "sandbox"
    OUTSIDE = Path(_tmp) / "outside"
    SANDBOX.mkdir(parents=True, exist_ok=True)
    OUTSIDE.mkdir(parents=True, exist_ok=True)
    os.environ["CUA_SANDBOX_ROOT"] = str(SANDBOX)

    sys.path.insert(0, str(CUA_SRC_SRC))
    import computer_use_anywhere.skills as skills  # noqa: E402

    CANARY = "EXFIL-LINE-B"
    (OUTSIDE / "secret.txt").write_text(CANARY + "\n")        # 沙箱外机密
    (SANDBOX / "normal.txt").write_text("hello\n")             # 沙箱内正常文件
    (SANDBOX / "dict.txt").write_text("alpha\nbeta\ngamma\n")  # grep 目标

    # (命令行 argv, 需在沙箱内创建的软链名)
    ESCAPE_CASES = [
        (["grep", "-flinky.txt", "dict.txt"], "flinky.txt"),
        (["grep", "-rflinky.txt", "dict.txt"], "flinky.txt"),
        (["grep", "-feq=link.txt", "dict.txt"], "eq=link.txt"),
        (["grep", "-ftrail.txt=", "dict.txt"], "trail.txt="),
        (["grep", "-fa..b.txt", "dict.txt"], "a..b.txt"),
        (["grep", "-fx..y", "dict.txt"], "x..y"),
        (["grep", "-fv1.2..3", "dict.txt"], "v1.2..3"),
        (["grep", "-fa~b.txt", "dict.txt"], "a~b.txt"),
    ]

    # 合法命令（必须放行，零误杀）
    ALLOW_CASES = [
        ["git", "log", "--pretty=format:%H", "-1"],
        ["grep", "--max-count=5", "alpha", "dict.txt"],
        ["ls", "-la"],
        ["head", "-n", "2", "dict.txt"],
        ["cat", "normal.txt"],
        ["wc", "-l", "dict.txt"],
    ]

    passed = skipped = failed = escape_pass = 0
    results: list[tuple[str, str, str]] = []

    def make_link(name: str) -> bool:
        """在沙箱内建名为 name、指向沙箱外机密的软链。成功 True，环境受限 False。"""
        link = SANDBOX / name
        try:
            if link.is_symlink() or link.exists():
                link.unlink()
            os.symlink(OUTSIDE / "secret.txt", link)
            return True
        except OSError:
            return False

    # 主测试：守卫逻辑（确定性、不依赖外部 grep）
    for argv, link in ESCAPE_CASES:
        if not make_link(link):
            results.append(("SKIP", " ".join(argv), "软链创建失败（Windows 符号链接权限/开发者模式未开）"))
            skipped += 1
            continue
        err = skills._reject_escaping_path_args(argv)
        if err is not None:
            passed += 1
            escape_pass += 1
            results.append(("PASS", " ".join(argv), f"rejected={err!r}"))
        else:
            failed += 1
            results.append(("FAIL", " ".join(argv), "err=None (escaped!)"))

    for argv in ALLOW_CASES:
        err = skills._reject_escaping_path_args(argv)
        if err is None:
            passed += 1
            results.append(("PASS", " ".join(argv), "allowed"))
        else:
            failed += 1
            results.append(("FAIL", " ".join(argv), f"err={err!r} (false kill)"))

    # 端到端加分项：探测 grep 后真实执行
    grep_path = shutil.which("grep")
    if grep_path:
        for argv, link in ESCAPE_CASES:
            if not make_link(link):
                continue
            res = skills._run_shell(" ".join(argv), 20)
            leaked = CANARY in (res.get("stdout") or "") or CANARY in (res.get("stderr") or "")
            if "error" in res and not leaked:
                passed += 1
                results.append(("PASS(e2e)", " ".join(argv), "blocked + no-leak"))
            else:
                failed += 1
                results.append(("FAIL(e2e)", " ".join(argv), f"leaked={leaked} res={res}"))
    else:
        results.append(("INFO", "end-to-end", "PATH 未探测到 grep，跳过端到端（守卫逻辑已由主测试覆盖）"))

    # 汇总
    print("=" * 64)
    print("cua_src shell 隔离 一键烟雾测试")
    print(f"  守卫模块 : {skills.__file__}")
    print(f"  沙箱根   : {SANDBOX}")
    print(f"  PASS={passed}  FAIL={failed}  SKIP={skipped}")
    print("=" * 64)
    for status, label, note in results:
        print(f"  [{status}] {label}\n           -- {note}")

    shutil.rmtree(_tmp, ignore_errors=True)

    if failed:
        print("\n结论：存在失败用例 —— 修复可能未生效或已回归，请检查。")
        sys.exit(1)
    if escape_pass == 0:
        print("\n结论：逃逸用例全部被环境跳过（无法建软链），未能实质验证。")
        print("       请在开启『开发者模式』或管理员/特权下运行，或在 WSL / Git Bash 内执行。")
        sys.exit(2)
    print("\n结论：全部通过，shell OS 级隔离修复在运行态生效。")
    sys.exit(0)
