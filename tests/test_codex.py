# -*- coding: utf-8 -*-
"""Codex 版的自动测试：适配层、钩子检查、自检、安装和更新。不需要模型，也不碰真实的 Codex 配置。

运行：python3 -m unittest discover -s tests -p 'test_*.py'
"""
import glob
import hashlib
import http.server
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CODEX = os.path.join(REPO, "codex")
HOOK = os.path.join(CODEX, "hooks", "codex_hook.py")
QFLOW = os.path.join(CODEX, "bin", "qflow.py")
INSTALL = os.path.join(CODEX, "bin", "qf_install.py")
VERSION = json.load(open(os.path.join(CODEX, ".codex-plugin", "plugin.json"), encoding="utf-8"))["version"]

# 假的 codex 命令：只实现安装用到的四条插件命令，把插件复制到假的 CODEX_HOME 里。
FAKE_CODEX = r'''#!/bin/sh
exec "%(py)s" - "$@" <<'PYEOF'
import json, os, shutil, sys
a = sys.argv[1:]
home = os.environ["CODEX_HOME"]
state = os.path.join(home, "fake-state.json")
try: s = json.load(open(state))
except Exception: s = {"markets": {}, "log": []}
s["log"].append(a)
code = 0
if a[:3] == ["plugin", "marketplace", "add"]:
    m = json.load(open(os.path.join(a[3], ".agents", "plugins", "marketplace.json"), encoding="utf-8"))
    s["markets"][m["name"]] = a[3]
elif a[:3] == ["plugin", "marketplace", "remove"]:
    code = 0 if s["markets"].pop(a[3], None) else 1
elif a[:2] == ["plugin", "add"]:
    name, market = a[2].split("@")
    root = s["markets"].get(market)
    if not root: code = 1
    else:
        src = os.path.join(root, "plugins", name)
        v = json.load(open(os.path.join(src, ".codex-plugin", "plugin.json"), encoding="utf-8"))["version"]
        dst = os.path.join(home, "plugins", "cache", market, name, v)
        shutil.rmtree(os.path.dirname(dst), ignore_errors=True)
        shutil.copytree(src, dst)
elif a[:2] == ["plugin", "remove"]:
    name, market = a[2].split("@")
    shutil.rmtree(os.path.join(home, "plugins", "cache", market, name), ignore_errors=True)
json.dump(s, open(state, "w"))
sys.exit(code)
PYEOF
'''


class Base(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="qcx-proj-"))
        self.home = os.path.realpath(tempfile.mkdtemp(prefix="qcx-home-"))
        self.codex_home = os.path.join(self.home, "codex-home")
        os.makedirs(self.codex_home)
        self.env = dict(os.environ, QFLOW_HOME=os.path.join(self.home, "qf"), CODEX_HOME=self.codex_home, HOME=self.home,
                        QFLOW_PROJECT_DIR=self.root, QFLOW_NO_UPDATE_CHECK="1", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
        for k in ("CLAUDE_PROJECT_DIR", "QFLOW_DISABLED", "QFLOW_NO_HEARTBEAT", "QFLOW_NO_GUARD", "QFLOW_DIR", "QFLOW_CMD"):
            self.env.pop(k, None)
        self.state = os.path.join(self.home, "qf", "codex")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.home, ignore_errors=True)

    def hook(self, event, env=None, **kw):
        d = dict(dict(hook_event_name=event, session_id="s1", cwd=self.root), **kw)
        r = subprocess.run([sys.executable, "-B", HOOK], input=json.dumps(d).encode("utf-8"), env=env or self.env, capture_output=True, cwd=self.root)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        out = r.stdout.decode("utf-8").strip()
        return json.loads(out) if out else {}

    def decision(self, out):
        spec = out.get("hookSpecificOutput") or {}
        return spec.get("permissionDecision"), spec.get("permissionDecisionReason") or ""

    def qflow(self, *args, env=None):
        r = subprocess.run([sys.executable, "-B", QFLOW] + list(args), env=env or self.env, capture_output=True, cwd=self.root)
        return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace")

    def patch(self, *files, kind="Add"):
        body = "".join("*** %s File: %s\n+x\n" % (kind, f) for f in files)
        return {"command": "*** Begin Patch\n%s*** End Patch" % body}

    def write(self, *files, **kw):
        return self.decision(self.hook("PreToolUse", tool_name="apply_patch", tool_input=self.patch(*files), **kw))


