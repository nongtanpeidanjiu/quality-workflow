// 安装自检：node "<插件目录>/bin/doctor.mjs"
// 不修改插件和 WorkBuddy 的设置。缺 Python 时自动装一份免安装版到 ~/.quality-workflow/python/。全部通过后记录一次，之后不再提醒。
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { PLUGIN_ROOT, STATE_HOME, VERSION, runPython } from "./lib.mjs";
import { ensurePython } from "./setup-python.mjs";

const results = [];
function check(name, ok, detail, fix) {
  results.push({ name, ok, detail, fix });
}

// 1. Python
const setup = process.env.QFLOW_NO_AUTO_PYTHON === "1" ? { python: null, how: "", log: [], manual: ["已关闭自动安装（QFLOW_NO_AUTO_PYTHON=1）。"] } : await ensurePython();
const python = setup.python;
if (python) {
  const v = spawnSync(python, ["--version"], { encoding: "utf8", windowsHide: true });
  check("Python", true, `${(v.stdout || v.stderr || "").trim() || "可以运行"}（${setup.how}：${python}）`);
} else {
  check("Python", false, `找不到 Python，自动安装也没有成功。${setup.log.join(" ")}`,
    `请按下面任意一种办法安装，装好后重新运行 doctor：${setup.manual.join("；")}`);
}

// 2. 插件文件
const required = [
  ".codebuddy-plugin/plugin.json", "hooks/hooks.json", "hooks/hook_handler.py", "bin/hook.mjs", "bin/qflow.mjs", "bin/lib.mjs",
  "bin/setup-python.mjs", "scripts/flow.py", "scripts/flowlib.py", "agents/independent-checker.md", "skills/quality-workflow/SKILL.md",
  "skills/quality-workflow/assets/任务单模板.md",
];
const missing = required.filter((f) => !fs.existsSync(path.join(PLUGIN_ROOT, f)));
check("插件文件", missing.length === 0, missing.length ? `缺少：${missing.join("、")}` : `完整（版本 ${VERSION}）`,
  missing.length ? "插件包不完整。请重新完整解压安装包，再在 WorkBuddy 里重新安装插件。" : undefined);

// 3. 关卡逻辑：在临时目录里模拟一次“说定前写文件”和一次难以收回的命令
if (python && missing.length === 0) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "qflow-doctor-"));
  try {
    const env = { CLAUDE_PROJECT_DIR: tmp };
    const s = runPython(python, path.join(PLUGIN_ROOT, "scripts", "flow.py"), ["start", "--title", "doctor"], { cwd: tmp, env });
    const hook = (payload) => spawnSync(process.execPath, [path.join(PLUGIN_ROOT, "bin", "hook.mjs")], {
      input: JSON.stringify({ session_id: "doctor", cwd: tmp, ...payload }), encoding: "utf8", windowsHide: true,
      env: { ...process.env, CODEBUDDY_PROJECT_DIR: tmp, QFLOW_NO_HEARTBEAT: "1" },
    });
    const decision = (r) => { try { return JSON.parse(r.stdout).hookSpecificOutput.permissionDecision; } catch { return null; } };
    const w = hook({ hook_event_name: "PreToolUse", tool_name: "Write", tool_input: { file_path: path.join(tmp, "docs", "a.md"), content: "x" } });
    const b = hook({ hook_event_name: "PreToolUse", tool_name: "Bash", tool_input: { command: "git push --force origin main" } });
    const ok = s.status === 0 && decision(w) === "deny" && decision(b) === "ask";
    check("关卡逻辑", ok,
      ok ? "说定前写文件被拦下，难以收回的命令会询问用户" : `建档=${s.status}，写文件=${decision(w)}，难以收回的命令=${decision(b)}；${(s.stderr || w.stderr || "").trim().slice(0, 300)}`,
      ok ? undefined : "把这段输出原样发给插件提供者。不要修改插件文件让它通过。");
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
}

// 4. WorkBuddy 是否真的在调用插件的 hook
let beat = {};
try { beat = JSON.parse(fs.readFileSync(path.join(STATE_HOME, "hook-heartbeat.json"), "utf8")); } catch { /* 还没有 */ }
const recent = Object.entries(beat).filter(([, v]) => v.version === VERSION && Date.now() - v.ts < 6 * 3600 * 1000).map(([k]) => k);
const live = recent.includes("UserPromptSubmit") || recent.includes("SessionStart");
check("WorkBuddy 调用插件", live,
  live ? `最近 6 小时收到：${recent.join("、")}` : "没有收到 WorkBuddy 的调用记录。",
  live ? undefined : "确认插件已在 WorkBuddy 的插件管理里安装并启用，然后新建一个任务，在新任务里再运行一次 doctor。如果仍未通过，重启 WorkBuddy 后再试。");

// 5. 旧版插件冲突
const settingsFile = path.join(process.env.WORKBUDDY_CONFIG_DIR || path.join(os.homedir(), ".workbuddy"), "settings.json");
let conflicts = [];
try {
  const enabled = JSON.parse(fs.readFileSync(settingsFile, "utf8")).enabledPlugins || {};
  conflicts = Object.entries(enabled).filter(([k, v]) => v === true && /quality-workflow/.test(k) && !k.startsWith("quality-workflow@")).map(([k]) => k);
} catch { /* 读不到设置就跳过 */ }
check("旧版插件", conflicts.length === 0, conflicts.length ? `同时启用了：${conflicts.join("、")}` : "没有冲突",
  conflicts.length ? "请用户在 WorkBuddy 的插件管理里停用并卸载这些旧版本，再新建任务运行 doctor。" : undefined);

// 输出
const passed = results.every((r) => r.ok);
const lines = ["quality-workflow 安装自检", ""];
for (const r of results) {
  lines.push(`${r.ok ? "通过" : "未通过"}｜${r.name}：${r.detail}`);
  if (!r.ok && r.fix) lines.push(`    怎么修：${r.fix}`);
}
lines.push("");
if (passed) {
  fs.mkdirSync(STATE_HOME, { recursive: true });
  fs.writeFileSync(path.join(STATE_HOME, "doctor-ok.json"), JSON.stringify({ version: VERSION, ts: Date.now(), platform: process.platform, python }, null, 2));
  lines.push("全部通过。插件已经生效。");
} else {
  lines.push("没有全部通过。修复时只能处理环境（例如安装 Python、启用插件、重启 WorkBuddy）；不要修改插件的规则文件、hook 配置或任务记录来让检查通过。修好后再运行一次 doctor。");
}
console.log(lines.join("\n"));
process.exit(passed ? 0 : 1);
