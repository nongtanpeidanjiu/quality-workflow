// 检测 Python；找不到就自动装一份免安装版到 ~/.quality-workflow/python/。
// 不改系统设置，不需要管理员权限。用法：node "<插件目录>/bin/setup-python.mjs"
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { IS_WIN, STATE_HOME, PRIVATE_PYTHON_DIR, findPython } from "./lib.mjs";

// 固定版本和校验值，下载后核对，不一致就不用。
const TAG = "20261110";
const PY = "3.12.16";
const ASSETS = {
  "darwin-arm64": ["aarch64-apple-darwin", "2f11b8aa3726bdbd87dd9eb76568412faddf4f47e26913fa45d6a2e424972acd"],
  "darwin-x64": ["x86_64-apple-darwin", "b78a4c479f1964ec484880bee567568266f68da102269fb93a05fe8716b45f91"],
  "win32-x64": ["x86_64-pc-windows-msvc", "355ee61b8207b91574491b1c6dc9b0ebb4408fc56bfafe498f3ef439314b52dc"],
  "win32-arm64": ["aarch64-pc-windows-msvc", "c1b334c7b1db8f962b37a333d8362cab0ba7ebda1a7cce917fae56a1e0c8cbe7"],
};
const SOURCES = [
  (f) => `https://registry.npmmirror.com/-/binary/python-build-standalone/${TAG}/${encodeURIComponent(f)}`,
  (f) => `https://github.com/astral-sh/python-build-standalone/releases/download/${TAG}/${encodeURIComponent(f)}`,
];

function sha256(file) {
  return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

async function download(url, dest) {
  // 先用 Node 自带的下载；不行再用系统的 curl（它会走系统代理）。
  try {
    const res = await fetch(url, { redirect: "follow", signal: AbortSignal.timeout(180000) });
    if (res.ok) {
      fs.writeFileSync(dest, Buffer.from(await res.arrayBuffer()));
      return true;
    }
  } catch { /* 换 curl */ }
  const curls = IS_WIN ? [path.join(process.env.SystemRoot || "C:\\Windows", "System32", "curl.exe"), "curl"] : ["curl", "/usr/bin/curl"];
  for (const curl of curls) {
    const r = spawnSync(curl, ["-fsSL", "--max-time", "300", "-o", dest, url], { windowsHide: true, timeout: 320000 });
    if (r.status === 0 && fs.existsSync(dest)) return true;
  }
  return false;
}

function extract(archive, dir) {
  // Windows 用系统自带的 tar；Git Bash 里的 tar 不认带盘符的路径。
  const tars = IS_WIN ? [path.join(process.env.SystemRoot || "C:\\Windows", "System32", "tar.exe"), "tar"] : ["tar", "/usr/bin/tar"];
  for (const tar of tars) {
    const r = spawnSync(tar, ["-xzf", archive, "-C", dir], { windowsHide: true, timeout: 180000 });
    if (r.status === 0) return true;
  }
  return false;
}

function manualSteps() {
  if (IS_WIN) {
    return [
      "在终端运行：winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements",
      "或者下载安装：https://www.python.org/downloads/windows/ （安装时勾选 Add python.exe to PATH）",
      "国内下载较快的地址：https://registry.npmmirror.com/binary.html?path=python/3.12.10/ （选 python-3.12.10-amd64.exe）",
    ];
  }
  return [
    "下载安装：https://www.python.org/downloads/macos/ （下载 macOS 64-bit universal2 installer，双击安装）",
    "国内下载较快的地址：https://registry.npmmirror.com/binary.html?path=python/3.12.10/ （选 python-3.12.10-macos11.pkg）",
    "装了 Homebrew 的话，也可以运行：brew install python@3.12",
  ];
}

// 返回 { python, how, log, manual }。python 为 null 表示没装上。
export async function ensurePython() {
  const log = [];
  let python = findPython({ fresh: true });
  if (python) return { python, how: "已有", log, manual: [] };

  const key = `${process.platform}-${process.arch}`;
  const asset = ASSETS[key];
  if (!asset) {
    log.push(`这个系统（${key}）没有对应的免安装版 Python。`);
  } else {
    const file = `cpython-${PY}+${TAG}-${asset[0]}-install_only_stripped.tar.gz`;
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "qflow-python-"));
    const archive = path.join(tmp, "python.tar.gz");
    try {
      let ok = false;
      for (const src of SOURCES) {
        const url = src(file);
        log.push(`正在下载 Python ${PY}：${new URL(url).host}`);
        try { fs.rmSync(archive, { force: true }); } catch { /* 没有旧文件 */ }
        if (!(await download(url, archive))) { log.push("下载没有成功，换下一个地址。"); continue; }
        if (sha256(archive) !== asset[1]) { log.push("文件校验不一致，丢弃，换下一个地址。"); continue; }
        ok = true;
        break;
      }
      if (ok) {
        const stage = path.join(tmp, "out");
        fs.mkdirSync(stage);
        if (!extract(archive, stage)) {
          log.push("解压没有成功。");
        } else {
          fs.rmSync(PRIVATE_PYTHON_DIR, { recursive: true, force: true });
          fs.mkdirSync(path.dirname(PRIVATE_PYTHON_DIR), { recursive: true });
          fs.cpSync(path.join(stage, "python"), PRIVATE_PYTHON_DIR, { recursive: true, verbatimSymlinks: true });
          python = findPython({ fresh: true });
          if (python) return { python, how: `已自动安装到 ${PRIVATE_PYTHON_DIR}`, log, manual: [] };
          log.push("装好的 Python 无法运行。");
        }
      }
    } catch (e) {
      log.push(`自动安装出错：${e.message}`);
    } finally {
      fs.rmSync(tmp, { recursive: true, force: true });
    }
  }

  // Windows 再试一次系统的包管理器。
  if (IS_WIN) {
    log.push("改用 winget 安装。");
    spawnSync("winget", ["install", "-e", "--id", "Python.Python.3.12", "--scope", "user", "--silent", "--accept-package-agreements", "--accept-source-agreements"],
      { windowsHide: true, timeout: 600000, shell: true });
    python = findPython({ fresh: true });
    if (python) return { python, how: "已用 winget 安装", log, manual: [] };
    log.push("winget 没有装上。");
  }
  return { python: null, how: "", log, manual: manualSteps() };
}

// 直接运行时：检测并安装，把结果说清楚。
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const r = await ensurePython();
  for (const line of r.log) console.log(line);
  if (r.python) {
    console.log(`Python 可用（${r.how}）：${r.python}`);
    process.exit(0);
  }
  console.log("没能自动装好 Python。请按下面任意一种办法安装，装好后再运行一次本命令：");
  for (const m of r.manual) console.log(`  - ${m}`);
  process.exit(1);
}