class Adapter(Base):
    def test_write_needs_declaration_and_names_launcher(self):
        verdict, reason = self.write(os.path.join(self.root, "a.md"))
        self.assertEqual(verdict, "deny")
        self.assertIn("写文件前要先表态", reason)
        self.assertIn(os.path.join(self.state, "qflow"), reason)
        self.assertNotIn(".mjs", reason)

    def test_patch_is_checked_file_by_file(self):
        code, out = self.qflow("start", "--title", "guide", env=dict(self.env, QFLOW_NO_GUARD="1"))
        self.assertEqual(code, 0, out)
        sheet = glob.glob(os.path.join(self.root, ".flow", "tasks", "*", "任务单.md"))[0]
        self.assertEqual(self.write(sheet)[0], None)  # 说定前可以写任务单
        verdict, _ = self.write(sheet, os.path.join(self.root, "docs", "a.md"))
        self.assertEqual(verdict, "deny")  # 同一个补丁里有一个不该写的文件，整个拒绝
        self.assertEqual(self.decision(self.hook("PreToolUse", tool_name="apply_patch", tool_input={
            "command": "*** Begin Patch\n*** Update File: %s\n*** Move to: %s\n@@\n-a\n+b\n*** End Patch" % (sheet, os.path.join(self.root, "moved.md"))}))[0], "deny")

    def test_hard_to_undo_command_needs_user_reply(self):
        cmd = {"command": "git push --force origin main"}
        verdict, reason = self.decision(self.hook("PreToolUse", tool_name="Bash", tool_input=cmd))
        self.assertEqual(verdict, "deny")
        self.assertIn("等用户回复", reason)
        self.assertEqual(self.decision(self.hook("PreToolUse", tool_name="Bash", tool_input=cmd))[0], "deny")  # 没回复，再试还是拒绝
        self.hook("UserPromptSubmit", prompt="可以，推吧")
        self.assertEqual(self.decision(self.hook("PreToolUse", tool_name="Bash", tool_input=cmd))[0], None)  # 回复后放行一次
        self.assertEqual(self.decision(self.hook("PreToolUse", tool_name="Bash", tool_input=cmd))[0], "deny")  # 只放行一次
        other = {"command": "git push --force origin dev"}
        self.hook("PreToolUse", tool_name="Bash", tool_input=other)
        self.hook("UserPromptSubmit", prompt="好", session_id="another")
        self.assertEqual(self.decision(self.hook("PreToolUse", tool_name="Bash", tool_input=other))[0], "deny")  # 别的会话里的回复不算

    def test_plain_command_and_other_tools_pass(self):
        self.assertEqual(self.hook("PreToolUse", tool_name="Bash", tool_input={"command": "ls -la"}), {})
        self.assertEqual(self.hook("PreToolUse", tool_name="collaborationspawn_agent", tool_input={"task_name": "x"}), {})

    def test_checker_cannot_write(self):
        target = os.path.join(self.root, ".flow", "x.md")
        verdict, reason = self.write(target, agent_id="a1", agent_type="independent-checker")
        self.assertEqual(verdict, "deny")
        self.assertIn("只能看", reason)
        self.assertNotIn("只能看", self.write(target, agent_id="a2", agent_type="default")[1])

    def test_checker_report_is_captured(self):
        self.qflow("start", "--title", "guide", env=dict(self.env, QFLOW_NO_GUARD="1"))
        self.hook("SubagentStart", agent_id="a1", agent_type="independent-checker")
        self.hook("SubagentStop", agent_id="a1", agent_type="independent-checker", last_assistant_message="类型：清单\n结论：通过\n")
        reports = glob.glob(os.path.join(self.root, ".flow", "tasks", "*", "evidence", "check-1.md"))
        self.assertEqual(len(reports), 1)
        self.hook("SubagentStop", agent_id="a2", agent_type="default", last_assistant_message="类型：清单\n结论：通过\n")
        self.assertEqual(len(glob.glob(os.path.join(self.root, ".flow", "tasks", "*", "evidence", "check-*.md"))), 1)

    def test_user_words_and_choices_are_logged(self):
        self.qflow("start", "--title", "guide", env=dict(self.env, QFLOW_NO_GUARD="1"))
        self.hook("UserPromptSubmit", prompt="分母用上季度的销量")
        self.hook("PostToolUse", tool_name="request_user_input", tool_input={}, tool_response={"answers": {"用哪一份制度": "用 2026 版"}})
        log = open(os.path.join(self.root, ".flow", "user-messages.jsonl"), encoding="utf-8").read()
        self.assertIn("分母用上季度的销量", log)
        self.assertIn("用 2026 版", log)

    def test_session_start_context(self):
        out = self.hook("SessionStart", source="startup")
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn(os.path.join(self.state, "qflow"), ctx)
        self.assertIn("doctor", ctx)
        self.assertIn("quality-workflow skill", ctx)
        os.makedirs(self.state, exist_ok=True)
        json.dump({"version": VERSION}, open(os.path.join(self.state, "doctor-ok.json"), "w"))
        json.dump({"ts": time.time(), "version": "99.0.0"}, open(os.path.join(self.state, "latest.json"), "w"))
        env = dict(self.env)
        env.pop("QFLOW_NO_UPDATE_CHECK")
        out = self.hook("SessionStart", env=env, source="startup")
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("安装自检", ctx)
        self.assertIn("99.0.0", ctx)
        self.assertIn("重新信任", ctx)
        self.assertNotIn("99.0.0", self.hook("SessionStart", env=env, source="startup")["hookSpecificOutput"]["additionalContext"])  # 每天只提醒一次

    def test_stop_passes_without_task(self):
        self.assertEqual(self.hook("Stop", stop_hook_active=False, last_assistant_message="好"), {})


