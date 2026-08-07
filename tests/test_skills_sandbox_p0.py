"""P0 冒烟/回归：shell skill 的沙箱 cwd、最小 env、稳定根锚点、超时杀树（G1-G4）。

注意：本文件必须在导入 computer_use_anywhere.skills 之前设置 CUA_SANDBOX_ROOT，
因为沙箱根是在模块加载时解析并落盘的（G3 的稳定锚点语义）。
"""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

_SANDBOX = tempfile.mkdtemp(prefix="cua_p0_")
os.environ["CUA_SANDBOX_ROOT"] = _SANDBOX
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from computer_use_anywhere import skills as sk  # noqa: E402


def _write(name: str, body: str) -> str:
    p = os.path.join(_SANDBOX, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(body)
    return p


# --------------------------------------------------------------------------
# 不变式：白名单与 confirmed 门禁不得因本次改动而漂移
# --------------------------------------------------------------------------
def test_allowlist_invariants():
    assert len(sk._SHELL_ALLOWLIST) == 27
    assert len(sk._SHELL_CONFIRM_REQUIRED) == 9
    assert sk._SHELL_CONFIRM_REQUIRED <= sk._SHELL_ALLOWLIST


def test_existing_gates_preserved():
    assert "not in the allowlist" in sk._run_shell("rm -rf /", 5)["error"]
    assert "requires explicit confirmation" in sk._run_shell("git status", 5)["error"]
    assert "metacharacters" in sk._run_shell("echo a | cat", 5)["error"]
    assert sk._run_shell("   ", 5)["error"] == "empty command"


# --------------------------------------------------------------------------
# G3 稳定沙箱根锚点
# --------------------------------------------------------------------------
def test_sandbox_root_is_stable_and_exists():
    assert os.path.normcase(str(sk._SANDBOX_ROOT)) == os.path.normcase(os.path.realpath(_SANDBOX))
    assert os.path.isdir(sk._SANDBOX_ROOT)


def test_sandbox_root_independent_of_cwd():
    """核心回归：切换进程 cwd 后沙箱根不得漂移（原实现用 Path.cwd() 会漂）。"""
    before = sk._SANDBOX_ROOT
    old = os.getcwd()
    try:
        os.chdir(tempfile.gettempdir())
        assert sk._resolve_sandbox_root() == before
    finally:
        os.chdir(old)


# --------------------------------------------------------------------------
# G1 cwd 锁定 + 路径逃逸拒绝
# --------------------------------------------------------------------------
def test_child_cwd_is_sandbox_root():
    _write("probe_cwd.py", "import os\nprint(os.getcwd())\n")
    r = sk._run_shell("python probe_cwd.py", 30, confirmed=True)
    assert r.get("returncode") == 0, r
    got = os.path.normcase(os.path.realpath(r["stdout"].strip()))
    assert got == os.path.normcase(str(sk._SANDBOX_ROOT))


def test_absolute_path_outside_sandbox_refused():
    outside = "C:/Windows/win.ini" if os.name == "nt" else "/etc/passwd"
    r = sk._run_shell(f"cat {outside}", 10)
    assert "outside sandbox root" in r.get("error", ""), r


def test_root_relative_path_refused():
    """Python 3.13 起 ntpath.isabs('/etc/passwd') 为 False —— 必须仍被拦截。"""
    r = sk._run_shell("cat /etc/passwd", 10)
    assert "outside sandbox root" in r.get("error", ""), r


def test_relative_traversal_refused():
    r = sk._run_shell("cat ../../secrets.txt", 10)
    assert "outside sandbox root" in r.get("error", ""), r


def test_option_value_path_refused():
    r = sk._run_shell("grep --file=/etc/passwd x", 10)
    assert "outside sandbox root" in r.get("error", ""), r


def test_attached_option_value_path_refused():
    """Task #8 回归：附着式选项值（无 '='，如 grep -f/etc/passwd）必须被拒。

    旧逻辑只识别 `--opt=value` 与独立 path token，对 `-f/etc/passwd` 这种
    '以 - 开头但不含 =' 的参数误判为纯选项而跳过，导致路径沙箱校验被绕过（逃逸）。
    修复后 _reject_escaping_path_args 会提取选项字母后的首个路径指示符起算附着值。
    """
    outside = "C:/Windows/win.ini" if os.name == "nt" else "/etc/passwd"
    r = sk._run_shell(f"grep -f{outside} x", 10)
    assert "outside sandbox root" in r.get("error", ""), r


def test_attached_option_value_pure_flags_allowed():
    """反向用例：纯选项不得被路径校验误拒（防过度收紧）。

    直接用守卫函数做确定性断言，避免依赖具体可执行文件是否在最小 env 中。
    """
    assert sk._reject_escaping_path_args(["grep", "-rf", "sub", "dir"]) is None
    assert sk._reject_escaping_path_args(["ls", "-la"]) is None
    assert sk._reject_escaping_path_args(["grep", "-n100", "x"]) is None
    assert sk._reject_escaping_path_args(["find", ".", "-name", "y"]) is None


def test_inside_sandbox_paths_allowed():
    _write("probe_ok.py", "print('ok')\n")
    inside = (Path(_SANDBOX) / "probe_ok.py").as_posix()  # 正斜杠：shlex 会吃掉反斜杠
    r = sk._run_shell(f"python {inside}", 30, confirmed=True)
    assert r.get("returncode") == 0, r
    assert "ok" in r["stdout"]


def test_plain_args_not_misread_as_paths():
    """反向用例：普通非路径 token 不得被路径校验误拒（防收紧过度）。"""
    _write("echo_probe.py", "import sys\nprint(' '.join(sys.argv[1:]))\n")
    for args in ("hello", "1234", "a.b.c", "v1.2.3-rc1", "user@example.com", "hello world"):
        r = sk._run_shell(f"python echo_probe.py {args}", 30, confirmed=True)
        assert r.get("returncode") == 0, (args, r)
        assert "sandbox root" not in str(r.get("error", "")), (args, r)


def test_sandbox_prefix_sibling_is_outside():
    assert not sk._is_within_sandbox(Path(str(sk._SANDBOX_ROOT) + "_evil") / "x")


def _make_link(link: str, target: str, directory: bool) -> bool:
    """尽力创建链接；不支持（无权限/文件系统不支持）时返回 False 以便跳过。"""
    try:
        if directory and os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", link, target], capture_output=True)
        else:
            os.symlink(target, link, target_is_directory=directory)
    except (OSError, NotImplementedError, ValueError):
        return False
    return os.path.exists(link) or os.path.islink(link)


def test_symlink_inside_sandbox_cannot_escape():
    """CRITICAL 回归：沙箱内指向沙箱外的 symlink/junction 必须被拒。

    攻击链真实存在：先用 confirmed 的 `python` 在沙箱内埋一个 junction，
    之后用免确认的 `cat` 即可读取全盘任意文件。校验必须覆盖**所有**非选项 token，
    不能按"看起来像不像路径"预筛，否则 `escape_link/x` 这种普通相对路径会被跳过。
    """
    outside = tempfile.mkdtemp(prefix="cua_p0_out_")
    canary = os.path.join(outside, "CANARY.txt")
    with open(canary, "w", encoding="utf-8") as f:
        f.write("TOP-SECRET-CANARY")

    dirlink = os.path.join(_SANDBOX, "escape_link")
    filelink = os.path.join(_SANDBOX, "file_link")
    made_dir = _make_link(dirlink, outside, directory=True)
    made_file = _make_link(filelink, canary, directory=False)
    if not (made_dir or made_file):
        return  # 环境不支持创建链接，跳过

    probes = []
    if made_dir:
        probes += ["escape_link/CANARY.txt", "./escape_link/CANARY.txt"]
    if made_file:
        probes += ["file_link"]
    for target in probes:
        r = sk._run_shell(f"cat {target}", 15)
        assert "outside sandbox root" in r.get("error", ""), (target, r)
        assert "CANARY" not in r.get("stdout", ""), (target, r)

    # 经由链接执行沙箱外的代码同样必须被拒
    if made_dir:
        with open(os.path.join(outside, "steal.py"), "w", encoding="utf-8") as f:
            f.write("print('EXECUTED-OUTSIDE-SANDBOX')\n")
        r = sk._run_shell("python escape_link/steal.py", 20, confirmed=True)
        assert "outside sandbox root" in r.get("error", ""), r
        assert "EXECUTED-OUTSIDE" not in r.get("stdout", ""), r


@pytest.mark.parametrize(
    ("linkname", "argv"),
    [
        ("linky.txt", ["grep", "-flinky.txt"]),
        ("linky.txt", ["grep", "-rflinky.txt"]),
        ("eq=link.txt", ["grep", "-feq=link.txt"]),
        ("trail.txt=", ["grep", "-ftrail.txt="]),
        # 第三变体：文件名自身含 '..' / '~'（合法文件名字符），不能再按路径指示符豁免
        ("a..b.txt", ["grep", "-fa..b.txt"]),
        ("x..y", ["grep", "-fx..y"]),
        ("v1.2..3", ["grep", "-fv1.2..3"]),
        ("a~b.txt", ["grep", "-fa~b.txt"]),
    ],
)
def test_attached_bare_name_symlink_refused(linkname, argv):
    """P1 回归：**粘连**附着值里的裸相对文件名可能是指向沙箱外的软链。

    `grep -flinky.txt` 与带空格的 `grep -f linky.txt` 不同 —— 后者的 `linky.txt`
    是独立 token 会直接走 `_safe_path`，前者整体以 `-` 开头，一度被当作纯选项跳过。

    `-rflinky.txt`：聚簇短选项无法静态确定吃掉了几个选项字母，只剥 1 个字母不够。
    `-feq=link.txt` / `-ftrail.txt=`：`=` 未必是 `--key=value` 分隔符，也可能只是
    文件名的一个字符；在第一个 `=` 处截断会漏掉真正被打开的 `eq=link.txt`，
    而 `=` 后为空时更会整条跳过。三者同属"附着裸名软链"逃逸。

    第三变体：`a..b.txt` / `x..y` / `v1.2..3` / `a~b.txt` —— 文件名自身含 `..` 或 `~`
    （二者都是**合法文件名字符**，只有单独的 `..`/`~` 成分才是导航），之前按路径指示符
    过滤会把这类后缀整个豁免出扫描，使攻击者把软链改名 `a..b.txt` 即可复活逃逸；后缀
    候选改为"是否为单段文件名"判定后闭合（仅盘符相对的 `t:%H` 这类仍排除，避免误杀）。
    """
    outside = tempfile.mkdtemp(prefix="cua_p1_out_")
    secret = os.path.join(outside, "SECRET.txt")
    with open(secret, "w", encoding="utf-8") as f:
        f.write("TOP-SECRET-CANARY")

    link = os.path.join(_SANDBOX, linkname)
    if not os.path.exists(link) and not _make_link(link, secret, directory=False):
        pytest.skip("environment cannot create symlinks")

    err = sk._reject_escaping_path_args(argv)
    assert err is not None and "outside sandbox root" in err, (argv, err)


def test_attached_bare_name_scan_does_not_reject_real_options():
    """反向用例：逐后缀扫描不得误杀真实选项（防收紧过度）。

    `--pretty=format:%H` 尤其关键 —— 其后缀 `t:%H` 会被 Windows 当成盘符相对路径，
    若不按路径指示符过滤后缀就会被误判越界而拒绝，导致 git 常用调用直接不可用。
    """
    for argv in (
        ["git", "log", "--pretty=format:%H"],
        ["git", "log", "--format=%H%d%s"],
        ["grep", "--max-count=5", "x"],
        ["grep", "--exclude-dir=node_modules", "x"],
        ["grep", "--binary-files=text", "x"],
        ["grep", "--include=a.py", "x"],
        ["node", "--max-old-space-size=4096"],
        ["ls", "-1"],
        ["wc", "-lwc", "f.txt"],
    ):
        assert sk._reject_escaping_path_args(argv) is None, argv


def test_overlong_option_body_refused_even_with_equals():
    """超长选项体直接拒（给逐后缀扫描封顶），且不得借 '=' 绕过长度封顶。"""
    assert sk._reject_escaping_path_args(["grep", "-" + "a" * 300]) is not None
    assert sk._reject_escaping_path_args(["grep", "--x=" + "a" * 300]) is not None
    assert sk._reject_escaping_path_args(["grep", "-" + "a" * 200]) is None


# --------------------------------------------------------------------------
# G2 最小环境变量
# --------------------------------------------------------------------------
def test_secrets_not_leaked_to_child():
    os.environ["ANTHROPIC_API_KEY"] = "sk-must-not-leak"
    os.environ["OPENAI_API_KEY"] = "sk-must-not-leak-2"
    _write(
        "probe_env.py",
        "import os\n"
        "print(os.environ.get('ANTHROPIC_API_KEY', '<ABSENT>'))\n"
        "print(os.environ.get('OPENAI_API_KEY', '<ABSENT>'))\n"
        "print(len(os.environ))\n",
    )
    r = sk._run_shell("python probe_env.py", 30, confirmed=True)
    lines = r.get("stdout", "").splitlines()
    assert lines[0] == "<ABSENT>", r
    assert lines[1] == "<ABSENT>", r
    assert int(lines[2]) <= 25, r


def test_built_env_has_no_secret_like_keys():
    env = sk._safe_subprocess_env()
    bad = [k for k in env if any(t in k.upper() for t in ("KEY", "TOKEN", "SECRET", "PASSWORD"))]
    assert bad == [], bad
    assert "PATH" in env
    assert set(env) <= {k.upper() for k in sk._SAFE_ENV_KEYS} | set(sk._SAFE_ENV_KEYS)


# --------------------------------------------------------------------------
# G4 超时回收进程树
# --------------------------------------------------------------------------
def test_timeout_kills_process_tree():
    """父进程 spawn 一个 grandchild；超时后整棵树都必须死（旧实现会留下孤儿孙进程）。

    用心跳文件判活，避免依赖 wmic/ps 的输出编码。
    """
    beat = os.path.join(_SANDBOX, "heartbeat.txt")
    if os.path.exists(beat):
        os.remove(beat)
    _write(
        "gchild.py",
        "import time\n"
        "for i in range(2000):\n"
        "    open('heartbeat.txt', 'w').write(str(i))\n"
        "    time.sleep(0.1)\n",
    )
    _write(
        "parent.py",
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, 'gchild.py'])\n"
        "time.sleep(120)\n",
    )
    r = sk._run_shell("python parent.py", 3, confirmed=True)
    assert "timed out" in r.get("error", "").lower(), r

    time.sleep(1.0)  # 给回收留出时间
    assert os.path.exists(beat), "grandchild never started; test is not exercising tree-kill"
    with open(beat) as f:
        first = f.read()
    time.sleep(1.5)
    with open(beat) as f:
        second = f.read()
    assert first == second, (
        f"grandchild still alive after timeout (heartbeat {first} -> {second}); "
        "process tree was not reaped"
    )


