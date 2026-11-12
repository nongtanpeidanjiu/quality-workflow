// WorkBuddy hook 入口：node "<插件目录>/bin/hook.mjs"
// zsh、Git Bash、PowerShell 下写法相同，所以 Mac 和 Windows 共用一份配置。
import fs from "node:fs";
import path from "node:path";
import { PLUGIN_ROOT, findPython, projectDir, runPython, recordHeartbeat, doctorPassed } from "./lib.mjs";

function out(obj) {
  if (obj) process.stdout.write(JSON.stringify(obj));
  process.exit(0);
}

function readStdin() {
  try { return fs.readFileSync(0, "utf8"); } catch { return ""; }
}

const raw = readStdin();
let data;
try { data = JSON.parse(raw); } catch { out(); }
const event = data.hook_event_name;
recordHeartbeat(event);

// 系统自动续跑的“用户消息”不是用户说的话，不能记进原话记录。
if (event === "UserPromptSubmit" && data.is_internal_continuation === true) out();

const root = projectDir(data.cwd);
const flowRoot = process.env.QFLOW_DIR ? path.resolve(process.env.QFLOW_DIR) : path.join(root, ".flow");
const python = findPython();

if (!python) {
  const msg = `【quality-workflow】流程关卡没有生效：找不到可用的 Python。请先运行 node "${path.join(PLUGIN_ROOT, "bin", "setup-python.mjs")}"，它会自动装好一份免安装版 Python（不改系统设置）。装好后新建任务即可。`;
  if (event === "SessionStart") out({ systemMessage: msg, hookSpecificOutput: { hookEventName: "SessionStart", additionalContext: msg } });
  // 已经建档的任务，关卡失效时不放行写入。
  if (event === "PreToolUse" && fs.existsSync(path.join(flowRoot, "tasks"))) {
    out({ hookSpecificOutput: { hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: msg } });
  }
  out();
}

const r = runPython(python, path.join(PLUGIN_ROOT, "hooks", "hook_handler.py"), [], {
  input: raw, cwd: root, env: { CLAUDE_PROJECT_DIR: root },
});
let stdout = (r.stdout || "").trim();
if (r.stderr) process.stderr.write(r.stderr);

if (r.status !== 0 || r.error) {
  const msg = "【quality-workflow】流程关卡运行出错，请让智能体运行插件的 doctor 检查。";
  if (event === "PreToolUse" && fs.existsSync(path.join(flowRoot, "tasks"))) {
    out({ hookSpecificOutput: { hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: msg } });
  }
  if (event === "SessionStart") out({ systemMessage: msg });
  out();
}

// 首次安装后还没通过 doctor：每次开新任务都提醒。
if (event === "SessionStart" && !doctorPassed()) {
  let obj = {};
  try { obj = stdout ? JSON.parse(stdout) : {}; } catch { obj = {}; }
  const spec = obj.hookSpecificOutput || { hookEventName: "SessionStart" };
  const doctor = `node "${path.join(PLUGIN_ROOT, "bin", "doctor.mjs")}"`;
  const note = `【quality-workflow】插件还没有通过安装自检。开始任何工作前，先运行：${doctor}。有问题就按它给出的建议修好环境，再运行一次，直到全部通过。`;
  spec.additionalContext = note + "\n" + (spec.additionalContext || "");
  obj.hookSpecificOutput = spec;
  obj.systemMessage = "quality-workflow 插件还没有完成安装自检，智能体会先运行自检。";
  stdout = JSON.stringify(obj);
}

if (stdout) process.stdout.write(stdout);
process.exit(0);
