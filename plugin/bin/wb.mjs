// 操作 WorkBuddy 插件管理的公共函数：安装脚本（install.mjs）和插件内的更新命令（update.mjs）共用。
// 只用 Node 自带模块，Mac 和 Windows 通用。
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";

export const MARKET = "quality-workflow-market";
export const PLUGIN = "quality-workflow";
export const KEY = `${PLUGIN}@${MARKET}`;
// 自动更新从这个 GitHub 仓库拉取。仓库根目录的 .codebuddy-plugin/marketplace.json 就是市场清单。
export const GITHUB_REPO = "nongtanpeidanjiu/quality-workflow";
const IS_WIN = process.platform === "win32";

export function readJson(p) {
  try { return JSON.parse(fs.readFileSync(p, "utf8").replace(/^﻿/, "")); } catch { return null; }
}

export function configDir() {
  return process.env.WORKBUDDY_CONFIG_DIR || path.join(os.homedir(), ".workbuddy");
}

const settingsFile = () => path.join(configDir(), "settings.json");
const marketsFile = () => path.join(configDir(), "plugins", "known_marketplaces.json");
const installedFile = () => path.join(configDir(), "plugins", "installed_plugins.json");

export function installedRecord() {
  return readJson(installedFile())?.plugins?.[KEY]?.[0] || null;
}

export function marketRecord() {
  return readJson(marketsFile())?.[MARKET] || null;
}

// WorkBuddy 内置引擎（codebuddy-lite-wb.mjs）的位置。
export function findEngine() {
  const rel = ["resources", "app.asar.unpacked", "cli", "dist", "codebuddy-lite-wb.mjs"];
  const list = [process.env.QFLOW_WB_ENGINE];
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
  return list.filter(Boolean).find((p) => fs.existsSync(p)) || null;
}

// 只传系统必需的变量。WorkBuddy 会话里的变量（如 SERVER__PORT、代理设置）会让引擎
// 去占用主程序正在用的端口，或者在解析代理时报错，所以一律不带进去。
const KEEP = ["PATH", "Path", "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
  "SystemRoot", "SYSTEMROOT", "windir", "ComSpec", "PATHEXT", "TEMP", "TMP", "TMPDIR", "USER", "USERNAME", "LOGNAME",
  "LANG", "LC_ALL", "LC_CTYPE", "SHELL"];

export function makeRunner(engine) {
  const env = {};
  for (const k of KEEP) if (process.env[k] !== undefined) env[k] = process.env[k];
  // Mac 上引擎要用 /usr/sbin 里的 ioreg 读取设备信息，命令路径里没有时补上。
  if (!IS_WIN && !(env.PATH || "").split(":").includes("/usr/sbin")) env.PATH = `${env.PATH || "/usr/bin:/bin"}:/usr/sbin:/sbin`;
  Object.assign(env, { CODEBUDDY_CONFIG_DIR: configDir(), WORKBUDDY_CONFIG_DIR: configDir() });
  return function wb(...args) {
    const r = spawnSync(process.execPath, [engine, "plugin", ...args], {
      encoding: "utf8", windowsHide: true, timeout: 180000, input: "", env, cwd: os.tmpdir(),
    });
    return { ok: r.status === 0, out: `${r.stdout || ""}${r.stderr || ""}`.trim() };
  };
}

const sleep = (ms) => Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);

// 改 WorkBuddy 的一个 JSON 配置文件。文件被占用时重试；解析不了就不动它。
function patchJson(file, change) {
  let raw = "{}";
  try { raw = fs.readFileSync(file, "utf8"); } catch (e) { if (e.code !== "ENOENT") throw e; }
  const data = JSON.parse(raw.replace(/^﻿/, "") || "{}");
  change(data);
  fs.writeFileSync(file, `${JSON.stringify(data, null, 2)}\n`);
}

export const isEnabled = () => readJson(settingsFile())?.enabledPlugins?.[KEY] === true;
export const isAutoUpdate = () => marketRecord()?.autoUpdate === true;

// 启用插件。只有设置里明确写着 true 才算启用：没有这一项时 WorkBuddy 不会加载插件。
export function ensureEnabled(wb) {
  let error = "";
  for (let i = 0; i < 8 && !isEnabled(); i += 1) {
    if (i) sleep(1000);
    const en = wb("enable", KEY);
    if (isEnabled()) break;
    if (!en.ok) error = en.out;
    try {
      patchJson(settingsFile(), (d) => { d.enabledPlugins = { ...(d.enabledPlugins || {}), [KEY]: true }; });
    } catch (e) { error = `${e.code || ""} ${e.message}`.trim(); }
  }
  return { ok: isEnabled(), error };
}