class Guard(Base):
    def test_guard_requires_fresh_heartbeat_of_current_version(self):
        code, out = self.qflow("guard")
        self.assertEqual(code, 1)
        self.assertIn("Settings > Hooks", out)
        code, out = self.qflow("start", "--title", "x")
        self.assertEqual(code, 2)  # 钩子没运行时，不让建档开工
        self.assertFalse(os.path.isdir(os.path.join(self.root, ".flow")))
        self.hook("PreToolUse", tool_name="Bash", tool_input={"command": "ls"})
        self.assertEqual(self.qflow("guard")[0], 0)
        self.assertEqual(self.qflow("start", "--title", "x")[0], 0)
        # 插件更新后，旧版本的心跳不算
        hb = os.path.join(self.state, "hook-heartbeat.json")
        data = json.load(open(hb))
        for v in data.values():
            v["version"] = "0.0.1"
        json.dump(data, open(hb, "w"))
        code, out = self.qflow("guard")
        self.assertEqual(code, 1)
        self.assertIn("旧版本", out)
        self.assertEqual(self.qflow("status")[0], 0)  # 只读命令不受影响

    def test_stale_heartbeat_fails(self):
        self.hook("PreToolUse", tool_name="Bash", tool_input={"command": "ls"})
        hb = os.path.join(self.state, "hook-heartbeat.json")
        data = json.load(open(hb))
        for v in data.values():
            v["ts"] -= 3600
        json.dump(data, open(hb, "w"))
        self.assertEqual(self.qflow("guard")[0], 1)

    def test_doctor(self):
        code, out = self.qflow("doctor")
        self.assertEqual(code, 1, out)
        self.assertIn("通过｜关卡逻辑", out)
        self.assertIn("未通过｜钩子在运行", out)
        self.assertIn("未通过｜独立检查员", out)
        self.assertIn("未通过｜开工检查", out)
        with open(os.path.join(self.codex_home, "AGENTS.md"), "w", encoding="utf-8") as f:
            f.write("<!-- quality-workflow:start -->\n<!-- quality-workflow:end -->\n")
        self.hook("UserPromptSubmit", prompt="运行自检")
        os.makedirs(os.path.join(self.codex_home, "agents"))
        shutil.copy(os.path.join(CODEX, "agents", "independent-checker.toml"), os.path.join(self.codex_home, "agents"))
        code, out = self.qflow("doctor")
        self.assertEqual(code, 0, out)
        self.assertIn("全部通过", out)
        self.assertTrue(os.path.isfile(os.path.join(self.state, "doctor-ok.json")))
        self.assertFalse(os.path.isdir(os.path.join(self.root, ".flow")))  # 自检不在用户项目里留东西


