#!/usr/bin/env python3
"""把 codex/ 打成可转发的安装包（Codex 版）。用法：python3 installer/codex/打包.py
生成的压缩包在 dist/ 目录下。打包前会先同步共用源码并重写下载清单。"""
import json
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(REPO, "codex")
DOCS = os.path.join(REPO, "docs", "codex")
DIST = os.path.join(REPO, "dist")

# 两个平台共用一个版本号，来源是仓库根目录的 VERSION。这里顺手把各处对齐，并同步共用源码。
subprocess.run([sys.executable, os.path.join(REPO, "tools", "bump_version.py")], check=True)
VERSION = json.load(open(os.path.join(SRC, ".codex-plugin", "plugin.json"), encoding="utf-8"))["version"]
NAME = "quality-workflow-codex-%s" % VERSION
OUT = os.path.join(DIST, NAME)

shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(os.path.join(OUT, "marketplace", "plugins"))
shutil.copytree(SRC, os.path.join(OUT, "marketplace", "plugins", "quality-workflow"), ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"))
for name in ("install.sh", "install.ps1"):
    shutil.copy(os.path.join(HERE, name), os.path.join(OUT, name))
for name in sorted(os.listdir(DOCS)):
    if name.endswith(".md"):
        shutil.copy(os.path.join(DOCS, name), os.path.join(OUT, name))

zpath = OUT + ".zip"
if os.path.exists(zpath):
    os.remove(zpath)
with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
    for d, _, files in os.walk(OUT):
        for fn in files:
            full = os.path.join(d, fn)
            z.write(full, os.path.relpath(full, DIST))
print("已生成：%s" % zpath)
