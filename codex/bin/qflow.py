# -*- coding: utf-8 -*-
"""智能体调用流程命令的入口（Codex 版）。由插件目录之外的启动器 qflow 调用。

除了转给 scripts/flow.py 的流程命令，还多三个：
  qflow guard    检查钩子是否在运行（每次开工前先运行）
  qflow doctor   安装自检
  qflow update   更新到最新版本
钩子没有被信任时关卡是失效的，所以会改动任务记录的命令在钩子没运行时一律拒绝。
"""
import os
import subprocess
import sys

BIN = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BIN)
import qf_common as C  # noqa: E402

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

READ_ONLY = {"status", "guard", "doctor", "update", "-h", "--help"}


def guard_message(detail):
    return ("未通过：quality-workflow 的钩子没有在运行（%s），流程关卡现在是失效的。先不要开始工作。\n%s\n"
            "用户处理好之后，再运行一次 %s guard 确认。" % (detail, C.TRUST_STEPS, C.qflow_cmd()))


def run(script, args):
    root = C.project_dir()
    env = dict(os.environ, CLAUDE_PROJECT_DIR=root, QFLOW_CMD=C.qflow_cmd(), PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    return subprocess.call([sys.executable, "-B", script] + args, cwd=root, env=env)


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else ""
    if cmd == "doctor":
        sys.exit(run(os.path.join(BIN, "doctor.py"), args[1:]))
    if cmd == "update":
        sys.exit(run(os.path.join(BIN, "qf_install.py"), ["update"] + args[1:]))
    alive, detail = C.hooks_alive()
    if cmd == "guard":
        if alive or os.environ.get("QFLOW_NO_GUARD") == "1":
            print("通过：钩子在运行（%s），流程关卡有效。插件版本 %s。" % (detail, C.VERSION))
            sys.exit(0)
        print(guard_message(detail))
        sys.exit(1)
    if not alive and cmd not in READ_ONLY and os.environ.get("QFLOW_NO_GUARD") != "1":
        print(guard_message(detail), file=sys.stderr)
        sys.exit(2)
    sys.exit(run(os.path.join(C.PLUGIN, "scripts", "flow.py"), args))


if __name__ == "__main__":
    main()
