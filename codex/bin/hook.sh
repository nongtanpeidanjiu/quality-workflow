#!/bin/sh
# Codex 钩子入口（Mac）。只用系统自带的 sh：找到 Python 后交给 codex_hook.py。
DIR=$(cd "$(dirname "$0")/.." && pwd)
. "$DIR/bin/find-python.sh"
if [ -z "$PY" ]; then
  IN=$(cat)
  case "$IN" in
    *'"SessionStart"'*)
      # 新任务开始时自动装一次，装好就接着往下走。
      sh "$DIR/bin/setup-python.sh" >/dev/null 2>&1
      QF_FRESH=1; qf_find_python
      if [ -n "$PY" ]; then printf '%s' "$IN" | "$PY" -B "$DIR/hooks/codex_hook.py"; exit $?; fi
      printf '%s' '{"systemMessage":"quality-workflow：找不到可用的 Python，流程关卡没有生效。","hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"【quality-workflow】流程关卡没有生效：找不到可用的 Python，自动安装也没有成功。请先运行 sh \"'"$DIR"'/bin/setup-python.sh\"，按它的提示装好 Python，再新建任务。"}}'
      ;;
    *'"PreToolUse"'*)
      # 已经建档的任务，关卡失效时不放行。
      if [ -d ".flow/tasks" ]; then
        printf '%s' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"【quality-workflow】流程关卡没有生效：找不到可用的 Python。请先运行 sh \"'"$DIR"'/bin/setup-python.sh\" 装好 Python。"}}'
      fi
      ;;
  esac
  exit 0
fi
exec "$PY" -B "$DIR/hooks/codex_hook.py"