@unittest.skipIf(os.name == "nt", "shell 脚本只在 Mac 上测")
class Shell(Base):
    def no_python_env(self):
        env = dict(self.env, QFLOW_ONLY_PRIVATE_PYTHON="1", QFLOW_NO_AUTO_PYTHON="1", PATH="/usr/bin:/bin")
        env.pop("QFLOW_PYTHON", None)
        return env

    def sh_hook(self, env, **d):
        r = subprocess.run(["sh", os.path.join(CODEX, "bin", "hook.sh")], input=json.dumps(dict(cwd=self.root, session_id="s", **d)).encode("utf-8"),
                           env=env, capture_output=True, cwd=self.root)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        out = r.stdout.decode("utf-8").strip()
        return json.loads(out) if out else {}

    def test_hook_sh_runs_python(self):
        out = self.sh_hook(self.env, hook_event_name="PreToolUse", tool_name="apply_patch", tool_input=self.patch(os.path.join(self.root, "a.md")))
        self.assertEqual(self.decision(out)[0], "deny")

    def test_no_python_warns_and_fails_closed(self):
        env = self.no_python_env()
        out = self.sh_hook(env, hook_event_name="SessionStart", source="startup")
        self.assertIn("找不到可用的 Python", out["hookSpecificOutput"]["additionalContext"])
        self.assertIn("setup-python.sh", out["hookSpecificOutput"]["additionalContext"])
        patch = self.patch(os.path.join(self.root, "a.md"))
        self.assertEqual(self.sh_hook(env, hook_event_name="PreToolUse", tool_name="apply_patch", tool_input=patch), {})
        os.makedirs(os.path.join(self.root, ".flow", "tasks"))
        self.assertEqual(self.decision(self.sh_hook(env, hook_event_name="PreToolUse", tool_name="apply_patch", tool_input=patch))[0], "deny")


