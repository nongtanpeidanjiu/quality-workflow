// 智能体调用流程命令的入口：node "<插件目录>/bin/qflow.mjs" <子命令> ...
import path from "node:path";
import { PLUGIN_ROOT, findPython, projectDir, runPython } from "./lib.mjs";

const python = findPython();
if (!python) {
  console.error(`未通过：找不到可用的 Python。请运行 node "${path.join(PLUGIN_ROOT, "bin", "doctor.mjs")}" 检查环境。`);
  process.exit(2);
}
const root = projectDir();
const r = runPython(python, path.join(PLUGIN_ROOT, "scripts", "flow.py"), process.argv.slice(2), { cwd: root, env: { CLAUDE_PROJECT_DIR: root } });
if (r.stdout) process.stdout.write(r.stdout);
if (r.stderr) process.stderr.write(r.stderr);
process.exit(r.status ?? 2);
