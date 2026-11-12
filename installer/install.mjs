// quality-workflow 一键安装：node install.mjs
// 先检测环境（缺 Python 会自动装一份免安装版），卸载旧版本，再用 WorkBuddy 内置引擎的插件命令安装。
// 优先从 GitHub 安装并打开自动更新；连不上 GitHub 时，改用压缩包里的文件安装（不会自动更新）。
// Mac 和 Windows 通用。只用 Node 自带模块。
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SRC_MARKET = path.join(HERE, "marketplace");
const PLUGIN_DIR = path.join(SRC_MARKET, "plugins", "quality-workflow");
const IS_WIN = process.platform === "win32";

function fail(msg) {
  console.log(`\n安装没有完成：${msg}`);
  console.log("可以改用安装说明里“手动安装”的方法：在 WorkBuddy 的“专家·技能·连接器 → 技能 → 套件”页面点“添加市场”。");
  process.exit(1);
}

// 1. 插件包
let pluginManifest = null;
try { pluginManifest = JSON.parse(fs.readFileSync(path.join(PLUGIN_DIR, ".codebuddy-plugin", "plugin.json"), "utf8")); } catch { /* 下面报错 */ }
if (!pluginManifest || !fs.existsSync(path.join(SRC_MARKET, ".codebuddy-plugin", "marketplace.json"))) {
  fail("找不到插件文件。请确认压缩包已完整解压，install.mjs 和 marketplace 文件夹在同一个文件夹里。");
}
const nodeMajor = Number(process.versions.node.split(".")[0]);
if (nodeMajor < 18) {
  fail(`当前的 Node 版本是 ${process.versions.node}，太旧了。请改用 WorkBuddy 自带的 Node 运行本脚本：Mac 在 ~/.workbuddy/binaries/node/versions/<版本>/bin/node，Windows 在 %USERPROFILE%\\.workbuddy\\binaries\\node\\versions\\<版本>\\node.exe。`);
}
const W = await import(pathToFileURL(path.join(PLUGIN_DIR, "bin", "wb.mjs")).href);
const ZIP_VERSION = pluginManifest.version;

console.log(`正在安装 quality-workflow ${ZIP_VERSION}`);
console.log("\n第 1 步：检测环境");
console.log(`  Node ${process.versions.node}：可用`);

// Python：找不到就自动装一份免安装版到用户目录下的 .quality-workflow/python，不改系统设置。
let pythonNote = "";
try {
  const { ensurePython } = await import(pathToFileURL(path.join(PLUGIN_DIR, "bin", "setup-python.mjs")).href);
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

const configDir = W.configDir();
if (!fs.existsSync(configDir)) fail(`找不到 WorkBuddy 的配置目录 ${configDir}。请确认这台电脑装了 WorkBuddy，并且至少打开过一次。`);
const engine = W.findEngine();
if (!engine) fail("找不到 WorkBuddy 的安装位置。如果 WorkBuddy 装在非默认位置，可以设置环境变量 QFLOW_WB_ENGINE 指向其中的 codebuddy-lite-wb.mjs 后重试。");
const wb = W.makeRunner(engine);
console.log(`  WorkBuddy：已找到（配置目录 ${configDir}）`);

// 2. 卸载旧版本
console.log("\n第 2 步：卸载旧版本");
if (!W.removeOld(wb)) console.log("  没有旧版本。");

// 3. 安装：先试 GitHub（以后能自动更新），不行再用压缩包里的文件。
console.log("\n第 3 步：安装插件");
let fromGithub = false;
let githubError = "";
if (process.env.QFLOW_NO_GITHUB !== "1") {
  console.log(`  正在从 GitHub（${W.GITHUB_REPO}）安装，可能要等一两分钟……`);
  const g = W.installFromGithub(wb);
  fromGithub = g.ok;
  githubError = g.out || "";
  console.log(fromGithub ? "  已从 GitHub 安装。" : "  连不上 GitHub，改用压缩包里的文件安装。");
}
if (!fromGithub) {
  // 复制到固定位置，删掉下载的文件也不影响使用。
  const target = path.join(os.homedir(), ".quality-workflow", `marketplace-${ZIP_VERSION}`);
  fs.rmSync(target, { recursive: true, force: true });
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.cpSync(SRC_MARKET, target, { recursive: true });
  const d = W.installFromDir(wb, target);
  if (!d.ok) fail(d.out);
}
const rec = W.installedRecord();
if (!rec) fail("安装命令执行了，但安装记录里没有找到插件。");
const VERSION = rec.version || ZIP_VERSION;

// 4. 启用、打开自动更新
const en = W.ensureEnabled(wb);
const au = fromGithub ? W.ensureAutoUpdate() : { ok: true };
if (!en.ok || !au.ok) {
  const later = W.fixLater();
  console.log("  已安装，但还没有启用。");
  console.log(`\n插件已安装，但没有启用：quality-workflow ${VERSION} 的文件已装好，启用状态没能写进 WorkBuddy 的设置文件。`);
  console.log(`原因：${en.error || au.error || "设置文件写入后没有生效"}。通常是 WorkBuddy 正在运行，占用了设置文件。`);
  console.log("请用户按下面做：");
  if (later) console.log("  1. 现在完全退出 WorkBuddy（不是只关窗口），等 10 秒再打开。安装程序会在 WorkBuddy 退出时自动补上启用状态，30 分钟内有效。");
  else console.log("  1. 完全退出并重新打开 WorkBuddy。");
  console.log("  2. 打开“专家·技能·连接器 → 技能 → 套件”，找到 quality-workflow，确认开关是打开的；没有打开就手动打开，再重启一次 WorkBuddy。");
  console.log("  3. 新建一个任务，随便发一句话。看到运行安装自检的提醒，说明插件已经生效，按提示完成自检。");
  if (pythonNote) console.log(pythonNote);
  process.exit(2);
}

console.log("  已安装并启用。");
console.log(fromGithub ? "  自动更新：已开启。以后发布新版本，WorkBuddy 会在启动时自动升级（每天最多检查一次）。"
  : "  自动更新：没有开启（这台电脑连不上 GitHub）。以后有新版本时，插件会在新任务里提醒，说“更新插件”即可升级。");
if (!fromGithub && githubError && process.env.QFLOW_DEBUG === "1") console.log(`  GitHub 安装失败的原因：${githubError}`);
console.log(`\n安装成功：quality-workflow ${VERSION} 已安装并启用。`);
if (pythonNote) console.log(pythonNote);
if (IS_WIN) console.log("Windows 用户请注意：重新打开 WorkBuddy 后，先到“专家·技能·连接器 → 技能 → 套件”确认 quality-workflow 的开关是打开的；没有打开就手动打开，再重启一次 WorkBuddy。");
console.log("下一步：请用户完全退出并重新打开 WorkBuddy，然后新建一个任务，随便发一句话。新任务会自动提醒运行安装自检，按提示操作直到自检全部通过。");
