// quality-workflow 的公共工具：找 Python、确定项目目录、记录 hook 心跳。
// 只用 Node 自带模块。WorkBuddy 自带的 Node 在两个系统上都在命令路径里。
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

export const PLUGIN_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const IS_WIN = process.platform === "win32";
export const STATE_HOME = path.join(os.homedir(), ".quality-workflow");
export const PRIVATE_PYTHON_DIR = path.join(STATE_HOME, "python");
export const VERSION = JSON.parse(fs.readFileSync(path.join(PLUGIN_ROOT, ".codebuddy-plugin", "plugin.json"), "utf8")).version;

function exists(p) {
  try { return fs.statSync(p).isFile(); } catch { return false; }
}

function pythonNames() {
  return IS_WIN ? ["python.exe", "python3.exe"] : ["python3", "python"];
}

// WorkBuddy 自带 Python 的位置：~/.workbuddy/binaries/python/versions/<current 文件里的版本>/
function managedPythonCandidates() {
  const out = [];
  const base = path.join(process.env.WORKBUDDY_CONFIG_DIR || path.join(os.homedir(), ".workbuddy"), "binaries", "python", "versions");
  let versions = [];
  try {
    const cur = fs.readFileSync(path.join(base, "current"), "utf8").trim();
    if (cur) versions.push(cur);
  } catch { /* 没有 current 文件 */ }
  try {
    for (const v of fs.readdirSync(base).sort().reverse()) if (v !== "current" && !versions.includes(v)) versions.push(v);
  } catch { /* 没装自带 Python */ }
  for (const v of versions) {
    for (const sub of ["", "bin", "Scripts"]) for (const n of pythonNames()) out.push(path.join(base, v, sub, n));
  }
  return out;
}

// 插件自己装的免安装版 Python，以及 Windows 上常见的用户级安装位置。
function privatePythonCandidates() {
  const out = IS_WIN ? [path.join(PRIVATE_PYTHON_DIR, "python.exe")] : [path.join(PRIVATE_PYTHON_DIR, "bin", "python3")];
  if (IS_WIN && process.env.LOCALAPPDATA) {
    const base = path.join(process.env.LOCALAPPDATA, "Programs", "Python");
    try {
      for (const d of fs.readdirSync(base).sort().reverse()) out.push(path.join(base, d, "python.exe"));
    } catch { /* 没有这个目录 */ }
  }
  return out;
}

function extraPathCandidates() {
  const out = [];
  for (const dir of (process.env.WORKBUDDY_EXTRA_PATHS || "").split(path.delimiter).filter(Boolean)) {
    for (const n of pythonNames()) out.push(path.join(dir, n));
  }
  return out;
}

function pathCandidates() {
  const out = [];
  for (const dir of (process.env.PATH || process.env.Path || "").split(path.delimiter).filter(Boolean)) {
    // Windows 应用商店的 python 占位程序会打开商店，不能用。
    if (IS_WIN && /WindowsApps/i.test(dir)) continue;
    for (const n of pythonNames()) out.push(path.join(dir, n));
  }
  return out;
}

function works(exe) {
  const r = spawnSync(exe, ["-c", "import sys; print(sys.version_info >= (3, 8))"], { encoding: "utf8", timeout: 10000, windowsHide: true });
  return r.status === 0 && r.stdout.trim() === "True";
}

// 依次尝试：手动指定、WorkBuddy 自带、插件自己装的、WorkBuddy 额外路径、系统命令路径。结果缓存，失效时重新查找。
export function findPython({ fresh = false } = {}) {
  const cacheFile = path.join(STATE_HOME, "python-path.txt");
  if (!fresh) {
    try {
      const cached = fs.readFileSync(cacheFile, "utf8").trim();
      if (cached && exists(cached)) return cached;
    } catch { /* 没有缓存 */ }
  }
  const seen = new Set();
  const list = [process.env.QFLOW_PYTHON, ...managedPythonCandidates(), ...privatePythonCandidates(), ...extraPathCandidates(), ...pathCandidates()].filter(Boolean);
  for (const exe of list) {
    if (seen.has(exe) || !exists(exe)) continue;
    seen.add(exe);
    if (works(exe)) {
      try { fs.mkdirSync(STATE_HOME, { recursive: true }); fs.writeFileSync(cacheFile, exe); } catch { /* 缓存失败不影响使用 */ }
      return exe;
    }
  }
  if (IS_WIN) {
    const r = spawnSync("py", ["-3", "-c", "import sys; print(sys.executable)"], { encoding: "utf8", timeout: 10000, windowsHide: true });
    if (r.status === 0 && r.stdout.trim() && works(r.stdout.trim())) return r.stdout.trim();
  }
  return null;
}

export function projectDir(cwd) {
  return path.resolve(process.env.CODEBUDDY_PROJECT_DIR || process.env.CLAUDE_PROJECT_DIR || cwd || process.cwd());
}

// 运行原有的 Python 脚本。CLAUDE_PROJECT_DIR 是原脚本读取项目目录用的变量名。
export function runPython(python, script, args, { input, cwd, env } = {}) {
  return spawnSync(python, ["-B", script, ...args], {
    input, cwd, encoding: "utf8", windowsHide: true, timeout: 60000,
    env: { ...process.env, ...env, PYTHONIOENCODING: "utf-8", PYTHONDONTWRITEBYTECODE: "1" },
  });
}

export function recordHeartbeat(event) {
  if (process.env.QFLOW_NO_HEARTBEAT === "1") return;
  try {
    fs.mkdirSync(STATE_HOME, { recursive: true });
    const file = path.join(STATE_HOME, "hook-heartbeat.json");
    let data = {};
    try { data = JSON.parse(fs.readFileSync(file, "utf8")); } catch { /* 第一次 */ }
    data[event] = { ts: Date.now(), version: VERSION, platform: process.platform };
    fs.writeFileSync(file, JSON.stringify(data, null, 2));
  } catch { /* 心跳失败不影响关卡 */ }
}

// 这台电脑上通过过一次自检就算数。自动升级到新版本后不再重复提醒。
export function doctorPassed() {
  try {
    const d = JSON.parse(fs.readFileSync(path.join(STATE_HOME, "doctor-ok.json"), "utf8"));
    return Boolean(d.version) && (!d.platform || d.platform === process.platform);
  } catch { return false; }
}

// 新版本提醒：读后台查询的结果，过期了就在后台再查一次（不等结果）。
// 返回提醒文字；没有新版本、关掉了检查、或者今天已经提醒过，返回空。
export function updateNotice({ compare, spawnCheck }) {
  if (process.env.QFLOW_NO_UPDATE_CHECK === "1") return "";
  const file = path.join(STATE_HOME, "latest.json");
  let latest = null;
  try { latest = JSON.parse(fs.readFileSync(file, "utf8")); } catch { /* 还没查过 */ }
  if (!latest || Date.now() - (latest.ts || 0) > 24 * 3600 * 1000) spawnCheck();
  if (!latest?.version || compare(latest.version, VERSION) <= 0) return "";
  if (latest.notified === latest.version && Date.now() - (latest.notifiedAt || 0) < 24 * 3600 * 1000) return "";
  try { fs.writeFileSync(file, JSON.stringify({ ...latest, notified: latest.version, notifiedAt: Date.now() }, null, 2)); } catch { /* 记不下就下次再提醒 */ }
  return latest.version;
}
