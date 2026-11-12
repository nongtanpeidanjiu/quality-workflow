"""WorkBuddy 启动层的测试：通过 Node 启动器调用，不需要模型。"""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
HOOK = os.path.join(PLUGIN, "bin", "hook.mjs")
QFLOW = os.path.join(PLUGIN, "bin", "qflow.mjs")
DOCTOR = os.path.join(PLUGIN, "bin", "doctor.mjs")


def find_node():
    if os.environ.get("QFLOW_NODE"):
        return os.environ["QFLOW_NODE"]
    found = shutil.which("node")
    if found:
        return found
    for p in sorted(glob.glob(os.path.expanduser("~/.workbuddy/binaries/node/versions/*/bin/node")), reverse=True):
        return p
    raise unittest.SkipTest("找不到 Node")


NODE = find_node()
GOOD = open(os.path.join(HERE, "test_flow.py"), encoding="utf-8").read().split('GOOD = """', 1)[1].split('"""', 1)[0]


class WB(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="qwb-proj-"))
        self.home = os.path.realpath(tempfile.mkdtemp(prefix="qwb-home-"))
        os.makedirs(os.path.join(self.root, "docs"))
        self.env = dict(os.environ, HOME=self.home, USERPROFILE=self.home, CODEBUDDY_PROJECT_DIR=self.root, QFLOW_PYTHON=sys.executable, QFLOW_NO_UPDATE_CHECK="1")
        for k in ("CLAUDE_PROJECT_DIR", "QFLOW_DISABLED", "QFLOW_NO_HEARTBEAT", "WORKBUDDY_CONFIG_DIR"):
            self.env.pop(k, None)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.home, ignore_errors=True)

    def hook(self, event, env=None, **kw):
        d = dict(hook_event_name=event, session_id="s", cwd=self.root, **kw)
        r = subprocess.run([NODE, HOOK], input=json.dumps(d), env=env or self.env, capture_output=True, text=True, cwd=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout) if r.stdout.strip() else {}

    def qflow(self, *args, ok=True):
        r = subprocess.run([NODE, QFLOW] + list(args), env=self.env, capture_output=True, text=True, cwd=self.root)
        if ok:
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        else:
            self.assertNotEqual(r.returncode, 0)
        return r.stdout + r.stderr

    def decision(self, out):
        return (out.get("hookSpecificOutput") or {}).get("permissionDecision")

    def tdir(self):
        return glob.glob(os.path.join(self.root, ".flow", "tasks", "*"))[0]

    def say(self, text):
        time.sleep(0.02)
        self.hook("UserPromptSubmit", prompt=text)
        time.sleep(0.02)

    def understand(self):
        self.say("对，就按你说的理解来做")
        self.qflow("understand", "--text", "我理解你要一份给新用户看的安装指南，只写 docs。分两步：核对命令，写指南。", "--quote", "对，就按你说的理解来做")

    def list_check(self):
        self.hook("SubagentStop", agent_type="quality-workflow:independent-checker", last_assistant_message="类型：清单\n结论：通过\n")
        self.qflow("check", "--report", os.path.join(self.tdir(), "evidence", "check-1.md"))

    def test_full_path_through_node(self):
        self.qflow("start", "--title", "guide")
        self.assertEqual(self.decision(self.hook("PreToolUse", tool_name="Write", tool_input={"file_path": os.path.join(self.root, "docs/a.md")})), "deny")
        with open(os.path.join(self.tdir(), "任务单.md"), "w", encoding="utf-8") as f:
            f.write(GOOD)
        self.understand()
        self.list_check()
        self.qflow("state", "待确认")
        self.say("可以，就这样做")
        self.qflow("confirm", "--quote", "可以，就这样做")
        self.assertIn("K2", self.qflow("step", "S1", "进行中", ok=False))
        self.assertIsNone(self.decision(self.hook("PreToolUse", tool_name="Write", tool_input={"file_path": os.path.join(self.root, "docs/a.md")})))
        self.assertEqual(self.decision(self.hook("PreToolUse", tool_name="Write", tool_input={"file_path": os.path.join(self.root, "src.py")})), "deny")

    def test_internal_continuation_not_recorded_as_user(self):
        self.qflow("start", "--title", "guide")
        with open(os.path.join(self.tdir(), "任务单.md"), "w", encoding="utf-8") as f:
            f.write(GOOD)
        self.understand()
        self.list_check()
        self.qflow("state", "待确认")
        self.qflow("change", "--note", "x", ok=False)
        time.sleep(0.02)
        self.hook("UserPromptSubmit", prompt="确认", is_internal_continuation=True)
        self.qflow("confirm", "--quote", "确认", ok=False)

    def no_python_env(self):
        env = dict(self.env, PATH="/nonexistent")
        env.pop("QFLOW_PYTHON")
        return env

    def test_missing_python_warns_and_fails_closed(self):
        env = self.no_python_env()
        out = self.hook("SessionStart", env=env, source="startup")
        self.assertIn("流程关卡没有生效", out.get("systemMessage", ""))
        self.assertEqual(self.hook("PreToolUse", env=env, tool_name="Write", tool_input={"file_path": os.path.join(self.root, "a.md")}), {})
        os.makedirs(os.path.join(self.root, ".flow", "tasks"))
        out = self.hook("PreToolUse", env=env, tool_name="Write", tool_input={"file_path": os.path.join(self.root, "a.md")})
        self.assertEqual(self.decision(out), "deny")

    def test_doctor_reminder_until_passed(self):
        out = self.hook("SessionStart", source="startup")
        self.assertIn("doctor.mjs", out["hookSpecificOutput"]["additionalContext"])
        os.makedirs(os.path.join(self.home, ".quality-workflow"), exist_ok=True)
        version = json.load(open(os.path.join(PLUGIN, ".codebuddy-plugin", "plugin.json")))["version"]
        with open(os.path.join(self.home, ".quality-workflow", "doctor-ok.json"), "w") as f:
            json.dump({"version": version}, f)
        out = self.hook("SessionStart", source="startup")
        self.assertNotIn("doctor.mjs", out["hookSpecificOutput"]["additionalContext"])

    def write_latest(self, version, **extra):
        os.makedirs(os.path.join(self.home, ".quality-workflow"), exist_ok=True)
        with open(os.path.join(self.home, ".quality-workflow", "latest.json"), "w") as f:
            json.dump(dict(ts=int(time.time() * 1000), version=version, **extra), f)

    def test_update_notice_once_per_day(self):
        env = dict(self.env)
        env.pop("QFLOW_NO_UPDATE_CHECK")
        self.write_latest("99.0.0")
        out = self.hook("SessionStart", env=env, source="startup")
        self.assertIn("99.0.0", out["hookSpecificOutput"]["additionalContext"])
        self.assertIn("update.mjs", out["hookSpecificOutput"]["additionalContext"])
        self.assertIn("更新插件", out.get("systemMessage", ""))
        out = self.hook("SessionStart", env=env, source="startup")
        self.assertNotIn("99.0.0", out["hookSpecificOutput"]["additionalContext"])

    def test_no_notice_when_current_or_disabled(self):
        env = dict(self.env)
        env.pop("QFLOW_NO_UPDATE_CHECK")
        version = json.load(open(os.path.join(PLUGIN, ".codebuddy-plugin", "plugin.json")))["version"]
        self.write_latest(version)
        out = self.hook("SessionStart", env=env, source="startup")
        self.assertNotIn("新版本", out["hookSpecificOutput"]["additionalContext"])
        self.write_latest("99.0.0")
        out = self.hook("SessionStart", source="startup")
        self.assertNotIn("新版本", out["hookSpecificOutput"]["additionalContext"])

    def test_doctor_pass_survives_version_change(self):
        os.makedirs(os.path.join(self.home, ".quality-workflow"), exist_ok=True)
        with open(os.path.join(self.home, ".quality-workflow", "doctor-ok.json"), "w") as f:
            json.dump({"version": "0.0.1", "platform": sys.platform if sys.platform != "darwin" else "darwin"}, f)
        out = self.hook("SessionStart", source="startup")
        self.assertNotIn("doctor.mjs", out["hookSpecificOutput"]["additionalContext"])

    def test_check_update_reads_manifest(self):
        import http.server
        import threading
        body = json.dumps({"plugins": [{"name": "quality-workflow", "version": "9.8.7"}]}).encode()

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                code = 200 if self.path.startswith("/ok") else 404
                self.send_response(code)
                self.end_headers()
                if code == 200:
                    self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            base = "http://127.0.0.1:%d" % srv.server_port
            env = dict(self.env, QFLOW_UPDATE_URLS="%s/missing %s/ok" % (base, base))
            r = subprocess.run([NODE, os.path.join(PLUGIN, "bin", "check-update.mjs"), "--print"], env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            data = json.load(open(os.path.join(self.home, ".quality-workflow", "latest.json")))
            self.assertEqual(data["version"], "9.8.7")
            self.assertTrue(data["source"].endswith("/ok"))
        finally:
            srv.shutdown()

    def test_compare_version(self):
        code = ('import("%s").then(m => console.log([m.compareVersion("0.10.0","0.9.9")>0, m.compareVersion("0.4.0","0.4.0")===0,'
                ' m.compareVersion("0.3.3","0.4.0")<0, m.compareVersion("1.0","0.9.9")>0].join(",")))') % os.path.join(PLUGIN, "bin", "wb.mjs").replace("\\", "/")
        r = subprocess.run([NODE, "--input-type=module", "-e", code], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "true,true,true,true", r.stderr)

    def test_heartbeat_and_doctor(self):
        r = subprocess.run([NODE, DOCTOR], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("未通过｜WorkBuddy 调用插件", r.stdout)
        self.hook("UserPromptSubmit", prompt="运行自检")
        r = subprocess.run([NODE, DOCTOR], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("全部通过", r.stdout)
        self.assertTrue(os.path.isfile(os.path.join(self.home, ".quality-workflow", "doctor-ok.json")))

    def test_doctor_reports_old_plugin(self):
        os.makedirs(os.path.join(self.home, ".workbuddy"))
        with open(os.path.join(self.home, ".workbuddy", "settings.json"), "w") as f:
            json.dump({"enabledPlugins": {"quality-workflow-workbuddy@x": True, "quality-workflow@y": True}}, f)
        self.hook("UserPromptSubmit", prompt="运行自检")
        r = subprocess.run([NODE, DOCTOR], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("quality-workflow-workbuddy@x", r.stdout)
        self.assertNotIn("quality-workflow@y", r.stdout.split("旧版插件")[1].split("\n")[0])

    def test_doctor_does_not_fake_heartbeat(self):
        subprocess.run([NODE, DOCTOR], env=self.env, capture_output=True, text=True)
        self.assertFalse(os.path.exists(os.path.join(self.home, ".quality-workflow", "hook-heartbeat.json")))

    def test_project_dir_with_spaces_and_chinese(self):
        root = os.path.join(self.root, "我的 项目")
        os.makedirs(root)
        env = dict(self.env, CODEBUDDY_PROJECT_DIR=root)
        r = subprocess.run([NODE, QFLOW, "start", "--title", "t"], env=env, capture_output=True, text=True, cwd=root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isdir(os.path.join(root, ".flow", "tasks")))


if __name__ == "__main__":
    unittest.main()