// 打开市场的自动更新。插件命令没有这个选项，所以直接写市场记录，字段和 WorkBuddy 界面里的开关是同一个。
export function ensureAutoUpdate() {
  let error = "";
  for (let i = 0; i < 8 && marketRecord() && !isAutoUpdate(); i += 1) {
    if (i) sleep(1000);
    try {
      patchJson(marketsFile(), (d) => { if (d[MARKET]) d[MARKET].autoUpdate = true; });
    } catch (e) { error = `${e.code || ""} ${e.message}`.trim(); }
  }
  return { ok: isAutoUpdate(), error };
}

// 设置文件一直被占用时，留一个后台小程序，在接下来 30 分钟里等文件可写再补上启用和自动更新。
export function fixLater() {
  try {
    const dir = path.join(os.homedir(), ".quality-workflow");
    fs.mkdirSync(dir, { recursive: true });
    const helper = path.join(dir, "enable-later.mjs");
    fs.writeFileSync(helper, [
      'import fs from "node:fs";',
      `const settings = ${JSON.stringify(settingsFile())}, markets = ${JSON.stringify(marketsFile())};`,
      `const key = ${JSON.stringify(KEY)}, market = ${JSON.stringify(MARKET)};`,
      "const end = Date.now() + 30 * 60 * 1000;",
      "const patch = (file, change) => {",
      "  try {",
      '    const data = JSON.parse(fs.readFileSync(file, "utf8").replace(/^\\uFEFF/, "") || "{}");',
      '    if (change(data)) fs.writeFileSync(file, JSON.stringify(data, null, 2) + "\\n");',
      "  } catch { /* 还被占用，下次再试 */ }",
      "};",
      "const tick = () => {",
      "  patch(settings, (d) => { if (d.enabledPlugins?.[key] === true) return false; d.enabledPlugins = { ...(d.enabledPlugins || {}), [key]: true }; return true; });",
      "  patch(markets, (d) => { if (!d[market] || d[market].autoUpdate === true) return false; d[market].autoUpdate = true; return true; });",
      "  if (Date.now() < end) setTimeout(tick, 3000);",
      "};",
      "tick();",
    ].join("\n"));
    spawn(process.execPath, [helper], { detached: true, stdio: "ignore", windowsHide: true }).unref();
    return true;
  } catch { return false; }
}

// 卸载本插件的旧版本和旧市场（包括 quality-workflow-workbuddy 试用版）。
export function removeOld(wb, log = console.log) {
  let removed = 0;
  const installed = readJson(installedFile())?.plugins || {};
  for (const key of Object.keys(installed)) {
    if (/^quality-workflow(-workbuddy)?@/.test(key)) {
      const r = wb("uninstall", key);
      log(`  ${r.ok ? "已卸载旧版本" : "卸载旧版本未成功"}：${key}`);
      removed += 1;
    }
  }
  const markets = readJson(marketsFile()) || {};
  for (const name of Object.keys(markets)) {
    if (name === MARKET || /^quality-workflow-workbuddy/.test(name)) {
      const r = wb("marketplace", "remove", name);
      log(`  ${r.ok ? "已移除旧市场" : "移除旧市场未成功"}：${name}`);
      removed += 1;
    }
  }
  return removed;
}

// 从 GitHub 添加市场并安装。成功返回 { ok: true }，失败返回原因，调用方决定是否退回本地安装。
export function installFromGithub(wb) {
  const add = wb("marketplace", "add", GITHUB_REPO);
  if (!add.ok || !marketRecord()) return { ok: false, out: add.out };
  const inst = wb("install", KEY);
  if (!inst.ok || !installedRecord()) {
    wb("marketplace", "remove", MARKET);
    return { ok: false, out: inst.out };
  }
  return { ok: true };
}

export function installFromDir(wb, dir) {
  const v = wb("validate", dir);
  if (!v.ok) return { ok: false, out: `插件包校验未通过：\n${v.out}` };
  const add = wb("marketplace", "add", dir);
  if (!add.ok) return { ok: false, out: `添加市场失败：\n${add.out}` };
  const inst = wb("install", KEY);
  if (!inst.ok || !installedRecord()) return { ok: false, out: `安装失败：\n${inst.out}` };
  return { ok: true };
}

// 比较版本号 a.b.c，a 比 b 新返回正数。
export function compareVersion(a, b) {
  const pa = String(a).split(".").map((x) => parseInt(x, 10) || 0);
  const pb = String(b).split(".").map((x) => parseInt(x, 10) || 0);
  for (let i = 0; i < Math.max(pa.length, pb.length); i += 1) {
    const d = (pa[i] || 0) - (pb[i] || 0);
    if (d) return d;
  }
  return 0;
}
