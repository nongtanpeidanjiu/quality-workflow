#!/bin/sh
# quality-workflow（Codex 版）一键安装，Mac 用：sh install.sh
# 按“电脑上什么都没有”来设计，只用系统自带的 sh、curl、tar：
# 先检测环境，缺 Python 就自动装一份免安装版（不改系统设置，不要管理员权限），再安装插件。
# 不需要 git、Node，也不要求命令路径里有 codex。
HERE=$(cd "$(dirname "$0")" && pwd)
REPO="nongtanpeidanjiu/quality-workflow"
PLUGIN_SRC="$HERE/marketplace/plugins/quality-workflow"
CLEAN=""

if [ -f "$PLUGIN_SRC/bin/qf_install.py" ]; then
  ROOT="$PLUGIN_SRC"
  FROM="$HERE/marketplace"
else
  # 没有随安装包一起来：把安装程序本身先下载下来。
  echo "正在下载安装程序……"
  ROOT=$(mktemp -d "${TMPDIR:-/tmp}/qflow-bootstrap.XXXXXX") || { echo "安装没有完成：无法创建临时文件夹。"; exit 1; }
  CLEAN="$ROOT"
  FROM=""
  CURL=$(command -v curl 2>/dev/null || echo /usr/bin/curl)
  mkdir -p "$ROOT/bin" "$ROOT/.codex-plugin"
  for F in bin/find-python.sh bin/setup-python.sh bin/qf_install.py bin/qf_common.py .codex-plugin/plugin.json; do
    GOT=""
    for BASE in "https://raw.githubusercontent.com/$REPO/main/codex" "https://cdn.jsdelivr.net/gh/$REPO@main/codex"; do
      if "$CURL" -fsSL --max-time 60 -o "$ROOT/$F" "$BASE/$F" 2>/dev/null; then GOT=1; break; fi
    done
    if [ -z "$GOT" ]; then
      echo "安装没有完成：连不上 GitHub，镜像也连不上。请下载安装包（压缩包），解压后运行里面的 install.sh。"
      rm -rf "$CLEAN"; exit 1
    fi
  done
fi

echo "第 0 步：检测 Python"
. "$ROOT/bin/find-python.sh"
if [ -z "$PY" ]; then
  echo "  没有找到 Python，正在自动安装免安装版（不改系统设置）……"
  sh "$ROOT/bin/setup-python.sh" | sed 's/^/  /'
  QF_FRESH=1
  qf_find_python
fi
if [ -z "$PY" ]; then
  echo "安装没有完成：这台电脑没有可用的 Python，自动安装也没有成功。请按上面列出的办法装好 Python，再运行一次本脚本。"
  [ -n "$CLEAN" ] && rm -rf "$CLEAN"
  exit 1
fi
echo "  Python：$PY"
echo

if [ -n "$FROM" ]; then
  "$PY" -B "$ROOT/bin/qf_install.py" install --from "$FROM"
else
  "$PY" -B "$ROOT/bin/qf_install.py" install
fi
CODE=$?
[ -n "$CLEAN" ] && rm -rf "$CLEAN"
exit $CODE
