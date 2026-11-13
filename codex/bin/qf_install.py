# -*- coding: utf-8 -*-
"""安装、更新、查新版本（Codex 版）。只用 Python 标准库，Mac 和 Windows 通用。

  qf_install.py install [--from <安装包里的 marketplace 文件夹>]   安装；不给 --from 就从网络下载
  qf_install.py update [--force]                                  更新到最新版本
  qf_install.py check                                             查最新版本号，写到 latest.json（后台用）

不依赖 git，也不要求命令路径里有 codex：会到 ChatGPT 桌面版的安装位置里找自带的那个。
插件装成“本地市场”，所以 Codex 不会在后台自动更新它。更新只在用户说“更新插件”时进行，
因为 Codex 规定插件内容一变，钩子就要重新信任。
"""
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

BIN = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BIN)
import qf_common as C  # noqa: E402

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

MANIFEST = "codex-manifest.json"
# 先查 GitHub 原始文件，访问不了时再查 jsDelivr 的镜像。{ref} 是分支或标签，{path} 是仓库里的路径。
BASES = (os.environ.get("QFLOW_UPDATE_BASES") or " ".join([
    "https://raw.githubusercontent.com/%s/{ref}/{path}" % C.GITHUB_REPO,
    "https://cdn.jsdelivr.net/gh/%s@{ref}/{path}" % C.GITHUB_REPO,
])).split()
AGENT_MARK = "# 由 quality-workflow 插件安装，更新插件时会被覆盖。"


def say(text=""):
    print(text, flush=True)


# ---------- 下载 ----------

