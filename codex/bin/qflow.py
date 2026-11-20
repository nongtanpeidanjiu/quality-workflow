# -*- coding: utf-8 -*-
"""智能体调用流程命令的入口（Codex 版）。由插件目录之外的启动器 qflow 调用。

除了转给 scripts/flow.py 的流程命令，还多这几个：
  qflow guard      开工前的检查：流程是否在用、关卡是否有效
  qflow doctor     安装自检
  qflow update     更新到最新版本
  qflow off / on   停用、恢复这套流程（停用要附用户原话，或由用户在终端里运行）
  qflow uninstall  卸载，并把装在插件目录外的东西清干净（同样要出自用户）
插件启用着、钩子却没运行时，关卡是失效的，这时会改动任务记录的命令一律拒绝。
用户有意停用或卸载的，不算故障，一律放行。
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

READ_ONLY = {"status", "guard", "doctor", "update", "off", "on", "uninstall", "-h", "--help"}
NORMAL = ("按普通方式工作即可：不需要再运行流程命令（start、direct、confirm 等），也不要劝用户把它打开。"
          "用户说要卸载时，仍然用 qflow uninstall；用户说要恢复时，用 qflow on。")


def guard_message(detail):
    return ("未通过：quality-workflow 是启用状态，但它的钩子没有在运行（%s），流程关卡现在是失效的。先不要开始工作。\n%s\n"
            "用户处理好之后，再运行一次 %s guard 确认。\n"
            "如果用户这次说的就是要卸载这个插件，直接运行 %s uninstall，不受上面的限制。" % (detail, C.TRUST_STEPS, C.qflow_cmd(), C.qflow_cmd()))


def run(script, args):
    root = C.project_dir()
    env = dict(os.environ, CLAUDE_PROJECT_DIR=root, QFLOW_CMD=C.qflow_cmd(), PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    return subprocess.call([sys.executable, "-B", script] + args, cwd=root, env=env)


def user_said(quote):
    """核对这句话是不是用户说过的。用户消息由钩子记录，所以只有钩子在运行时才核对得到。"""
    if not quote:
        return False
    sys.path.insert(0, os.path.join(C.PLUGIN, "scripts"))
    os.environ.setdefault("CLAUDE_PROJECT_DIR", C.project_dir())
    import flowlib as L
    return L.find_user_quote(L.project_root(C.project_dir()), quote) is not None


def by_user(args, what):
    """停用和卸载只能出自用户：要么附上核对得到的用户原话，要么是用户本人在终端里运行。

    否则智能体可以自己运行它们来绕开关卡。返回 None 表示通过，否则返回拒绝的话。
    """
    # 只有关卡真的在起作用时才需要核对：这时智能体自己停用或卸载，才是在绕开关卡。
    # 用户已经关掉钩子、停用插件，或者钩子还没被信任，关卡本来就不起作用，没有什么可绕开的；
    # 而且钩子不运行就记不到用户的话，要求原话只会让用户卸载不了。
    if C.intent()[0] != "active" or not C.hooks_alive()[0]:
        return None
    quote = args[args.index("--quote") + 1] if "--quote" in args and args.index("--quote") + 1 < len(args) else ""
    if user_said(quote):
        return None
    if sys.stdin.isatty() and sys.stdout.isatty():
        try:
            answer = input("确认要%s quality-workflow 吗？输入 yes 继续：" % what).strip().lower()
        except EOFError:
            answer = ""
        return None if answer in ("yes", "y", "是") else "已取消。"
    return ("未通过：%s要由用户决定。请加上 --quote \"用户说要%s的那句原话\"（整条消息或其中完整的一句）。"
            "用户没有这样说过，就不要%s。用户也可以自己在 Codex 的 Settings > Hooks 里关掉钩子，或在插件页面停用插件。" % (what, what, what))


def cmd_off(args):
    reason = by_user(args, "停用")
    if reason:
        print(reason, file=sys.stderr)
        return 2
    C.write_json(C.off_path(), {"ts": __import__("time").time(), "project": C.project_dir()})
    print("已停用 quality-workflow：写文件不再被拦，也不再要求先表态和建档。%s\n以后想恢复，运行 %s on 。" % (NORMAL, C.qflow_cmd()))
    return 0


def cmd_on():
    try:
        os.remove(C.off_path())
        print("已恢复 quality-workflow。新建一个任务后生效；钩子如果被关掉了，还要在 Settings > Hooks 里打开。")
    except OSError:
        print("quality-workflow 本来就没有被 qflow off 停用。")
    return 0


def cmd_guard():
    state, why = C.intent()
    if state == "uninstalled":
        # 从界面卸载时平台不会通知插件，留在插件目录外的东西只能在这里顺手清掉。
        run(os.path.join(BIN, "qf_install.py"), ["cleanup"])
        print("通过：%s，留在外面的东西已经清理。%s" % (why, NORMAL))
        return 0
    if state != "active":
        print("通过：%s。%s" % (why, NORMAL))
        return 0
    alive, detail = C.hooks_alive()
    if alive or os.environ.get("QFLOW_NO_GUARD") == "1":
        print("通过：钩子在运行（%s），流程关卡有效。插件版本 %s。" % (detail, C.VERSION))
        return 0
    print(guard_message(detail))
    return 1


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else ""
    if cmd == "doctor":
        sys.exit(run(os.path.join(BIN, "doctor.py"), args[1:]))
    if cmd == "update":
        sys.exit(run(os.path.join(BIN, "qf_install.py"), ["update"] + args[1:]))
    if cmd == "guard":
        sys.exit(cmd_guard())
    if cmd == "off":
        sys.exit(cmd_off(args[1:]))
    if cmd == "on":
        sys.exit(cmd_on())
    if cmd == "uninstall":
        reason = by_user(args[1:], "卸载")
        if reason:
            print(reason, file=sys.stderr)
            sys.exit(2)
        sys.exit(run(os.path.join(BIN, "qf_install.py"), ["uninstall"]))
    state, why = C.intent()
    alive, detail = C.hooks_alive()
    if state == "active" and not alive and cmd not in READ_ONLY and os.environ.get("QFLOW_NO_GUARD") != "1":
        print(guard_message(detail), file=sys.stderr)
        sys.exit(2)
    sys.exit(run(os.path.join(C.PLUGIN, "scripts", "flow.py"), args))


if __name__ == "__main__":
    main()
