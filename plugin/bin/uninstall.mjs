// 卸载 quality-workflow（WorkBuddy 版），并把装在插件目录之外的东西清干净：
//   node "<插件目录>/bin/uninstall.mjs" --quote "用户说要卸载的那句原话"
// 卸载要由用户决定：要么附上核对得到的用户原话，要么是用户本人在终端里运行。否则智能体可以自己卸载来绕开关卡。
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import readline from "node:readline";
import { PLUGIN_ROOT, STATE_HOME, findPython, projectDir, runPython } from "./lib.mjs";
import * as W from "./wb.mjs";

const args = process.argv.slice(2);
const qi = args.indexOf("--quote");
const quote = qi >= 0 ? (args[qi + 1] || "") : "";

function userSaid() {
  if (!quote) return false;
  const python = findPython();
  if (!python) return false;
  const root = projectDir();
  const r = runPython(python, path.join(PLUGIN_ROOT, "scripts", "flow.py"), ["quote-check", "--quote", quote], { cwd: root, env: { CLAUDE_PROJECT_DIR: root } });
  return r.status === 0;
}

async function confirmedInTerminal() {
  if (!process.stdin.isTTY || !process.stdout.isTTY) return false;
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  const answer = await new Promise((resolve) => rl.question("确认要卸载 quality-workflow 吗？输入 yes 继续：", resolve));
  rl.close();
  return ["yes", "y", "是"].includes(String(answer).trim().toLowerCase());
}

if (!userSaid() && !(await confirmedInTerminal())) {
  console.error('未通过：卸载要由用户决定。请加上 --quote "用户说要卸载的那句原话"（整条消息或其中完整的一句）。用户没有这样说过，就不要卸载。用户也可以自己在 WorkBuddy 的“专家·技能·连接器 → 技能 → 套件”里停用或卸载。');
  process.exit(2);
}

console.log("正在卸载 quality-workflow（WorkBuddy 版）\n");
const report = [];
const engine = W.findEngine();
if (engine) {
  const wb = W.makeRunner(engine);
  const n = W.removeOld(wb, () => {});
  report.push(["WorkBuddy 里的插件和市场", n ? "已卸载" : "本来就没有"]);
} else {
  report.push(["WorkBuddy 里的插件和市场", "没找到 WorkBuddy 程序，请在“专家·技能·连接器 → 技能 → 套件”里手动卸载"]);
}

// 用户目录下属于本插件的东西。Codex 版也用同一个文件夹，它的子目录 codex/ 和共用的 Python 要看情况。
const codexInstalled = fs.existsSync(path.join(STATE_HOME, "codex"));
const mine = ["hook-heartbeat.json", "doctor-ok.json", "latest.json", "python-path.txt", "enable-later.mjs", "direct", "recent-messages"];
let names = [];
try { names = fs.readdirSync(STATE_HOME); } catch { /* 文件夹不存在 */ }
const removed = [];
for (const name of names) {
  if (mine.includes(name) || /^marketplace-\d+\.\d+\.\d+$/.test(name)) {
    try { fs.rmSync(path.join(STATE_HOME, name), { recursive: true, force: true }); removed.push(name); } catch { /* 删不掉就留着 */ }
  }
}
report.push(["运行记录和旧版本副本", removed.length ? `已删除：${removed.join("、")}` : "本来就没有"]);
const python = path.join(STATE_HOME, "python");
if (fs.existsSync(python)) {
  if (codexInstalled) {
    report.push(["自动安装的免安装版 Python", "保留。Codex 版还在用它"]);
  } else {
    fs.rmSync(python, { recursive: true, force: true });
    report.push(["自动安装的免安装版 Python", "已删除"]);
  }
}
let left = [];
try { left = fs.readdirSync(STATE_HOME); } catch { /* 已经没有了 */ }
if (left.length === 0) {
  try { fs.rmdirSync(STATE_HOME); } catch { /* 留着也无妨 */ }
} else {
  report.push([`${STATE_HOME} 里剩下的`, `${left.join("、")}（${left.includes("codex") ? "codex 是 Codex 版的；" : ""}improvements.md 是你积累的改进记录，没有动）`]);
}

for (const [what, result] of report) console.log(`  ${what}：${result}`);
console.log("\n卸载完成。请完全退出并重新打开 WorkBuddy。");
console.log("没有删除的东西：各个项目里的 .flow 文件夹（任务记录），需要的话自己删。");
