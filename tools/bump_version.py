#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""改版本号。WorkBuddy 版和 Codex 版共用一个版本号，唯一来源是仓库根目录的 VERSION 文件。

  python3 tools/bump_version.py 0.5.1    把版本号改成 0.5.1
  python3 tools/bump_version.py          不改版本号，只把 VERSION 里的版本写到各处（修复不一致时用）
  python3 tools/bump_version.py --check  只检查各处是否一致，不一致时退出码为 1（自动测试用）

会改：VERSION、两个插件的清单、WorkBuddy 的市场清单、安装文档里的版本号，然后同步 Codex 版并重写下载清单。
不会改：《升级说明》和《设计规格》里的历史版本号，那些要手写。发版时记得在《升级说明》里加上新版本的变化。
"""
import json
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION_FILE = os.path.join(REPO, "VERSION")
MANIFESTS = [
    ("plugin/.codebuddy-plugin/plugin.json", lambda d: d, "version"),
    ("codex/.codex-plugin/plugin.json", lambda d: d, "version"),
    (".codebuddy-plugin/marketplace.json", lambda d: d["plugins"][0], "version"),
]
DOCS = ["README.md", "docs/安装说明.md", "docs/快速安装.md", "docs/给智能体的安装步骤.md",
        "docs/codex/安装说明.md", "docs/codex/给智能体的安装步骤.md"]
VERSION_RE = r"\d+\.\d+\.\d+"
# 文档里出现版本号的几种写法。只换这些位置，避免误伤正文里别的数字。
DOC_PATTERNS = [
    r"(quality-workflow(?:-codex)?-)%s" % VERSION_RE,       # 安装包和文件夹的名字
    r"(quality-workflow )%s( 已安装)" % VERSION_RE,           # 安装成功的提示
    r"(^版本 )%s" % VERSION_RE,                               # 文档开头的版本行
    r"(插件版本：)%s" % VERSION_RE,
    r"(版本号是 \*\*)%s" % VERSION_RE,
]


def path(rel):
    return os.path.join(REPO, *rel.split("/"))


def read_version():
    with open(VERSION_FILE, encoding="utf-8") as f:
        return f.read().strip()


def doc_text(text, version):
    for pat in DOC_PATTERNS:
        text = re.sub(pat, lambda m: m.group(1) + version + (m.group(2) if m.lastindex and m.lastindex > 1 else ""), text, flags=re.M)
    return text


def problems(version):
    out = []
    for rel, pick, key in MANIFESTS:
        with open(path(rel), encoding="utf-8") as f:
            got = pick(json.load(f)).get(key)
        if got != version:
            out.append("%s 里是 %s" % (rel, got))
    for rel in DOCS:
        with open(path(rel), encoding="utf-8") as f:
            text = f.read()
        if doc_text(text, version) != text:
            out.append("%s 里的版本号不是 %s" % (rel, version))
    return out


def apply(version):
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        f.write(version + "\n")
    for rel, pick, key in MANIFESTS:
        with open(path(rel), encoding="utf-8") as f:
            data = json.load(f)
        pick(data)[key] = version
        with open(path(rel), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
    for rel in DOCS:
        with open(path(rel), encoding="utf-8") as f:
            text = f.read()
        new = doc_text(text, version)
        if new != text:
            with open(path(rel), "w", encoding="utf-8") as f:
                f.write(new)


def main():
    args = sys.argv[1:]
    if "--check" in args:
        version = read_version()
        bad = problems(version)
        if bad:
            print("版本号不一致（VERSION 是 %s），请运行 python3 tools/bump_version.py：%s" % (version, "；".join(bad)))
            sys.exit(1)
        print("版本号一致：%s" % version)
        return
    version = args[0] if args else read_version()
    if not re.fullmatch(VERSION_RE, version):
        sys.exit("版本号要写成 1.2.3 这样的三段数字。")
    apply(version)
    subprocess.run([sys.executable, os.path.join(REPO, "tools", "sync_codex.py")], check=True)
    print("版本号已统一为 %s。发版前记得在 docs/升级说明.md 里写上这个版本的变化，并打标签 v%s。" % (version, version))


if __name__ == "__main__":
    main()
