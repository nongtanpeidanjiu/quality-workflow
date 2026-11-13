# -*- coding: utf-8 -*-
"""Codex 版的公共函数：目录、版本、心跳、自检记录、新版本提醒。只用 Python 标准库。"""
import json
import os
import subprocess
import sys
import time

BIN = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(BIN)
IS_WIN = os.name == "nt"
PLUGIN_NAME = "quality-workflow"
MARKET = "quality-workflow-codex"
KEY = "%s@%s" % (PLUGIN_NAME, MARKET)
GITHUB_REPO = "nongtanpeidanjiu/quality-workflow"
HEARTBEAT_FRESH = 180  # 秒。qflow 命令本身会触发一次钩子，所以正常情况下心跳就在几秒前。


def _version():
    with open(os.path.join(PLUGIN, ".codex-plugin", "plugin.json"), encoding="utf-8") as f:
        return json.load(f)["version"]


VERSION = _version()


def home():
    return os.path.realpath(os.environ.get("QFLOW_HOME") or os.path.join(os.path.expanduser("~"), ".quality-workflow"))


def state_dir():
    return os.path.join(home(), "codex")


def codex_home():
    return os.path.realpath(os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex"))


def project_dir(d=None):
    return os.path.realpath(os.environ.get("QFLOW_PROJECT_DIR") or (d or {}).get("cwd") or os.getcwd())


def launcher_path():
    return os.path.join(state_dir(), "qflow.ps1" if IS_WIN else "qflow")


def qflow_cmd():
    """智能体运行 qflow 时要敲的命令。启动器在插件目录之外，插件更新后路径不变。"""
    if IS_WIN:
        return 'powershell -NoProfile -ExecutionPolicy Bypass -File "%s"' % launcher_path()
    return '"%s"' % launcher_path()


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def remember_plugin_root(root):
    """记下当前生效的插件目录，供插件目录之外的启动器使用。"""
    path = os.path.join(state_dir(), "plugin-root.txt")
    try:
        with open(path, encoding="utf-8") as f:
            if f.read().strip() == root:
                return
    except OSError:
        pass
    os.makedirs(state_dir(), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(root)


# ---------- 心跳：钩子每运行一次记一笔，用来判断钩子是否被信任并在运行 ----------

def heartbeat_path():
    return os.path.join(state_dir(), "hook-heartbeat.json")


def record_heartbeat(event):
    if os.environ.get("QFLOW_NO_HEARTBEAT") == "1" or not event:
        return
    try:
        data = read_json(heartbeat_path(), {}) or {}
        data[event] = {"ts": time.time(), "version": VERSION}
        write_json(heartbeat_path(), data)
    except OSError:
        pass  # 心跳失败不影响关卡


def hooks_alive(max_age=HEARTBEAT_FRESH, events=None):
    """最近 max_age 秒内，当前版本的钩子是否运行过。返回 (是否运行, 最近一次的说明)。"""
    data = read_json(heartbeat_path(), {}) or {}
    best = None
    for event, v in data.items():
        if events and event not in events:
            continue
        if v.get("version") != VERSION:
            continue
        if best is None or v.get("ts", 0) > best[1]:
            best = (event, v.get("ts", 0))
    if best and time.time() - best[1] <= max_age:
        return True, "%s，%d 秒前" % (best[0], time.time() - best[1])
    other = sorted(set(v.get("version") for v in data.values() if v.get("version") and v.get("version") != VERSION))
    if other:
        return False, "只有旧版本 %s 的钩子运行过，当前版本 %s 的钩子还没有运行" % ("、".join(other), VERSION)
    if best:
        return False, "上一次运行在 %d 分钟前" % ((time.time() - best[1]) / 60)
    return False, "钩子一次都没有运行过"


TRUST_STEPS = (
    "请用户这样做：打开 ChatGPT 桌面版，进入 Settings > Hooks（或点输入框附近的“Review hooks”），找到 Quality Workflow，"
    "把它的钩子全部信任并打开，然后新建一个任务。插件每次更新后都要重新信任一次，这是 Codex 的安全规定。"
)


# ---------- 自检记录 ----------

def doctor_ok_path():
    return os.path.join(state_dir(), "doctor-ok.json")


def doctor_passed():
    """这台电脑上通过过一次自检就算数。更新后要重新信任钩子，由心跳检查负责，不重复自检。"""
    d = read_json(doctor_ok_path())
    return bool(d and d.get("version"))


# ---------- 新版本提醒 ----------

def compare_version(a, b):
    def parts(v):
        return [int(x) if x.isdigit() else 0 for x in str(v).split(".")]
    pa, pb = parts(a), parts(b)
    n = max(len(pa), len(pb))
    pa += [0] * (n - len(pa))
    pb += [0] * (n - len(pb))
    return (pa > pb) - (pa < pb)


def latest_path():
    return os.path.join(state_dir(), "latest.json")


def spawn_background(args):
    kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if IS_WIN:
        kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000  # 脱离父进程、新进程组、不弹窗口
    else:
        kw["start_new_session"] = True
    try:
        subprocess.Popen(args, **kw)
    except OSError:
        pass


def update_notice():
    """有新版本时返回版本号，每天最多提醒一次。查询在后台做，不等结果。"""
    if os.environ.get("QFLOW_NO_UPDATE_CHECK") == "1":
        return ""
    latest = read_json(latest_path())
    if not latest or time.time() - latest.get("ts", 0) > 24 * 3600:
        spawn_background([sys.executable, "-B", os.path.join(BIN, "qf_install.py"), "check"])
    if not latest or not latest.get("version") or compare_version(latest["version"], VERSION) <= 0:
        return ""
    if latest.get("notified") == latest["version"] and time.time() - latest.get("notified_at", 0) < 24 * 3600:
        return ""
    try:
        write_json(latest_path(), dict(latest, notified=latest["version"], notified_at=time.time()))
    except OSError:
        pass
    return latest["version"]


def save_sample(name, d):
    """保存一份钩子输入的样本，方便以后核对 Codex 的格式。只留在本机。"""
    try:
        path = os.path.join(state_dir(), "samples", "%s.json" % name)
        write_json(path, {"ts": time.time(), "tool_name": d.get("tool_name"), "tool_input": d.get("tool_input"), "tool_response": d.get("tool_response")})
    except OSError:
        pass
