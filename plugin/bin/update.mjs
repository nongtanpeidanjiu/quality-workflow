// 把插件更新到 GitHub 上的最新版本：node "<插件目录>/bin/update.mjs"
// 用户说“更新插件”时由智能体运行。原来是从压缩包安装的，会顺便改成从 GitHub 安装并打开自动更新。
import { VERSION } from "./lib.mjs";
import * as W from "./wb.mjs";

function done(code, lines) {
  console.log(lines.join("\n"));
  process.exit(code);
}

const engine = W.findEngine();
if (!engine) done(1, ["更新没有完成：找不到 WorkBuddy 的安装位置。", "可以从 GitHub 的 Releases 页面下载最新的安装包，按《快速安装》重新安装。"]);
const wb = W.makeRunner(engine);

console.log(`当前版本：${VERSION}。正在从 GitHub（${W.GITHUB_REPO}）获取最新版本，可能要等一两分钟……`);
const market = W.marketRecord();
let ok = false;
let error = "";
if (market && market.type !== "directory") {
  // 已经是 GitHub 市场：同步市场，再升级插件。
  const m = wb("marketplace", "update", W.MARKET);
  const u = m.ok ? wb("update", W.KEY) : m;
  ok = m.ok && u.ok;
  error = u.out;
} else {
  // 原来是从压缩包装的：换成 GitHub 市场。GitHub 装不上时，用原来的文件装回去。
  const oldDir = market?.installLocation || market?.source?.path;
  W.removeOld(wb, () => {});
  const g = W.installFromGithub(wb);
  ok = g.ok;
  error = g.out || "";
  if (!ok && oldDir) W.installFromDir(wb, oldDir);
}

if (!ok) {
  done(1, [
    "更新没有完成：连不上 GitHub，或者 GitHub 上的市场同步失败。原来的版本没有受影响。",
    "可以稍后再试，或者从 GitHub 的 Releases 页面下载最新的安装包，按《快速安装》重新安装。",
    process.env.QFLOW_DEBUG === "1" ? `原因：${error}` : "",
  ].filter(Boolean));
}

const now = W.installedRecord()?.version || "未知";
const en = W.ensureEnabled(wb);
const au = W.ensureAutoUpdate();
if (!en.ok || !au.ok) W.fixLater();
done(0, [
  W.compareVersion(now, VERSION) > 0 ? `更新成功：${VERSION} → ${now}。` : `已经是最新版本：${now}。`,
  au.ok ? "自动更新：已开启。" : "自动更新：WorkBuddy 正在占用设置文件，会在你退出 WorkBuddy 时自动补上。",
  en.ok ? "" : "注意：插件的启用状态没能写入，重启后请到“专家·技能·连接器 → 技能 → 套件”确认 quality-workflow 的开关是打开的。",
  "请用户完全退出并重新打开 WorkBuddy，新版本在新建的任务里生效。",
].filter(Boolean));
