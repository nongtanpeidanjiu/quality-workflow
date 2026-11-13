# 找一个能用的 Python（3.8 以上），结果放在变量 PY 里；找不到时 PY 为空。
# 用法：. "<插件目录>/bin/find-python.sh"。只用系统自带的 sh。
QF_HOME="${QFLOW_HOME:-$HOME/.quality-workflow}"
QF_PY_CACHE="$QF_HOME/python-path.txt"

qf_py_ok() {
  [ -n "$1" ] && [ -x "$1" ] || return 1
  # Mac 没装命令行开发工具时，/usr/bin/python3 只是个会弹安装窗口的占位程序，不能用。
  if [ "$1" = "/usr/bin/python3" ] && [ "$(uname -s)" = "Darwin" ]; then
    /usr/bin/xcode-select -p >/dev/null 2>&1 || return 1
  fi
  "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1
}

qf_find_python() {
  PY=""
  if [ -z "$QF_FRESH" ] && [ -f "$QF_PY_CACHE" ]; then
    c=$(cat "$QF_PY_CACHE" 2>/dev/null)
    if [ -n "$c" ] && [ -x "$c" ]; then PY="$c"; return 0; fi
  fi
  # QFLOW_ONLY_PRIVATE_PYTHON=1 时不看系统里的 Python，用来模拟“电脑上什么都没有”。
  if [ "$QFLOW_ONLY_PRIVATE_PYTHON" = "1" ]; then
    set -- "$QFLOW_PYTHON" "$QF_HOME/python/bin/python3"
  else
    set -- "$QFLOW_PYTHON" "$QF_HOME/python/bin/python3" "$(command -v python3 2>/dev/null)" \
           /opt/homebrew/bin/python3 /usr/local/bin/python3 \
           /Library/Frameworks/Python.framework/Versions/Current/bin/python3 /usr/bin/python3
  fi
  for c in "$@"; do
    if qf_py_ok "$c"; then
      PY="$c"
      mkdir -p "$QF_HOME" 2>/dev/null && printf '%s' "$PY" > "$QF_PY_CACHE" 2>/dev/null
      return 0
    fi
  done
  return 1
}
qf_find_python
