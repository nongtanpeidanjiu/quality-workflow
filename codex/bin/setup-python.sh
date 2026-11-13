#!/bin/sh
# 检测 Python；找不到就自动装一份免安装版到 ~/.quality-workflow/python/。
# 不改系统设置，不需要管理员权限。只用系统自带的 sh、curl、tar、shasum。
# 用法：sh "<插件目录>/bin/setup-python.sh"
DIR=$(cd "$(dirname "$0")" && pwd)
QF_FRESH=1
. "$DIR/find-python.sh"
if [ -n "$PY" ]; then echo "Python 可用（已有）：$PY"; exit 0; fi

# QFLOW_NO_AUTO_PYTHON=1 时不自动下载（自动测试用）。
if [ "$QFLOW_NO_AUTO_PYTHON" = "1" ]; then echo "没有找到 Python，已设置为不自动安装。"; exit 1; fi

# 固定版本和校验值，下载后核对，不一致就不用。
TAG="20261110"
PYV="3.12.16"
case "$(uname -s)-$(uname -m)" in
  Darwin-arm64) TRIPLE="aarch64-apple-darwin"; SUM="2f11b8aa3726bdbd87dd9eb76568412faddf4f47e26913fa45d6a2e424972acd" ;;
  Darwin-x86_64) TRIPLE="x86_64-apple-darwin"; SUM="b78a4c479f1964ec484880bee567568266f68da102269fb93a05fe8716b45f91" ;;
  *) TRIPLE="" ;;
esac

manual() {
  echo "没能自动装好 Python。请按下面任意一种办法安装，装好后再运行一次本命令："
  echo "  - 下载安装：https://www.python.org/downloads/macos/ （下载 macOS 64-bit universal2 installer，双击安装）"
  echo "  - 国内下载较快的地址：https://registry.npmmirror.com/binary.html?path=python/3.12.10/ （选 python-3.12.10-macos11.pkg）"
  echo "  - 装了 Homebrew 的话，也可以运行：brew install python@3.12"
  exit 1
}

if [ -z "$TRIPLE" ]; then echo "这个系统（$(uname -s)-$(uname -m)）没有对应的免安装版 Python。"; manual; fi
FILE="cpython-$PYV+$TAG-$TRIPLE-install_only_stripped.tar.gz"
ENC="cpython-$PYV%2B$TAG-$TRIPLE-install_only_stripped.tar.gz"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/qflow-python.XXXXXX") || manual
trap 'rm -rf "$TMP"' EXIT
CURL=$(command -v curl 2>/dev/null || echo /usr/bin/curl)
TAR=$(command -v tar 2>/dev/null || echo /usr/bin/tar)
OK=""
for URL in "https://registry.npmmirror.com/-/binary/python-build-standalone/$TAG/$ENC" \
           "https://github.com/astral-sh/python-build-standalone/releases/download/$TAG/$ENC"; do
  echo "正在下载 Python $PYV：$(echo "$URL" | cut -d/ -f3)"
  rm -f "$TMP/python.tar.gz"
  if ! "$CURL" -fsSL --max-time 300 -o "$TMP/python.tar.gz" "$URL"; then echo "下载没有成功，换下一个地址。"; continue; fi
  GOT=$(/usr/bin/shasum -a 256 "$TMP/python.tar.gz" 2>/dev/null | cut -d' ' -f1)
  if [ "$GOT" != "$SUM" ]; then echo "文件校验不一致，丢弃，换下一个地址。"; continue; fi
  OK=1; break
done
[ -n "$OK" ] || manual
mkdir -p "$TMP/out" && "$TAR" -xzf "$TMP/python.tar.gz" -C "$TMP/out" || { echo "解压没有成功。"; manual; }
DEST="${QFLOW_HOME:-$HOME/.quality-workflow}/python"
rm -rf "$DEST" && mkdir -p "$(dirname "$DEST")" && mv "$TMP/out/python" "$DEST" || { echo "放到 $DEST 时出错。"; manual; }
QF_FRESH=1
qf_find_python
if [ -n "$PY" ]; then echo "Python 可用（已自动安装到 $DEST）：$PY"; exit 0; fi
echo "装好的 Python 无法运行。"; manual
