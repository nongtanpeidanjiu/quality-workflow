# -*- coding: utf-8 -*-
"""安装自检（Codex 版）：qflow doctor。逐项检查环境，每一项不通过都给出处理办法。"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

BIN = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BIN)
import qf_common as C  # noqa: E402

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

results = []


def check(name, ok, detail, fix=None):
    results.append((name, ok, detail, fix))


# 1. Python
ok = sys.version_info >= (3, 8)
check("Python", ok, "Python %s（%s）" % (sys.version.split()[0], sys.executable),
      None if ok else "Python 版本太旧。运行插件目录 bin 下的 setup-python 脚本，会自动装一份新的。")

# 2. 插件文件
required = [
    ".codex-plugin/plugin.json", "hooks/hooks.json", "hooks/hook_handler.py", "hooks/codex_hook.py",
    "bin/hook.sh", "bin/hook.ps1", "bin/find-python.sh", "bin/find-python.ps1", "bin/setup-python.sh", "bin/setup-python.ps1",
    "bin/qflow.py", "bin/qf_common.py", "bin/qf_install.py", "scripts/flow.py", "scripts/flowlib.py",
    "agents/independent-checker.toml", "skills/quality-workflow/SKILL.md", "skills/quality-workflow/assets/任务单模板.md",
]
missing = [f for f in required if not os.path.isfile(os.path.join(C.PLUGIN, f))]
check("插件文件", not missing, ("缺少：" + "、".join(missing)) if missing else "完整（版本 %s）" % C.VERSION,
      "插件包不完整。请重新安装插件。" if missing else None)

# 3. 关卡逻辑：在临时目录里模拟“说定前写文件”“难以收回的命令”“检查员写文件”
if not missing:
    tmp = os.path.realpath(tempfile.mkdtemp(prefix="qflow-doctor-"))
    tmp_home = os.path.realpath(tempfile.mkdtemp(prefix="qflow-doctor-home-"))
    try:
        env = dict(os.environ, CLAUDE_PROJECT_DIR=tmp, QFLOW_PROJECT_DIR=tmp, QFLOW_HOME=tmp_home, QFLOW_NO_HEARTBEAT="1",
                   QFLOW_NO_UPDATE_CHECK="1", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
        s = subprocess.run([sys.executable, "-B", os.path.join(C.PLUGIN, "scripts", "flow.py"), "start", "--title", "doctor"],
                           cwd=tmp, env=env, capture_output=True)

        def hook(**payload):
            d = dict(session_id="doctor", cwd=tmp, hook_event_name="PreToolUse", **payload)
            r = subprocess.run([sys.executable, "-B", os.path.join(C.PLUGIN, "hooks", "codex_hook.py")],
                               input=json.dumps(d).encode("utf-8"), cwd=tmp, env=env, capture_output=True)
            try:
                return json.loads(r.stdout.decode("utf-8"))["hookSpecificOutput"]["permissionDecision"]
            except (ValueError, KeyError):
                return None

        patch = "*** Begin Patch\n*** Add File: %s\n+x\n*** End Patch" % os.path.join(tmp, "docs", "a.md")
        w = hook(tool_name="apply_patch", tool_input={"command": patch})
        b = hook(tool_name="Bash", tool_input={"command": "git push --force origin main"})
        k = hook(tool_name="apply_patch", tool_input={"command": patch.replace("docs", ".flow")}, agent_type="independent-checker")
        ok = s.returncode == 0 and w == "deny" and b == "deny" and k == "deny"
        check("关卡逻辑", ok,
              "说定前写文件被拦下，难以收回的命令要先问用户，检查员不能改文件" if ok
              else "建档=%s，写文件=%s，难以收回的命令=%s，检查员写文件=%s；%s" % (s.returncode, w, b, k, s.stderr.decode("utf-8", "replace").strip()[:300]),
              None if ok else "把这段输出原样发给插件提供者。不要修改插件文件让它通过。")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(tmp_home, ignore_errors=True)

# 4. 钩子是否被信任并在运行。Codex 规定插件的钩子要用户审核信任后才运行，插件更新后要重新信任。
alive, detail = C.hooks_alive(max_age=6 * 3600, events=("UserPromptSubmit", "SessionStart", "PreToolUse"))
check("钩子在运行", alive, ("最近一次：" + detail) if alive else "没有收到当前版本钩子的运行记录（%s）。" % detail,
      None if alive else C.TRUST_STEPS + "在新任务里再运行一次自检。")

# 5. 独立检查员
agent = os.path.join(C.codex_home(), "agents", "independent-checker.toml")
has_agent = os.path.isfile(agent)
check("独立检查员", has_agent, "已安装（%s）" % agent if has_agent else "没有找到 %s" % agent,
      None if has_agent else "运行 %s update 重新安装，它会补上这个文件。" % C.qflow_cmd())

# 6. 全局说明里的开工检查。钩子没被信任时，只有这一段能让智能体先停下来。
guide = os.path.join(C.codex_home(), "AGENTS.md")
try:
    with open(guide, encoding="utf-8") as f:
        has_guide = "quality-workflow:start" in f.read()
except OSError:
    has_guide = False
check("开工检查", has_guide, "全局说明里有开工前检查关卡的要求（%s）" % guide if has_guide else "%s 里没有 quality-workflow 的那一段" % guide,
      None if has_guide else "运行 %s update 重新安装，它会补上这一段。" % C.qflow_cmd())

passed = all(r[1] for r in results)
lines = ["quality-workflow 安装自检（Codex 版）", ""]
for name, ok, detail, fix in results:
    lines.append("%s｜%s：%s" % ("通过" if ok else "未通过", name, detail))
    if not ok and fix:
        lines.append("    怎么修：%s" % fix)
lines.append("")
if passed:
    C.write_json(C.doctor_ok_path(), {"version": C.VERSION, "ts": time.time(), "platform": sys.platform, "python": sys.executable})
    lines.append("全部通过。插件已经生效。")
else:
    lines.append("没有全部通过。修复时只能处理环境（例如安装 Python、信任钩子、重新安装插件）；不要修改插件的规则文件、钩子配置或任务记录来让检查通过。修好后再运行一次自检。")
print("\n".join(lines))
sys.exit(0 if passed else 1)