@unittest.skipIf(os.name == "nt", "假的 codex 命令是 shell 脚本")
class Install(Base):
    def setUp(self):
        super().setUp()
        self.fake = os.path.join(self.home, "codex")
        with open(self.fake, "w") as f:
            f.write(FAKE_CODEX % {"py": sys.executable})
        os.chmod(self.fake, os.stat(self.fake).st_mode | stat.S_IEXEC)
        self.env["QFLOW_CODEX"] = self.fake
        self.market = os.path.join(self.home, "pkg", "marketplace")
        shutil.copytree(CODEX, os.path.join(self.market, "plugins", "quality-workflow"), ignore=shutil.ignore_patterns("__pycache__"))

    def run_install(self, *args, script=INSTALL, env=None):
        r = subprocess.run([sys.executable, "-B", script] + list(args), env=env or self.env, capture_output=True, cwd=self.home)
        return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace")

    def installed(self):
        return os.path.join(self.codex_home, "plugins", "cache", "quality-workflow-codex", "quality-workflow")

    def test_install_from_package(self):
        with open(os.path.join(self.codex_home, "AGENTS.md"), "w", encoding="utf-8") as f:
            f.write("用户自己的说明\n")
        code, out = self.run_install("install", "--from", self.market)
        self.assertEqual(code, 0, out)
        self.assertIn("Settings > Hooks", out)
        root = os.path.join(self.installed(), VERSION)
        self.assertTrue(os.path.isfile(os.path.join(root, "bin", "qflow.py")))
        self.assertEqual(open(os.path.join(self.state, "plugin-root.txt")).read(), root)
        agent = os.path.join(self.codex_home, "agents", "independent-checker.toml")
        self.assertIn('name = "independent-checker"', open(agent, encoding="utf-8").read())
        # 命令入口在插件目录之外，能直接运行
        r = subprocess.run([os.path.join(self.state, "qflow"), "start", "--title", "x"], env=dict(self.env, QFLOW_NO_GUARD="1"), capture_output=True, cwd=self.root)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        self.assertTrue(os.path.isdir(os.path.join(self.root, ".flow", "tasks")))
        self.assertTrue(os.path.isfile(os.path.join(self.state, "qflow.ps1")))
        # 全局说明里加了开工检查，用户原有的内容不动
        guide = os.path.join(self.codex_home, "AGENTS.md")
        text = open(guide, encoding="utf-8").read()
        self.assertTrue(text.startswith("用户自己的说明\n"))
        self.assertEqual(text.count("quality-workflow:start"), 1)
        self.assertIn("guard", text)
        # 再装一次：旧的被卸载，用户自己的同名子智能体会被备份，全局说明里不会重复加
        with open(agent, "w") as f:
            f.write('name = "independent-checker"\n')
        code, out = self.run_install("install", "--from", self.market)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(glob.glob(agent + ".bak-*")), 1)
        text = open(guide, encoding="utf-8").read()
        self.assertTrue(text.startswith("用户自己的说明\n"))
        self.assertEqual(text.count("quality-workflow:start"), 1)
        log = json.load(open(os.path.join(self.codex_home, "fake-state.json")))["log"]
        self.assertIn(["plugin", "remove", "quality-workflow@quality-workflow-codex"], log)

    def test_install_without_codex_explains(self):
        env = dict(self.env, QFLOW_CODEX="", PATH="/usr/bin:/bin")
        code, out = self.run_install("install", "--from", self.market, env=env)
        if code == 0:
            self.skipTest("这台电脑上装了 ChatGPT 桌面版，测不了“找不到 Codex”的情况")
        self.assertIn("找不到 ChatGPT 桌面版", out)

    def serve(self, version, corrupt=False):
        """起一个本地网站，冒充 GitHub：提供清单和插件文件。"""
        site = os.path.join(self.home, "site")
        shutil.rmtree(site, ignore_errors=True)
        ref = "v%s" % version
        shutil.copytree(CODEX, os.path.join(site, ref, "codex"), ignore=shutil.ignore_patterns("__pycache__"))
        pj = os.path.join(site, ref, "codex", ".codex-plugin", "plugin.json")
        data = json.load(open(pj, encoding="utf-8"))
        data["version"] = version
        json.dump(data, open(pj, "w", encoding="utf-8"), ensure_ascii=False)
        files = {}
        for d, _, names in os.walk(os.path.join(site, ref, "codex")):
            for n in names:
                full = os.path.join(d, n)
                files[os.path.relpath(full, os.path.join(site, ref, "codex")).replace(os.sep, "/")] = hashlib.sha256(open(full, "rb").read()).hexdigest()
        if corrupt:
            with open(os.path.join(site, ref, "codex", "scripts", "flow.py"), "a") as f:
                f.write("# 被改过\n")
        os.makedirs(os.path.join(site, "main"))
        json.dump({"version": version, "ref": ref, "files": files}, open(os.path.join(site, "main", "codex-manifest.json"), "w"))
        class Quiet(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=site, **k)

            def log_message(self, *a):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        self.env["QFLOW_UPDATE_BASES"] = "http://127.0.0.1:1/{ref}/{path} http://127.0.0.1:%d/{ref}/{path}" % srv.server_port

    def test_update_and_check(self):
        self.assertEqual(self.run_install("install", "--from", self.market)[0], 0)
        updater = os.path.join(self.installed(), VERSION, "bin", "qf_install.py")
        self.serve(VERSION)
        code, out = self.run_install("update", script=updater)
        self.assertEqual(code, 0, out)
        self.assertIn("已经是最新版本", out)
        self.serve("99.1.0")
        self.assertEqual(self.run_install("check", script=updater)[0], 0)
        self.assertEqual(json.load(open(os.path.join(self.state, "latest.json")))["version"], "99.1.0")
        code, out = self.run_install("update", script=updater)
        self.assertEqual(code, 0, out)
        self.assertIn("更新成功", out)
        self.assertIn("重新信任", out)
        self.assertTrue(os.path.isfile(os.path.join(self.installed(), "99.1.0", "bin", "qflow.py")))
        self.assertEqual(open(os.path.join(self.state, "plugin-root.txt")).read(), os.path.join(self.installed(), "99.1.0"))

    def test_update_rejects_tampered_file(self):
        self.assertEqual(self.run_install("install", "--from", self.market)[0], 0)
        self.serve("99.2.0", corrupt=True)
        code, out = self.run_install("update", script=os.path.join(self.installed(), VERSION, "bin", "qf_install.py"))
        self.assertEqual(code, 1, out)
        self.assertIn("校验值不一致", out)
        self.assertTrue(os.path.isfile(os.path.join(self.installed(), VERSION, "bin", "qflow.py")))  # 原来的版本没受影响

    def test_falls_back_to_main_when_tag_has_no_files(self):
        """标签打得比文件早时（标签里没有 Codex 版的文件），改从主分支下载，照样核对校验值。"""
        self.serve("99.4.0")
        site = os.path.join(self.home, "site")
        shutil.move(os.path.join(site, "v99.4.0", "codex"), os.path.join(site, "main", "codex"))
        code, out = self.run_install("install")
        self.assertEqual(code, 0, out)
        self.assertTrue(os.path.isfile(os.path.join(self.installed(), "99.4.0", "bin", "qflow.py")))

    def test_install_from_network(self):
        self.serve("99.3.0")
        code, out = self.run_install("install")
        self.assertEqual(code, 0, out)
        self.assertTrue(os.path.isfile(os.path.join(self.installed(), "99.3.0", "bin", "qflow.py")))


