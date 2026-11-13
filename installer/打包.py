#!/usr/bin/env python3
"""把 plugin/ 打成可转发的 WorkBuddy 安装包。用法：python3 installer/打包.py
生成的压缩包在 dist/ 目录下。"""
import json
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SRC = os.path.join(REPO, "plugin")
DOCS = os.path.join(REPO, "docs")
DIST = os.path.join(REPO, "dist")
VERSION = json.load(open(os.path.join(SRC, ".codebuddy-plugin", "plugin.json"), encoding="utf-8"))["version"]
NAME = "quality-workflow-%s" % VERSION
OUT = os.path.join(DIST, NAME)
SKIP = {"tests", "__pycache__", ".DS_Store"}

# 两个平台共用一个版本号，来源是仓库根目录的 VERSION。各处不一致时不打包。
subprocess.run([sys.executable, os.path.join(REPO, "tools", "bump_version.py"), "--check"], check=True)

shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(DIST, exist_ok=True)
plugin_dst = os.path.join(OUT, "marketplace", "plugins", "quality-workflow")
shutil.copytree(SRC, plugin_dst, ignore=lambda d, names: [n for n in names if n in SKIP])
os.makedirs(os.path.join(OUT, "marketplace", ".codebuddy-plugin"))
with open(os.path.join(OUT, "marketplace", ".codebuddy-plugin", "marketplace.json"), "w", encoding="utf-8") as f:
    json.dump({
        "name": "quality-workflow-market",
        "owner": {"name": "通用工作流程"},
        "plugins": [{"name": "quality-workflow", "source": "./plugins/quality-workflow", "version": VERSION,
                     "description": "先找出决定结果的关键项，先验证再使用，做完逐项核对证据，如实交付。Mac 和 Windows 通用。"}],
    }, f, ensure_ascii=False, indent=2)
for name in ("给智能体的安装步骤.md", "升级说明.md", "快速安装.md", "安装说明.md"):
    shutil.copy(os.path.join(DOCS, name), os.path.join(OUT, name))
shutil.copy(os.path.join(HERE, "install.mjs"), os.path.join(OUT, "install.mjs"))
subprocess.run([sys.executable, os.path.join(HERE, "生成网页说明.py"), OUT], check=True)

zpath = OUT + ".zip"
if os.path.exists(zpath):
    os.remove(zpath)
with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
    for d, _, files in os.walk(OUT):
        for fn in files:
            full = os.path.join(d, fn)
            z.write(full, os.path.relpath(full, DIST))
print("已生成：%s" % zpath)
