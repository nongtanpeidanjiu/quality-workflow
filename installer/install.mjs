// quality-workflow 一键安装：node install.mjs
// 先检测环境（缺 Python 会自动装一份免安装版），卸载旧版本，再用 WorkBuddy 内置引擎的插件命令安装。
// Mac 和 Windows 通用。只用 Node 自带模块。
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SRC_MARKET = path.join(HERE, "marketplace");
const MARKET = "quality-workflow-market";
const PLUGIN = "quality-workflow";
const IS_WIN = process.platform === "win32";

function fail(msg) {
  console.log(`\n安装没有完成：${msg}`);
  console.log("可以改用安装说明里“手动安装”的方法：在 WorkBuddy 的“专家·技能·连接器 → 技能 → 套件”页面点“添加市场”。");
  process.exit(1);
}

function readJson(p) {
  try { return JSON.parse(fs.readFileSync(p, "utf8")); } catch { return null; }
}

// 1. 插件包
const pluginManifest = readJson(path.join(SRC_MARKET, "plugins", PLUGIN, ".codebuddy-plugin", "plugin.json"));
if (!pluginManifest || !fs.existsSync(path.join(SRC_MARKET, ".codebuddy-plugin", "marketplace.json"))) {
  fail("找不到插件文件。请确认压缩包已完整解压，install.mjs 和 marketplace 文件夹在同一个文件夹里。");
}
const VERSION = pluginManifest.version;

console.log(`正在安装 quality-workflow ${VERSION}`);
console.log("\n第 1 步：检测环境");

// Node：插件靠它启动。版本太旧时提示换用 WorkBuddy 自带的。
const nodeMajor = Number(process.versions.node.split(".")[0]);
if (nodeMajor < 18) {
  fail(`当前的 Node 版本是 ${process.versions.node}，太旧了。请改用 WorkBuddy 自带的 Node 运行本脚本：Mac 在 ~/.workbuddy/binaries/node/versions/<版本>/bin/node，Windows 在 %USERPROFILE%\\.workbuddy\\binaries\\node\\versions\\<版本>\\node.exe。`);
}
console.log(`  Node ${process.versions.node}：可用`);

// Python：找不到就自动装一份免安装版到用户目录下的 .quality-workflow/python，不改系统设置。
let pythonNote = "";
try {
  const { ensurePython } = await import(pathToFileURL(path.join(SRC_MARKET, "plugins", PLUGIN, "bin", "setup-python.mjs")).href);
  const r = await ensurePython();
  for (const line of r.log) console.log(`  ${line}`);
  if (r.python) {
    console.log(`  Python：可用（${r.how}）`);
  } else {
    console.log("  Python：没有找到，自动安装也没有成功。插件会照常安装，但要装好 Python 才能生效。");
    pythonNote = `还差一步：这台电脑没有可用的 Python。请按下面任意一种办法安装，装好后新建任务完成自检：\n${r.manual.map((m) => `  - ${m}`).join("\n")}`;
  }
} catch (e) {
  console.log(`  Python 检测出错：${e.message}。插件会照常安装，自检时会再检测一次。`);
}

// 2. WorkBuddy 的配置目录
const configDir = process.env.WORKBUDDY_CONFIG_DIR || path.join(os.homedir(), ".workbuddy");
if (!fs.existsSync(configDir)) fail(`找不到 WorkBuddy 的配置目录 ${configDir}。请确认这台电脑装了 WorkBuddy，并且至少打开过一次。`);

// 3. WorkBuddy 内置引擎
function findEngine() {
  const rel = ["resources", "app.asar.unpacked", "cli", "dist", "codebuddy-lite-wb.mjs"];
  const list = [process.env.QFLOW_WB_ENGINE];
  // 在 WorkBuddy 里运行时，它会告诉我们自己的安装位置。
  if (process.env.WORKBUDDY_RESOURCES_PATH) list.push(path.join(process.env.WORKBUDDY_RESOURCES_PATH, ...rel.slice(1)));
  if (process.env.WORKBUDDY_APP_PATH) {
    list.push(path.join(process.env.WORKBUDDY_APP_PATH, ...rel));
    list.push(path.join(process.env.WORKBUDDY_APP_PATH, "Contents", "Resources", ...rel.slice(1)));
  }
  if (IS_WIN) {
    for (const base of [process.env.LOCALAPPDATA && path.join(process.env.LOCALAPPDATA, "Programs"), process.env.ProgramFiles, process.env["ProgramFiles(x86)"]].filter(Boolean)) {
      for (const name of ["WorkBuddy", "workbuddy"]) list.push(path.join(base, name, ...rel));
      try {
        for (const d of fs.readdirSync(base)) if (/workbuddy/i.test(d)) list.push(path.join(base, d, ...rel));
      } catch { /* 目录不存在 */ }
    }
  } else {
    for (const app of ["/Applications/WorkBuddy.app", path.join(os.homedir(), "Applications", "WorkBuddy.app")]) {
      list.push(path.join(app, "Contents", "Resources", ...rel.slice(1)));
    }
  }
  return list.filter(Boolean).find((p) => fs.existsSync(p));
}
const engine = findEngine();
if (!engine) fail("找不到 WorkBuddy 的安装位置。如果 WorkBuddy 装在非默认位置，可以设置环境变量 QFLOW_WB_ENGINE 指向其中的 codebuddy-lite-wb.mjs 后重试。");

