#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 WorkBuddy 版（plugin/）里两边共用的内容同步到 Codex 版（codex/），并生成下载清单。

  python3 tools/sync_codex.py           同步并重写清单
  python3 tools/sync_codex.py --check   只检查是否一致，不一致时退出码为 1（自动测试用）

共用的：流程脚本、关卡程序、参考文件、任务单模板。
按平台改写后生成的：技能说明（SKILL.md）、独立检查员的定义。
改流程规则时只改 plugin/ 下的源文件，然后运行本脚本。
版本号不在这里改：两个平台共用仓库根目录的 VERSION，用 tools/bump_version.py 改。
"""
import hashlib
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "plugin")
DST = os.path.join(REPO, "codex")
SKILL = "skills/quality-workflow"

COPY = ["scripts/flow.py", "scripts/flowlib.py", "hooks/hook_handler.py", SKILL + "/assets/任务单模板.md"]
COPY += [SKILL + "/references/" + n for n in sorted(os.listdir(os.path.join(SRC, SKILL, "references")))]

HEAD_NEW = '''**本文和参考文件中的 `qflow`，是装在插件目录之外的一个命令入口，在项目根目录运行：**

- Mac：`"$HOME/.quality-workflow/codex/qflow"`，例如 `"$HOME/.quality-workflow/codex/qflow" status`
- Windows：`powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\\.quality-workflow\\codex\\qflow.ps1"`，后面接子命令

**每次开工前，先运行 `qflow guard`，然后照它说的做。** 它检查这套流程是否在用、关卡（钩子）是否有效，有三种结果：

- **通过，流程关卡有效：** 按本技能的要求做。
- **通过，流程已停用或已卸载：** 用户在 Codex 里关掉了钩子、停用或卸载了插件，或者让你运行过 `qflow off`。这是用户的决定，**按普通方式工作，不要再运行流程命令，也不要劝用户把它打开**。用户说要卸载或恢复时，照样用下面的 `qflow uninstall` 和 `qflow on`。
- **未通过：** 插件是启用状态，钩子却没在运行，通常是刚安装或刚更新、钩子还没有被信任。这时关卡是失效的，写文件不会被拦。**停下来，不要开始工作**，把它输出的处理办法原样告诉用户，等用户处理好再继续。

**停用、恢复和卸载都由用户决定，不要自作主张：**

- 用户说暂时不想用这套流程：`qflow off --quote "用户的原话"`。之后写文件不再被拦。用户说要恢复：`qflow on`。
- 用户说要卸载：`qflow uninstall --quote "用户的原话"`。它会把装在插件目录之外的东西一起清掉，并逐项报告。
- 流程关卡在起作用时，这两个命令会核对 `--quote` 是不是用户真的说过的话，不是就拒绝；用户已经关掉钩子、停用插件，或者钩子还没被信任时，不需要 `--quote`。不要为了省事或绕开关卡去运行它们，也不要去改 Codex 的配置文件。

**安装后还没通过自检时，** 先运行 `qflow doctor`。有未通过的项目，只按它的建议修环境（安装 Python、信任钩子、重新安装插件），不要修改插件的规则文件、钩子配置或任务记录来让它通过。修好后再运行，直到全部通过。用户说“更新插件”时运行 `qflow update`，把输出原样告诉用户。

**独立检查员**是一个名为 `independent-checker` 的子智能体。文中“调用 `independent-checker`”，指启动一个子智能体并把角色（agent type）指定为 `independent-checker`。它只能看不能改，写文件会被程序拒绝。'''

REPLACE = [
    ("**难以收回的动作单独管：** 删除数据、对外发布、强制推送这类动作，执行前程序会拦下来请用户确认。这和结果对不对是两回事，任何任务都适用。",
     "**难以收回的动作单独管：** 删除数据、对外发布、强制推送这类动作，第一次执行时程序会拒绝。你要把打算做的事和后果告诉用户，等用户回复；用户明确同意后，把同一条命令原样再执行一次，程序就会放行。用户没有明确同意，不要再执行，也不要换别的办法绕开。这和结果对不对是两回事，任何任务都适用。"),
]


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def skill_text():
    s = read(os.path.join(SRC, SKILL, "SKILL.md"))
    start = s.index("**本文和参考文件中的 `qflow`，一律写成")
    end = s.index("一项数据错了，方法和过程再漂亮")
    s = s[:start] + HEAD_NEW + "\n\n" + s[end:]
    for old, new in REPLACE:
        if old not in s:
            raise SystemExit("技能说明里找不到要改写的句子，请更新 tools/sync_codex.py：%s" % old[:30])
        s = s.replace(old, new)
    for word in ("CODEBUDDY", "WorkBuddy", ".mjs"):
        if word in s:
            raise SystemExit("生成的 Codex 技能说明里还有 WorkBuddy 版的写法：%s" % word)
    return s


def agent_text():
    s = read(os.path.join(SRC, "agents", "independent-checker.md"))
    front, body = s.split("---\n", 2)[1:]
    desc = next(l.split(":", 1)[1].strip() for l in front.splitlines() if l.startswith("description:"))
    old = "- 你不能运行命令，测试、脚本和重算的结果读 `evidence/` 里执行者保存的输出。"
    if old not in body:
        raise SystemExit("检查员定义里找不到要改写的句子，请更新 tools/sync_codex.py")
    body = body.replace(old, "- 你只能读，不能改：写文件会被程序拒绝，也不要用命令去改动任何文件。测试、脚本和重算的结果读 `evidence/` 里执行者保存的输出。")
    if "'''" in body:
        raise SystemExit("检查员定义里不能出现三个连续的单引号")
    return ('# 由 quality-workflow 插件安装，更新插件时会被覆盖。\n'
            'name = "independent-checker"\n'
            'description = %s\n'
            "developer_instructions = '''\n%s'''\n" % (json.dumps(desc, ensure_ascii=False), body.lstrip("\n")))


def expected():
    out = {rel: open(os.path.join(SRC, rel), "rb").read() for rel in COPY}
    out[SKILL + "/SKILL.md"] = skill_text().encode("utf-8")
    out["agents/independent-checker.toml"] = agent_text().encode("utf-8")
    return out


def all_files():
    files = {}
    for d, dirs, names in os.walk(DST):
        dirs[:] = [x for x in dirs if x != "__pycache__"]
        for n in names:
            if n == ".DS_Store":
                continue
            full = os.path.join(d, n)
            files[os.path.relpath(full, DST).replace(os.sep, "/")] = hashlib.sha256(open(full, "rb").read()).hexdigest()
    return files


def manifest_text():
    version = json.load(open(os.path.join(DST, ".codex-plugin", "plugin.json"), encoding="utf-8"))["version"]
    return json.dumps({"version": version, "ref": "v%s" % version, "files": dict(sorted(all_files().items()))}, ensure_ascii=False, indent=1) + "\n"


def main():
    check = "--check" in sys.argv
    stale = []
    for rel, data in expected().items():
        path = os.path.join(DST, rel)
        if not os.path.isfile(path) or open(path, "rb").read() != data:
            stale.append(rel)
            if not check:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(data)
    mpath = os.path.join(REPO, "codex-manifest.json")
    text = manifest_text()
    if not os.path.isfile(mpath) or read(mpath) != text:
        stale.append("codex-manifest.json")
        if not check:
            with open(mpath, "w", encoding="utf-8") as f:
                f.write(text)
    if check:
        if stale:
            print("Codex 版和源文件不一致，请运行 python3 tools/sync_codex.py：%s" % "、".join(stale))
            sys.exit(1)
        print("一致。")
    else:
        print("已同步：%s" % ("、".join(stale) if stale else "没有变化"))


if __name__ == "__main__":
    main()