class Sync(unittest.TestCase):
    def test_shared_sources_in_sync(self):
        r = subprocess.run([sys.executable, os.path.join(REPO, "tools", "sync_codex.py"), "--check"], capture_output=True)
        self.assertEqual(r.returncode, 0, r.stdout.decode("utf-8", "replace"))

    def test_one_version_everywhere(self):
        r = subprocess.run([sys.executable, os.path.join(REPO, "tools", "bump_version.py"), "--check"], capture_output=True)
        self.assertEqual(r.returncode, 0, r.stdout.decode("utf-8", "replace"))
        wb = json.load(open(os.path.join(REPO, "plugin", ".codebuddy-plugin", "plugin.json"), encoding="utf-8"))["version"]
        self.assertEqual(wb, VERSION)  # WorkBuddy 版和 Codex 版是同一个版本号
        manifest = json.load(open(os.path.join(REPO, "codex-manifest.json"), encoding="utf-8"))
        self.assertEqual((manifest["version"], manifest["ref"]), (VERSION, "v" + VERSION))

    def test_powershell_files_have_bom_and_crlf(self):
        for f in glob.glob(os.path.join(CODEX, "bin", "*.ps1")) + [os.path.join(REPO, "installer", "codex", "install.ps1")]:
            data = open(f, "rb").read()
            self.assertTrue(data.startswith(b"\xef\xbb\xbf"), f)  # 没有这个标记，旧版 PowerShell 会把中文读成乱码
            self.assertNotIn(b"\n", data.replace(b"\r\n", b""), f)

    def test_hooks_json_covers_events(self):
        hooks = json.load(open(os.path.join(CODEX, "hooks", "hooks.json"), encoding="utf-8"))["hooks"]
        self.assertEqual(set(hooks), {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "SubagentStart", "SubagentStop", "Stop"})
        for groups in hooks.values():
            h = groups[0]["hooks"][0]
            self.assertIn("${PLUGIN_ROOT}/bin/hook.sh", h["command"])
            self.assertIn("hook.ps1", h["commandWindows"])


if __name__ == "__main__":
    unittest.main()