# --------------------------------------------------------------------------
# 输出解码健壮性（非 UTF-8 代码页 / 二进制噪声）
# --------------------------------------------------------------------------
def test_undecodable_output_does_not_crash():
    """HIGH 回归：命令输出含当前编码无法解码的字节时不得崩。

    不设 errors='replace' 时，subprocess 的 reader 线程抛 UnicodeDecodeError →
    communicate() 返回 None → stdout[:4000] 抛 TypeError，被兜底 except 吞成
    "'NoneType' object is not subscriptable"。中文 Windows(936) 下 ipconfig /
    tasklist 这类免确认命令会直接不可用。
    """
    _write(
        "noisy.py",
        "import sys\n"
        "sys.stdout.buffer.write(bytes(range(128, 256)) * 8)\n"
        "sys.stdout.buffer.write(b'\\nTAIL-OK\\n')\n"
        "sys.stderr.buffer.write(bytes(range(128, 256)))\n",
    )
    r = sk._run_shell("python noisy.py", 30, confirmed=True)
    assert "error" not in r, r
    assert r["returncode"] == 0, r
    assert isinstance(r["stdout"], str) and isinstance(r["stderr"], str), r
    assert "TAIL-OK" in r["stdout"], r


def test_allowlisted_system_commands_return_text():
    """ipconfig / tasklist 在白名单内且免确认，必须能正常返回文本。"""
    for cmd in ("ipconfig", "tasklist") if os.name == "nt" else ("date",):
        r = sk._run_shell(cmd, 30)
        if "error" in r and "FileNotFoundError" in str(r["error"]):
            continue  # 该环境没有此命令，跳过
        assert "error" not in r, (cmd, r)
        assert isinstance(r.get("stdout"), str), (cmd, r)


# --------------------------------------------------------------------------
# 文件读写沙箱（既有行为回归）
# --------------------------------------------------------------------------
def test_file_io_sandbox_preserved():
    outside = "C:/Windows/win.ini" if os.name == "nt" else "/etc/passwd"
    assert "escapes sandbox root" in sk._read_file("../../etc/passwd")
    assert "escapes sandbox root" in sk._write_file(outside, "x")["error"]
    assert "message" in sk._write_file("ok.txt", "hi")
    assert sk._read_file("ok.txt") == "hi"
