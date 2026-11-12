// 查询 GitHub 上发布的最新版本号，结果写到 ~/.quality-workflow/latest.json。
// 由 hook 在后台启动，每天最多一次，不阻塞任务。设置环境变量 QFLOW_NO_UPDATE_CHECK=1 可以关掉。
import fs from "node:fs";
import path from "node:path";
import { STATE_HOME } from "./lib.mjs";
import { GITHUB_REPO } from "./wb.mjs";

const FILE = ".codebuddy-plugin/marketplace.json";
// 先查 GitHub 原始文件，国内访问不了时再查 jsDelivr 的镜像。
const URLS = (process.env.QFLOW_UPDATE_URLS || [
  `https://raw.githubusercontent.com/${GITHUB_REPO}/main/${FILE}`,
  `https://cdn.jsdelivr.net/gh/${GITHUB_REPO}@main/${FILE}`,
].join(" ")).split(/\s+/).filter(Boolean);

async function fetchVersion(url) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 8000);
  try {
    const res = await fetch(url, { signal: ctrl.signal, headers: { "cache-control": "no-cache" } });
    if (!res.ok) return null;
    const data = await res.json();
    return data?.plugins?.find((p) => p.name === "quality-workflow")?.version || null;
  } catch { return null; } finally { clearTimeout(timer); }
}

const result = { ts: Date.now(), version: null, source: null };
for (const url of URLS) {
  const v = await fetchVersion(url);
  if (v) { result.version = v; result.source = url; break; }
}
try {
  fs.mkdirSync(STATE_HOME, { recursive: true });
  fs.writeFileSync(path.join(STATE_HOME, "latest.json"), JSON.stringify(result, null, 2));
} catch { /* 写不了就算了 */ }
if (process.argv.includes("--print")) console.log(JSON.stringify(result));