// 只传系统必需的变量。WorkBuddy 会话里的变量（如 SERVER__PORT、代理设置）会让引擎
// 去占用主程序正在用的端口，或者在解析代理时报错，所以一律不带进去。
const KEEP = ["PATH", "Path", "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
  "SystemRoot", "SYSTEMROOT", "windir", "ComSpec", "PATHEXT", "TEMP", "TMP", "TMPDIR", "USER", "USERNAME", "LOGNAME",
  "LANG", "LC_ALL", "LC_CTYPE", "SHELL"];
const cleanEnv = {};
for (const k of KEEP) if (process.env[k] !== undefined) cleanEnv[k] = process.env[k];
Object.assign(cleanEnv, { CODEBUDDY_CONFIG_DIR: configDir, WORKBUDDY_CONFIG_DIR: configDir });

function wb(...args) {
  const r = spawnSync(process.execPath, [engine, "plugin", ...args], {
    encoding: "utf8", windowsHide: true, timeout: 120000, input: "", env: cleanEnv,
  });
  return { ok: r.status === 0, out: `${r.stdout || ""}${r.stderr || ""}`.trim() };
}

console.log(`  WorkBuddy：已找到（配置目录 ${configDir}）`);

// 4. 卸载旧版本和旧市场（包括 quality-workflow-workbuddy 试用版）
console.log("\n第 2 步：卸载旧版本");
let removed = 0;
const installed = readJson(path.join(configDir, "plugins", "installed_plugins.json"))?.plugins || {};
for (const key of Object.keys(installed)) {
  if (/^quality-workflow(-workbuddy)?@/.test(key)) {
    const r = wb("uninstall", key);
    console.log(`  ${r.ok ? "已卸载旧版本" : "卸载旧版本未成功"}：${key}`);
    removed += 1;
  }
}
const markets = readJson(path.join(configDir, "plugins", "known_marketplaces.json")) || {};
for (const name of Object.keys(markets)) {
  if (name === MARKET || /^quality-workflow-workbuddy/.test(name)) {
    const r = wb("marketplace", "remove", name);
    console.log(`  ${r.ok ? "已移除旧市场" : "移除旧市场未成功"}：${name}`);
    removed += 1;
  }
}

if (!removed) console.log("  没有旧版本。");

// 5. 把插件复制到固定位置，删掉下载的文件也不影响使用
console.log("\n第 3 步：安装插件");
const target = path.join(os.homedir(), ".quality-workflow", `marketplace-${VERSION}`);
fs.rmSync(target, { recursive: true, force: true });
fs.mkdirSync(path.dirname(target), { recursive: true });
fs.cpSync(SRC_MARKET, target, { recursive: true });

// 6. 校验、添加市场、安装
const v = wb("validate", target);
if (!v.ok) fail(`插件包校验未通过：\n${v.out}`);
const add = wb("marketplace", "add", target);
if (!add.ok) fail(`添加市场失败：\n${add.out}`);
const inst = wb("install", `${PLUGIN}@${MARKET}`);
if (!inst.ok) fail(`安装失败：\n${inst.out}`);

// 7. 核对
const after = readJson(path.join(configDir, "plugins", "installed_plugins.json"))?.plugins?.[`${PLUGIN}@${MARKET}`];
const enabled = readJson(path.join(configDir, "settings.json"))?.enabledPlugins?.[`${PLUGIN}@${MARKET}`];
if (!after) fail("安装命令执行了，但安装记录里没有找到插件。");
if (enabled === false) {
  const en = wb("enable", `${PLUGIN}@${MARKET}`);
  if (!en.ok) fail(`插件已安装但没有启用：\n${en.out}`);
}

console.log(`  已安装并启用。`);
console.log(`\n安装成功：quality-workflow ${VERSION} 已安装并启用。`);
if (pythonNote) console.log(pythonNote);
console.log("下一步：请用户完全退出并重新打开 WorkBuddy，然后新建一个任务，随便发一句话。新任务会自动提醒运行安装自检，按提示操作直到自检全部通过。");
