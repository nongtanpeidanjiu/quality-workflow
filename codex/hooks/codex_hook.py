# -*- coding: utf-8 -*-
"""Codex 钩子适配层：把 Codex 的钩子输入翻译成 hook_handler.py 能懂的格式，再把结果翻译回去。

hook_handler.py 和 scripts/ 下的流程脚本与 WorkBuddy 版共用同一份源码，这里只处理平台差异：
- Codex 改文件用 apply_patch，一次可以改多个文件，要拆开逐个过关卡。
- Codex 的钩子不能弹窗问用户，“难以收回的动作”改成先拒绝、等用户回复后放行一次。
- Codex 不能给子智能体单独设只读，独立检查员写文件由这里拒绝。
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PLUGIN, "bin"))
import qf_common as C  # noqa: E402

HANDLER = os.path.join(HERE, "hook_handler.py")
PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+?)\s*$|^\*\*\* Move to: (.+?)\s*$", re.M)
ASK_TOOLS = re.compile(r"request_user_input|ask_?user", re.I)
CHECKER = "independent-checker"


def emit(obj=None):
    if obj:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.exit(0)


def deny(reason):
    emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}})


def run_handler(d):
    """调用共用的关卡程序，返回它输出的 JSON（没有输出时为空字典）。"""
    env = dict(os.environ, CLAUDE_PROJECT_DIR=C.project_dir(d), QFLOW_CMD=C.qflow_cmd(), PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, "-B", HANDLER], input=json.dumps(d, ensure_ascii=False).encode("utf-8"), capture_output=True, env=env, timeout=120)
    if r.stderr:
        sys.stderr.write(r.stderr.decode("utf-8", "replace"))
    out = r.stdout.decode("utf-8", "replace").strip()
    if r.returncode != 0:
        raise RuntimeError("关卡程序退出码 %s" % r.returncode)
    return json.loads(out) if out else {}


def decision(out):
    spec = out.get("hookSpecificOutput") or {}
    return spec.get("permissionDecision"), spec.get("permissionDecisionReason") or ""


def patch_files(d):
    cmd = (d.get("tool_input") or {}).get("command") or ""
    files = []
    for m in PATCH_FILE.finditer(cmd):
        p = (m.group(1) or m.group(2)).strip()
        if p and p not in files:
            files.append(p)
    return files


# ---------- 难以收回的动作：先拒绝，用户回复后放行一次 ----------

def asks_path():
    return os.path.join(C.state_dir(), "pending-asks.json")


def load_asks():
    try:
        with open(asks_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    cutoff = time.time() - 24 * 3600
    return {k: v for k, v in data.items() if v.get("asked", 0) > cutoff}


def save_asks(data):
    os.makedirs(C.state_dir(), exist_ok=True)
    with open(asks_path(), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def ask_key(d):
    body = json.dumps([d.get("tool_name"), d.get("tool_input")], ensure_ascii=False, sort_keys=True)
    return "%s:%s" % (d.get("session_id") or "", hashlib.sha256(body.encode("utf-8")).hexdigest()[:24])


def handle_ask(d, reason):
    asks = load_asks()
    key = ask_key(d)
    entry = asks.get(key)
    if entry and entry.get("replied"):
        del asks[key]
        save_asks(asks)
        emit()  # 用户在被拦之后回复过，放行这一次
    asks[key] = {"asked": time.time(), "replied": False, "session": d.get("session_id")}
    save_asks(asks)
    deny(reason.rstrip("。") + "。现在先不要执行，也不要换别的办法绕开：把要做的事和后果告诉用户，等用户回复。"
         "用户明确同意后，把同一条命令原样再执行一次就会放行；用户没有明确同意就不要再执行。")


def mark_user_replied(session):
    asks = load_asks()
    changed = False
    for v in asks.values():
        if v.get("session") == session and not v.get("replied"):
            v["replied"] = True
            changed = True
    if changed:
        save_asks(asks)


# ---------- 各事件 ----------

def pre_tool(d):
    name = d.get("tool_name") or ""
    is_checker = CHECKER in (d.get("agent_type") or "")
    if name == "apply_patch":
        if is_checker:
            deny("独立检查员只能看、不能改。把发现的问题写进报告，由执行者修改。")
        for p in patch_files(d):
            out = run_handler(dict(d, tool_name="Write", tool_input={"file_path": p}))
            verdict, reason = decision(out)
            if verdict == "deny":
                deny(reason)
            if verdict == "ask":
                handle_ask(d, reason)
        emit()
    if name == "Bash" or name.startswith("mcp__"):
        out = run_handler(d)
        verdict, reason = decision(out)
        if verdict == "ask":
            handle_ask(d, reason)
        emit(out)
    emit()


def post_tool(d):
    name = d.get("tool_name") or ""
    if name == "apply_patch":
        for p in patch_files(d):
            run_handler(dict(d, tool_name="Write", tool_input={"file_path": p}))
        emit()
    if ASK_TOOLS.search(name):
        C.save_sample("request_user_input", d)
        run_handler(dict(d, tool_name="AskUserQuestion"))
    emit()


def session_start(d):
    C.remember_plugin_root(PLUGIN)
    out = run_handler(d)
    spec = out.get("hookSpecificOutput") or {"hookEventName": "SessionStart"}
    notes, messages = [], []
    q = C.qflow_cmd()
    notes.append("【quality-workflow】本文和技能里的 qflow 命令写成：%s 。例如 qflow status 就运行 %s status 。" % (q, q))
    if not C.doctor_passed():
        notes.append("【quality-workflow】插件还没有通过安装自检。开始任何工作前，先运行：%s doctor 。有问题就按它给出的建议修好环境，再运行一次，直到全部通过。" % q)
        messages.append("quality-workflow 插件还没有完成安装自检，智能体会先运行自检。")
    latest = C.update_notice()
    if latest:
        notes.append("【quality-workflow】插件有新版本 %s（当前 %s）。在第一次回复用户时，用一句话告诉用户：“quality-workflow 插件有新版本 %s，说‘更新插件’即可升级，升级后需要在 Settings > Hooks 里重新信任它的钩子。”然后照常处理用户的请求。用户说“更新插件”时，运行：%s update ，把输出原样告诉用户。"
                     % (latest, C.VERSION, latest, q))
        messages.append("quality-workflow 插件有新版本 %s，说“更新插件”即可升级。" % latest)
    spec["additionalContext"] = "\n".join(notes + [spec.get("additionalContext") or ""]).strip()
    out["hookSpecificOutput"] = spec
    if messages:
        out["systemMessage"] = "\n".join([out.get("systemMessage") or ""] + messages).strip()
    emit(out)


def main():
    raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    try:
        d = json.loads(raw)
    except ValueError:
        sys.exit(0)
    event = d.get("hook_event_name")
    C.record_heartbeat(event)
    try:
        if event == "PreToolUse":
            pre_tool(d)
        if event == "PostToolUse":
            post_tool(d)
        if event == "SessionStart":
            session_start(d)
        if event == "UserPromptSubmit":
            mark_user_replied(d.get("session_id"))
        if event in ("UserPromptSubmit", "SubagentStart", "SubagentStop", "Stop"):
            emit(run_handler(d))
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        # 写入前的关卡在流程已启用时不能因内部错误放行；其他事件不阻塞工作。
        if event == "PreToolUse" and os.path.isdir(os.path.join(C.project_dir(d), ".flow", "tasks")):
            deny("流程关卡内部出错，暂时拒绝这次操作：%s。请运行 %s doctor 检查。" % (e, C.qflow_cmd()))
        sys.stderr.write("quality-workflow 钩子出错：%s\n" % e)
    sys.exit(0)


if __name__ == "__main__":
    main()