def http_get(url, timeout=30):
    """先用 Python 自带的下载；不行再用系统的 curl（免安装版 Python 在 Mac 上可能没有证书库）。"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "quality-workflow", "Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status == 200:
                return r.read()
    except Exception:  # noqa: BLE001
        pass
    curls = [os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "curl.exe"), "curl"] if C.IS_WIN else ["curl", "/usr/bin/curl"]
    for curl in curls:
        try:
            r = subprocess.run([curl, "-fsSL", "--max-time", str(timeout), url], capture_output=True, timeout=timeout + 10)
            if r.returncode == 0:
                return r.stdout
        except (OSError, subprocess.SubprocessError):
            continue
    return None


def fetch(ref, path, timeout=30):
    quoted = urllib.parse.quote(path)
    for base in BASES:
        data = http_get(base.format(ref=ref, path=quoted), timeout)
        if data is not None:
            return data, base
    return None, None


def fetch_manifest(timeout=30):
    data, _ = fetch("main", MANIFEST, timeout)
    if data is None:
        return None
    try:
        m = json.loads(data.decode("utf-8-sig"))
        return m if m.get("version") and isinstance(m.get("files"), dict) else None
    except ValueError:
        return None


def download_plugin(manifest, dest):
    """按清单把插件文件下载到 dest，逐个核对校验值。成功返回 None，失败返回原因。

    先按版本标签下载；标签里没有这些文件时（比如标签打得比文件早），改从主分支下载。
    不管从哪里下载，每个文件都要和清单里的校验值一致才算数。
    """
    refs = [manifest.get("ref") or "main"]
    if "main" not in refs:
        refs.append("main")
    errors = []
    for ref in refs:
        err = _download_from(ref, manifest, dest)
        if err is None:
            return None
        errors.append(err)
        shutil.rmtree(dest, ignore_errors=True)
        os.makedirs(dest, exist_ok=True)
    # 文件被改过比下载不到更值得让用户知道，优先报这个。
    return next((e for e in errors if "校验值" in e), errors[-1])


def _download_from(ref, manifest, dest):
    for rel, want in sorted(manifest["files"].items()):
        data, _ = fetch(ref, "codex/" + rel, 60)
        if data is None:
            return "下载不到 %s" % rel
        if hashlib.sha256(data).hexdigest() != want:
            return "%s 的校验值不一致" % rel
        target = os.path.join(dest, *rel.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as f:
            f.write(data)
    return None


# ---------- 找 Codex ----------

def find_codex():
    exe = "codex.exe" if C.IS_WIN else "codex"
    cands = [os.environ.get("QFLOW_CODEX"), shutil.which("codex"),
             os.path.join(C.codex_home(), "plugins", ".plugin-appserver", "codex-cli", "bin", exe)]
    if C.IS_WIN:
        roots = [os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"), os.environ.get("ProgramFiles", ""),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WindowsApps")]
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-Command",
                                "Get-AppxPackage | Where-Object { $_.Name -match 'ChatGPT|Codex' } | ForEach-Object { $_.InstallLocation }"],
                               capture_output=True, text=True, timeout=30)
            roots += [line.strip() for line in r.stdout.splitlines() if line.strip()]
        except (OSError, subprocess.SubprocessError):
            pass
        for root in roots:
            if not root or not os.path.isdir(root):
                continue
            for pat in ("codex.exe", "*/codex.exe", "*ChatGPT*/**/codex.exe", "*Codex*/**/codex.exe", "**/codex-cli/**/codex.exe"):
                cands += glob.glob(os.path.join(root, pat), recursive=True)[:5]
    else:
        for app in ("/Applications/ChatGPT.app", os.path.expanduser("~/Applications/ChatGPT.app"),
                    "/Applications/Codex.app", os.path.expanduser("~/Applications/Codex.app")):
            cands.append(os.path.join(app, "Contents", "Resources", "codex-cli", "CodexCLI.app", "Contents", "MacOS", "codex"))
            cands.append(os.path.join(app, "Contents", "Resources", "codex"))
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def codex(exe, *args):
    try:
        r = subprocess.run([exe] + list(args), capture_output=True, stdin=subprocess.DEVNULL, timeout=180)
        return r.returncode == 0, (r.stdout + r.stderr).decode("utf-8", "replace").strip()
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)


# ---------- 安装 ----------

def market_dir():
    return os.path.join(C.state_dir(), "marketplace")


def build_market(plugin_src):
    """把插件放进固定位置的本地市场。删掉下载的文件也不影响使用。"""
    dest = market_dir()
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(os.path.join(dest, ".agents", "plugins"))
    shutil.copytree(plugin_src, os.path.join(dest, "plugins", C.PLUGIN_NAME), ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"))
    C.write_json(os.path.join(dest, ".agents", "plugins", "marketplace.json"), {
        "name": C.MARKET, "interface": {"displayName": "Quality Workflow"},
        "plugins": [{"name": C.PLUGIN_NAME, "source": {"source": "local", "path": "./plugins/%s" % C.PLUGIN_NAME},
                     "policy": {"installation": "AVAILABLE"}, "category": "Productivity"}],
    })
    return dest


def write_launchers():
    """启动器放在插件目录之外：插件更新后路径不变，也不影响 Codex 对插件内容的信任。"""
    d = C.state_dir()
    os.makedirs(d, exist_ok=True)
    sh = os.path.join(d, "qflow")
    with open(sh, "w", encoding="utf-8", newline="\n") as f:
        f.write('#!/bin/sh\n# quality-workflow 的命令入口（Mac）。由安装程序生成。\n'
                'QF_HOME="${QFLOW_HOME:-$HOME/.quality-workflow}"\n'
                'ROOT=$(cat "$QF_HOME/codex/plugin-root.txt" 2>/dev/null)\n'
                'if [ ! -f "$ROOT/bin/qflow.py" ]; then echo "未通过：找不到 quality-workflow 插件，请重新安装。" >&2; exit 2; fi\n'
                '. "$ROOT/bin/find-python.sh"\n'
                'if [ -z "$PY" ]; then sh "$ROOT/bin/setup-python.sh" >&2; QF_FRESH=1; qf_find_python; fi\n'
                'if [ -z "$PY" ]; then echo "未通过：找不到可用的 Python，自动安装也没有成功。" >&2; exit 2; fi\n'
                'exec "$PY" -B "$ROOT/bin/qflow.py" "$@"\n')
    try:
        os.chmod(sh, 0o755)
    except OSError:
        pass
    with open(os.path.join(d, "qflow.ps1"), "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write('# quality-workflow 的命令入口（Windows）。由安装程序生成。\n'
                "$QfHomeDir = if ($env:QFLOW_HOME) { $env:QFLOW_HOME } else { Join-Path $env:USERPROFILE '.quality-workflow' }\n"
                "$rootFile = Join-Path $QfHomeDir 'codex\\plugin-root.txt'\n"
                "$root = if (Test-Path -LiteralPath $rootFile) { (Get-Content -LiteralPath $rootFile -Raw).Trim() } else { '' }\n"
                "if (-not $root -or -not (Test-Path -LiteralPath (Join-Path $root 'bin\\qflow.py'))) { [Console]::Error.WriteLine('quality-workflow plugin not found, please reinstall.'); exit 2 }\n"
                ". (Join-Path $root 'bin\\find-python.ps1')\n"
                "if (-not $QfPy) { & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'bin\\setup-python.ps1') | Out-Host; $QfPy = Find-QfPython -Fresh }\n"
                "if (-not $QfPy) { [Console]::Error.WriteLine('Python not found and automatic install failed.'); exit 2 }\n"
                "try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}\n"
                "$env:PYTHONIOENCODING = 'utf-8'; $env:PYTHONUTF8 = '1'\n"
                "& $QfPy -B (Join-Path $root 'bin\\qflow.py') @args\n"
                "exit $LASTEXITCODE\n")


def install_agent(plugin_root):
    """独立检查员是 Codex 的子智能体，定义文件要放在 Codex 的 agents 目录里。"""
    src = os.path.join(plugin_root, "agents", "independent-checker.toml")
    dest = os.path.join(C.codex_home(), "agents", "independent-checker.toml")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.isfile(dest):
        with open(dest, encoding="utf-8", errors="replace") as f:
            if AGENT_MARK not in f.read():
                backup = "%s.bak-%d" % (dest, time.time())
                shutil.move(dest, backup)
                say("  已有一个同名的子智能体定义，备份到了 %s" % backup)
    shutil.copyfile(src, dest)


GUIDE_START = "<!-- quality-workflow:start（由 quality-workflow 插件维护，卸载插件时可以整段删除） -->"
GUIDE_END = "<!-- quality-workflow:end -->"


def install_guide():
    """在 Codex 每次都会读的全局说明（AGENTS.md）里加一小段：开工前先检查关卡是否在运行。

    技能只有在智能体觉得需要时才会读，钩子没被信任时它也不会运行，所以这条检查必须放在这里。
    只改带标记的这一段，文件里的其他内容不动。
    """
    path = os.path.join(C.codex_home(), "AGENTS.md")
    q = C.qflow_cmd()
    block = "\n".join([
        GUIDE_START,
        "## quality-workflow",
        "",
        "开始任何会改动文件的工作之前，先运行一次：`%s guard`" % q,
        "",
        "它检查 quality-workflow 插件的流程关卡（钩子）是否在运行。",
        "",
        "- **没有通过：** 先不要动任何文件。把它输出的处理办法原样告诉用户，等用户处理好。",
        "- **通过：** 按 quality-workflow 技能的要求做。写文件前要先表态，小事用 `qflow direct` 声明，其他先 `qflow start` 建档并复述。",
        "",
        "同一个任务里检查一次即可。只是回答问题、不改文件时不用检查。",
        GUIDE_END,
    ])
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        text = ""
    if GUIDE_START in text and GUIDE_END in text:
        new = text[:text.index(GUIDE_START)] + block + text[text.index(GUIDE_END) + len(GUIDE_END):]
    else:
        new = (text.rstrip("\n") + "\n\n" if text.strip() else "") + block + "\n"
    if new != text:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(new)
    return path


def installed_root():
    base = os.path.join(C.codex_home(), "plugins", "cache", C.MARKET, C.PLUGIN_NAME)
    dirs = [d for d in glob.glob(os.path.join(base, "*")) if os.path.isfile(os.path.join(d, "bin", "qflow.py"))]
    return max(dirs, key=os.path.getmtime) if dirs else None


def version_of(plugin_root):
    return (C.read_json(os.path.join(plugin_root, ".codex-plugin", "plugin.json")) or {}).get("version", "未知")


def do_install(plugin_src, updating=False):
    """plugin_src 是插件目录。返回退出码。"""
    version = version_of(plugin_src)
    say("第 2 步：找到 ChatGPT 桌面版自带的 Codex")
    exe = find_codex()
    market = build_market(plugin_src)
    if not exe:
        say("  没有找到。插件文件已经放到了 %s" % market)
        say("\n安装没有完成：找不到 ChatGPT 桌面版自带的 Codex 程序。请确认已经安装并打开过 ChatGPT 桌面版。")
        say("也可以手动安装：在 ChatGPT 桌面版的 Codex 里打开插件页面，添加本地市场，位置填 %s ，然后安装 Quality Workflow。" % market)
        return 1
    say("  已找到：%s" % exe)

    say("\n第 3 步：卸载旧版本，安装新版本")
    codex(exe, "plugin", "remove", C.KEY)
    codex(exe, "plugin", "marketplace", "remove", C.MARKET)
    ok, out = codex(exe, "plugin", "marketplace", "add", market)
    if not ok:
        say("\n安装没有完成：添加插件市场失败。\n%s" % out)
        return 1
    ok, out = codex(exe, "plugin", "add", C.KEY)
    root = installed_root()
    if not ok or not root:
        say("\n安装没有完成：安装插件失败。\n%s" % out)
        return 1
    C.remember_plugin_root(root)
    write_launchers()
    install_agent(root)
    guide = install_guide()
    try:
        os.remove(C.latest_path())
    except OSError:
        pass
    say("  已安装 quality-workflow %s，独立检查员和命令入口也装好了。" % version)
    say("  在 Codex 的全局说明 %s 里加了一小段（带 quality-workflow 标记）：开工前先检查流程关卡是否在运行。" % guide)

    say("\n%s成功：quality-workflow %s 已安装。还差一步，必须由用户来做：" % ("更新" if updating else "安装", version))
    say("  1. 完全退出并重新打开 ChatGPT 桌面版，进入 Codex。")
    say("  2. 打开 Settings > Hooks（或点输入框附近的“Review hooks”），找到 Quality Workflow，把它的钩子全部信任并打开。")
    say("     Codex 规定插件的钩子要用户审核后才运行。不做这一步，插件装上了但不起作用。%s" % ("插件更新后，之前的信任会失效，所以要重新信任。" if updating else ""))
    say("  3. 新建一个任务，随便发一句话。智能体会自动运行安装自检，直到看到“全部通过。插件已经生效。”")
    return 0


def cmd_install(args):
    say("正在安装 quality-workflow（Codex 版）")
    say("\n第 1 步：检测环境")
    say("  Python %s：可用（%s）" % (sys.version.split()[0], sys.executable))
    src = None
    if "--from" in args:
        src = os.path.join(args[args.index("--from") + 1], "plugins", C.PLUGIN_NAME)
        if not os.path.isfile(os.path.join(src, ".codex-plugin", "plugin.json")):
            say("\n安装没有完成：安装包不完整，找不到 %s 。请重新完整解压安装包。" % src)
            return 1
        say("  插件文件：使用安装包里的（版本 %s）" % version_of(src))
        return do_install(src)
    say("  插件文件：正在从网络下载……")
    manifest = fetch_manifest()
    if not manifest:
        say("\n安装没有完成：连不上 GitHub，镜像也连不上。请下载安装包（压缩包）后，按里面的《给智能体的安装步骤》安装。")
        return 1
    tmp = tempfile.mkdtemp(prefix="qflow-install-")
    try:
        err = download_plugin(manifest, tmp)
        if err:
            say("\n安装没有完成：%s。请稍后再试，或下载安装包（压缩包）安装。" % err)
            return 1
        say("  插件文件：已下载并核对（版本 %s）" % manifest["version"])
        return do_install(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def cmd_update(args):
    say("当前版本：%s。正在查询最新版本……" % C.VERSION)
    manifest = fetch_manifest()
    if not manifest:
        say("更新没有完成：连不上 GitHub，镜像也连不上。原来的版本没有受影响。可以稍后再试，或者下载最新的安装包重新安装。")
        return 1
    if C.compare_version(manifest["version"], C.VERSION) <= 0 and "--force" not in args:
        C.write_json(C.latest_path(), {"ts": time.time(), "version": manifest["version"]})
        write_launchers()
        root = installed_root()
        if root:
            install_agent(root)
            install_guide()
        say("已经是最新版本：%s。" % C.VERSION)
        return 0
    tmp = tempfile.mkdtemp(prefix="qflow-update-")
    try:
        err = download_plugin(manifest, tmp)
        if err:
            say("更新没有完成：%s。原来的版本没有受影响。" % err)
            return 1
        say("已下载并核对新版本 %s。\n" % manifest["version"])
        return do_install(tmp, updating=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def cmd_check():
    manifest = fetch_manifest(timeout=10)
    old = C.read_json(C.latest_path(), {}) or {}
    C.write_json(C.latest_path(), dict(old, ts=time.time(), version=(manifest or {}).get("version")))
    return 0


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "install"
    if cmd == "install":
        return cmd_install(args[1:])
    if cmd == "update":
        return cmd_update(args[1:])
    if cmd == "check":
        return cmd_check()
    say("用法：qf_install.py install [--from <marketplace 文件夹>] | update [--force] | check")
    return 2


if __name__ == "__main__":
    sys.exit(main())
