"""quality-workflow 的状态和判定逻辑。只用标准库，供 flow.py 和 hook_handler.py 共用。"""
import contextlib
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

FLOW_DIRNAME = ".flow"
PROTECTED = {"state.json", "config.json", "user-messages.jsonl", "active"}
WRITABLE_TASK_FILES = {"任务单.md"}
WRITABLE_TASK_DIRS = {"drafts", "evidence"}
ACTIVE = {"澄清中", "待确认", "执行中", "受阻", "待关闭"}
LOCKED_WRITE = {"澄清中", "待确认", "待关闭"}
# 平台自己的目录（记忆、设置）不算成果，也不拦。
PLATFORM_DIRS = {".workbuddy", ".codebuddy", ".claude"}
SKIP_DIRS = {".git", ".flow", "node_modules", "__pycache__", ".venv"} | PLATFORM_DIRS
SKIP_FILES = (".DS_Store", "Thumbs.db", "~$*", "*.tmp", ".~lock.*")
MAX_BASELINE_BYTES = 200 * 1024 * 1024
STOP_CAP = 3
GEN_START = "<!-- qflow:generated:start -->"
GEN_END = "<!-- qflow:generated:end -->"
EMPTY_MARK = "（待填写）"
NO_FILES = "（无文件成果）"
ITEM_TYPES = ("理解", "输入", "依据", "方法", "假设", "中间成果")
ITEM_LEVELS = ("决定性", "重要", "一般")
DECISIVE = "决定性"
ITEM_OK = ("已验证", "用户已确认", "未验证（用户同意继续）")
WAIVED = "未验证（用户同意继续）"
SHORT_QUOTE = 8
RECENT_KEEP = 30
RECENT_MAX_AGE = 24 * 3600


class FlowError(Exception):
    pass


def project_root(cwd=None):
    return os.path.realpath(os.environ.get("CLAUDE_PROJECT_DIR") or cwd or os.getcwd())


def flow_root(root):
    custom = os.environ.get("QFLOW_DIR")
    return os.path.realpath(custom) if custom else os.path.join(root, FLOW_DIRNAME)


def state_home():
    return os.path.realpath(os.environ.get("QFLOW_HOME") or os.path.expanduser("~/.quality-workflow"))


def improvements_path():
    return os.path.realpath(os.path.expanduser("~/.quality-workflow/improvements.md"))


def now():
    return time.time()


def norm(s):
    """路径比较用：统一 Unicode 形式；macOS 和 Windows 默认不区分大小写。"""
    s = unicodedata.normalize("NFC", s)
    return s.casefold() if sys.platform in ("darwin", "win32") else s


@contextlib.contextmanager
def flow_lock(root):
    """同一项目的状态读写串行化，防止并行子智能体互相覆盖。"""
    fr = flow_root(root)
    if fcntl is None or not os.path.isdir(fr):
        yield
        return
    with open(os.path.join(fr, ".lock"), "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def fmt_time(ts):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_json(path, data):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def load_config(root):
    cfg = read_json(os.path.join(flow_root(root), "config.json"), {})
    return cfg if isinstance(cfg, dict) else {}


# ---------- 任务定位 ----------

def task_dir(root, tid):
    return os.path.join(flow_root(root), "tasks", tid)


def load_task(root, tid):
    st = read_json(os.path.join(task_dir(root, tid), "state.json"))
    if not st:
        raise FlowError("找不到任务 %s" % tid)
    return st


def save_task(root, st):
    st["updated"] = now()
    write_json(os.path.join(task_dir(root, st["id"]), "state.json"), st)
    render_generated(root, st)


def active_tasks(root):
    base = os.path.join(flow_root(root), "tasks")
    if not os.path.isdir(base):
        return []
    out = []
    for name in sorted(os.listdir(base)):
        path = os.path.join(base, name, "state.json")
        if not os.path.exists(path):
            continue
        st = read_json(path)
        if not isinstance(st, dict) or "status" not in st or "items" not in st:
            raise FlowError("任务 %s 的状态文件已损坏，流程无法判断。请检查 %s。" % (name, path))
        if st.get("status") in ACTIVE:
            out.append(st)
    return out


def resolve_task(root, tid=None):
    """返回 (state, error)。多个活动任务且没有指定时不猜。"""
    if tid:
        return load_task(root, tid), None
    tasks = active_tasks(root)
    if not tasks:
        return None, None
    if len(tasks) == 1:
        return tasks[0], None
    pin = None
    try:
        with open(os.path.join(flow_root(root), "active"), encoding="utf-8") as f:
            pin = f.read().strip()
    except OSError:
        pass
    for t in tasks:
        if t["id"] == pin:
            return t, None
    return None, "有多个进行中的任务（%s），请用 qflow use <编号> 指定。" % "、".join(t["id"] for t in tasks)


# ---------- 用户原话校验 ----------

def recent_path(root):
    return os.path.join(state_home(), "recent-messages", text_hash(root)[:16] + ".jsonl")


def log_user_message(root, text, ts=None, extra=None):
    """建档后记入任务目录。建档前只在本机暂存最近几条，建档时导入，这样最初的要求也能被引用。"""
    entry = json.dumps(dict({"ts": ts if ts is not None else now(), "text": text}, **(extra or {})), ensure_ascii=False) + "\n"
    fr = flow_root(root)
    if os.path.isdir(fr):
        with open(os.path.join(fr, "user-messages.jsonl"), "a", encoding="utf-8") as f:
            f.write(entry)
        return
    path = recent_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = []
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    lines = (lines + [entry])[-RECENT_KEEP:]
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def direct_path(root, kind):
    return os.path.join(state_home(), "direct", "%s-%s.json" % (text_hash(root)[:16], kind))


def mark_prompt(root):
    """记下用户最近一次发消息的时间。“直接做”的声明只对这条消息有效。"""
    write_json(direct_path(root, "prompt"), {"ts": now()})


def direct_declared(root):
    d = read_json(direct_path(root, "declared"))
    if not d:
        return None
    last = (read_json(direct_path(root, "prompt")) or {}).get("ts", 0)
    return d if d.get("ts", 0) > last else None


def find_user_quote(root, quote):
    """在任务记录和建档前的暂存里找用户原话。"""
    for path in (os.path.join(flow_root(root), "user-messages.jsonl"), recent_path(root)):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        m = json.loads(line)
                    except ValueError:
                        continue
                    if quote_matches(quote, m.get("text")):
                        return m.get("ts")
        except OSError:
            continue
    return None


def import_recent_messages(root):
    path = recent_path(root)
    if not os.path.isfile(path):
        return
    keep = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if now() - m.get("ts", 0) <= RECENT_MAX_AGE:
                keep.append(json.dumps(m, ensure_ascii=False) + "\n")
    fr = flow_root(root)
    os.makedirs(fr, exist_ok=True)
    with open(os.path.join(fr, "user-messages.jsonl"), "a", encoding="utf-8") as f:
        f.writelines(keep)
    os.remove(path)


QUOTE_MARKS = "\"'“”‘’「」『』《》〈〉`"


def _norm(s):
    """比较原话时忽略空白、全半角和引号写法的差别。"""
    s = unicodedata.normalize("NFKC", s or "")
    return re.sub(r"\s+", "", s).translate({ord(c): None for c in QUOTE_MARKS})


CLAUSE_SPLIT = re.compile(r"[，,。.！!？?；;：:、\n]+")


def _clauses(text):
    text = unicodedata.normalize("NFKC", text or "")
    return {_norm(p) for p in CLAUSE_SPLIT.split(text) if _norm(p)}


def quote_matches(quote, message):
    """原话必须等于整条消息，或其中一个完整分句，防止从“我不同意”里摘出“同意”。"""
    stripped = CLAUSE_SPLIT.sub("", unicodedata.normalize("NFKC", quote or ""))
    if not _norm(stripped):
        return False
    if _norm(quote) == _norm(message):
        return True
    parts = _clauses(quote)
    return bool(parts) and parts <= _clauses(message)


def verify_quote(root, quote, after, short_after=None):
    """原话必须真实出现在用户消息记录中，并且晚于 after。
    short_after：很短的话（如“好的”）必须晚于这个时间，防止拿更早的一句应答充当确认。"""
    if short_after is not None and len(_norm(CLAUSE_SPLIT.sub("", unicodedata.normalize("NFKC", quote or "")))) < SHORT_QUOTE:
        after = max(after, short_after)
    if not _norm(quote):
        raise FlowError("需要填写用户原话（--quote），请填用户回复的完整句子。")
    try:
        with open(os.path.join(flow_root(root), "user-messages.jsonl"), encoding="utf-8") as f:
            for line in f:
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if m.get("ts", 0) > after and quote_matches(quote, m.get("text")):
                    return m["ts"]
    except OSError:
        pass
    raise FlowError("这句话不是 %s 之后用户说的完整句子。请先得到用户的回复，再引用完整原话。" % fmt_time(after))


# ---------- 任务单解析 ----------

def read_sheet(root, st):
    path = os.path.join(task_dir(root, st["id"]), "任务单.md")
    with open(path, encoding="utf-8") as f:
        return f.read()


def sections(text):
    body = re.sub(re.escape(GEN_START) + r".*?" + re.escape(GEN_END), "", text, flags=re.S)
    out, cur = {}, None
    for line in body.splitlines():
        m = re.match(r"^#{2,3}\s+(.+?)\s*$", line)
        if m:
            cur = m.group(1)
            out[cur] = []
        elif cur is not None:
            out[cur].append(line)
    return {k: "\n".join(v).strip() for k, v in out.items()}


def section_filled(text):
    return bool(text) and EMPTY_MARK not in text


def bullets(text):
    items = []
    for line in (text or "").splitlines():
        m = re.match(r"^\s*[-*]\s+(.+?)\s*$", line)
        if m:
            items.append(m.group(1).strip("`").strip())
    return items


def parse_table(text):
    rows = [l.strip() for l in (text or "").splitlines() if l.strip().startswith("|")]
    if len(rows) < 2:
        return [], []
    split = lambda r: [c.strip() for c in r.strip("|").split("|")]
    head = split(rows[0])
    data = []
    for r in rows[1:]:
        if re.match(r"^\|[\s:|-]+\|$", r):
            continue
        cells = split(r)
        if any(c and EMPTY_MARK not in c for c in cells):
            data.append(dict(zip(head, cells)))
    return head, data


def path_items(text, label):
    """路径节只接受“- 路径”。解析不了就报错，不能悄悄当成空。"""
    if not section_filled(text or "x"):
        return []
    items = []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        m = re.match(r"^\s*[-*]\s+(.+?)\s*$", line)
        item = m.group(1).strip().strip("`").strip() if m else None
        if item is None or (item not in (NO_FILES, "无") and re.search(r"[\s（）()，,。；;：]", item)):
            raise FlowError("“%s”每行只能写一个路径，如 - docs/guide.md，说明文字放到别处：%s" % (label, line.strip()))
        items.append(item)
    return items


def parse_items(text):
    """关键项清单：编号、要素、类型、级别、谁验证、怎么验证。"""
    _, rows = parse_table(text)
    items, seen = [], set()
    for r in rows:
        iid = (r.get("编号") or "").strip()
        if not iid:
            continue
        if not re.fullmatch(r"K\d+", iid):
            raise FlowError("关键项编号要写成 K1、K2 这样：%s" % iid)
        if iid in seen:
            raise FlowError("关键项编号重复：%s" % iid)
        seen.add(iid)
        typ, level = (r.get("类型") or "").strip(), (r.get("级别") or "").strip()
        who, how = (r.get("谁验证") or "").strip(), (r.get("怎么验证") or "").strip()
        if not (r.get("要素") or "").strip():
            raise FlowError("关键项 %s 没写要素。" % iid)
        if typ not in ITEM_TYPES:
            raise FlowError("关键项 %s 的类型只能是：%s" % (iid, "、".join(ITEM_TYPES)))
        if level not in ITEM_LEVELS:
            raise FlowError("关键项 %s 的级别只能是：%s" % (iid, "、".join(ITEM_LEVELS)))
        if level == DECISIVE:
            if who not in ("用户", "智能体"):
                raise FlowError("决定性的项 %s 要写明谁验证：用户（答案只在用户那里）或 智能体（可以客观核对）。" % iid)
            if not how:
                raise FlowError("决定性的项 %s 要写明怎么验证。" % iid)
        items.append({"id": iid, "name": r["要素"].strip(), "type": typ, "level": level, "verifier": who, "method": how})
    return items


def parse_sheet(text):
    sec = sections(text)
    _, steps = parse_table(sec.get("步骤和控制点"))
    criteria = {s.get("编号", ""): s.get("判定标准", "") for s in steps}
    uses = {s.get("编号", ""): re.findall(r"K\d+", s.get("用到的关键项", "") or "") for s in steps}
    allowed = path_items(sec.get("允许改动"), "允许改动")
    return {
        "goal": sec.get("目标", ""),
        "not_doing": sec.get("不做", ""),
        "success": sec.get("成功标准", ""),
        "allowed": [] if allowed == [NO_FILES] else allowed,
        "no_file_output": allowed == [NO_FILES],
        "forbidden": [b for b in path_items(sec.get("禁止改动"), "禁止改动") if b != "无"],
        "signature_extra": [sec.get("要做", ""), sec.get("必须满足", ""), sec.get("反例", "")],
        "steps": [s.get("编号", "") for s in steps if s.get("编号")],
        "criteria": criteria,
        "uses": uses,
        "items": parse_items(sec.get("关键项清单")),
    }


REQUIRED_HEADINGS = ["目标", "要做", "不做", "允许改动", "成功标准", "反例", "关键项清单", "步骤和控制点"]
ALL_HEADINGS = REQUIRED_HEADINGS + ["禁止改动", "必须满足", "需要用户确认或提供的", "假设和待定", "潜在需求", "资料", "决定和变更"]


def check_sheet_complete(text):
    sec = sections(text)
    gone = [h for h in ALL_HEADINGS if h not in sec]
    if gone:
        raise FlowError("任务单缺少固定标题：%s" % "、".join(gone))
    missing = [h for h in REQUIRED_HEADINGS if not section_filled(sec.get(h, ""))]
    if missing:
        raise FlowError("任务单这些部分还没写：%s" % "、".join(missing))
    parsed = parse_sheet(text)
    if not parsed["steps"]:
        raise FlowError("“步骤和控制点”至少要有一行，并写明编号。")
    empty = [k for k, v in parsed["criteria"].items() if not v]
    if empty:
        raise FlowError("这些步骤没写判定标准：%s" % "、".join(empty))
    if not parsed["allowed"] and not parsed["no_file_output"]:
        raise FlowError("“允许改动”要列出路径；没有文件成果时写 - %s。" % NO_FILES)
    if not any(i["type"] == "理解" and i["level"] == DECISIVE and i["verifier"] == "用户" for i in parsed["items"]):
        raise FlowError("关键项清单里至少要有一项类型为“理解”、由用户验证的决定性的项：要做成什么、给谁用、用来做什么。对用户意思的理解只有用户能确认，不能自己核对通过。")
    ids = {i["id"] for i in parsed["items"]}
    for sid, ks in parsed["uses"].items():
        bad = [k for k in ks if k not in ids]
        if bad:
            raise FlowError("步骤 %s 用到的关键项不在清单里：%s" % (sid, "、".join(bad)))
    return parsed


def apply_parsed(st, parsed):
    st["scope"] = {"allowed": parsed["allowed"], "forbidden": parsed["forbidden"], "no_file_output": parsed["no_file_output"]}
    st["inject"] = {"goal": parsed["goal"], "not_doing": parsed["not_doing"], "success": parsed["success"]}
    st["criteria_hash"] = text_hash(json.dumps(parsed["criteria"], ensure_ascii=False, sort_keys=True))
    existing = {s["id"]: s for s in st.get("steps", [])}
    new_steps = []
    for sid in parsed["steps"]:
        step = existing.get(sid) or {"id": sid, "status": "待开始"}
        step["uses"] = parsed["uses"].get(sid, [])
        new_steps.append(step)
    st["steps"] = new_steps


# ---------- 关键项 ----------

def item_ok(root, st, it):
    """已验证或已确认，并且证据文件没有被改动。"""
    if it.get("status") not in ITEM_OK:
        return False
    ev = it.get("evidence")
    if ev:
        path = os.path.join(task_dir(root, st["id"]), "evidence", ev["file"])
        if not os.path.isfile(path) or sha(path) != ev["hash"]:
            return False
    return True


def pending_decisive(root, st, ids=None):
    return [i for i in st.get("items", []) if i["level"] == DECISIVE and (ids is None or i["id"] in ids) and not item_ok(root, st, i)]


def mark_steps_recheck(st, item_ids):
    for s in st.get("steps", []):
        if s["status"] in ("待核验", "通过") and set(s.get("uses", [])) & set(item_ids):
            s["status"] = "需复核"


def sync_items(st, rows, approved=False):
    """把任务单里的关键项清单同步到状态。返回 (被重置的编号, 需要用户同意的降级或删除)。"""
    old = {i["id"]: i for i in st.get("items", [])}
    confirmed = bool(st.get("direction_confirm"))
    out, reset, need = [], [], []
    for r in rows:
        cur = old.pop(r["id"], None)
        if cur is None:
            out.append(dict(r, status="待验证", created=now()))
            continue
        if confirmed and cur["level"] == DECISIVE and r["level"] != DECISIVE:
            need.append("%s 从决定性降为%s" % (r["id"], r["level"]))
        if cur["name"] != r["name"] or (r["level"] == DECISIVE and cur["level"] != DECISIVE):
            if cur.get("status") != "待验证":
                reset.append(r["id"])
            cur.update(status="待验证")
            cur.pop("evidence", None)
            cur.pop("quote", None)
        cur.update(name=r["name"], type=r["type"], level=r["level"], verifier=r["verifier"], method=r["method"])
        out.append(cur)
    for iid, cur in old.items():
        if confirmed and cur["level"] == DECISIVE:
            need.append("%s 被删除" % iid)
    if need and not approved:
        raise FlowError("降低或删除决定性的项要用户同意：%s。请用 qflow change --note 说明 --quote 用户原话。" % "；".join(need))
    st["items"] = out
    mark_steps_recheck(st, reset)
    return reset, need


def items_digest(st):
    rows = [[i["id"], i["name"], i["level"], i.get("status"), (i.get("evidence") or {}).get("file"), (i.get("evidence") or {}).get("hash"), i.get("quote")] for i in st.get("items", []) if i["level"] == DECISIVE]
    return text_hash(json.dumps(rows, ensure_ascii=False, sort_keys=True))


def page_signature(parsed):
    return text_hash(json.dumps([direction_signature(parsed), parsed["items"], parsed["uses"]], ensure_ascii=False, sort_keys=True))


def direction_signature(parsed):
    """需要用户重新确认的只有目标、范围和成功标准。步骤怎么排、每步怎么判定，由执行者调整并记录。"""
    return text_hash(json.dumps([parsed["goal"], parsed["not_doing"], parsed["success"], parsed["allowed"], parsed["forbidden"], parsed["signature_extra"]], ensure_ascii=False, sort_keys=True))


# ---------- 路径和指纹 ----------

def rel_to(root, path, cwd=None):
    p = path if os.path.isabs(path) else os.path.join(cwd or root, path)
    real = os.path.realpath(p)
    try:
        rel = os.path.relpath(real, root)
    except ValueError:
        return None, real
    if rel == ".." or rel.startswith(".." + os.sep):
        return None, real
    return unicodedata.normalize("NFC", rel.replace(os.sep, "/")), real


def match_pattern(rel, pat):
    rel = norm(rel)
    pat = norm(pat.strip())
    if pat in (".", "./", "**", "*"):
        return True
    pat = pat[2:] if pat.startswith("./") else pat
    if pat.endswith("/"):
        return rel.startswith(pat)
    if rel == pat or rel.startswith(pat + "/"):
        return True
    return fnmatch.fnmatch(rel, pat)


def matches_any(rel, patterns):
    return any(match_pattern(rel, p) for p in patterns or [])


def in_temp(real):
    for base in {tempfile.gettempdir(), "/tmp", "/private/tmp"}:
        b = os.path.realpath(base)
        if real == b or real.startswith(b + os.sep):
            return True
    return False


def skip_dirs(root):
    extra = load_config(root).get("exclude") or []
    return SKIP_DIRS | {x.rstrip("/") for x in extra if isinstance(x, str)}


def scan_project(root):
    """全项目指纹，用修改时间和大小做缓存，避免每次重算。"""
    cache_path = os.path.join(flow_root(root), "hash-cache.json")
    cache = read_json(cache_path, {}) or {}
    new_cache, out = {}, {}
    skip = skip_dirs(root)
    fr = os.path.realpath(flow_root(root))
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip and os.path.realpath(os.path.join(dirpath, d)) != fr]
        for fn in filenames:
            if any(fnmatch.fnmatch(fn, p) for p in SKIP_FILES):
                continue
            full = os.path.join(dirpath, fn)
            if not os.path.isfile(full):
                continue
            rel = unicodedata.normalize("NFC", os.path.relpath(full, root).replace(os.sep, "/"))
            stat = os.stat(full)
            key = [stat.st_mtime, stat.st_size]
            hit = cache.get(rel)
            digest = hit[1] if hit and hit[0] == key else sha(full)
            new_cache[rel] = [key, digest]
            out[rel] = digest
    if os.path.isdir(fr):
        write_json(cache_path, new_cache)
    return out


def diff_fingerprints(root, st):
    rec = st.get("fingerprints", {})
    cur = scan_project(root)
    diffs = {}
    for rel, h in cur.items():
        if rec.get(rel) != h:
            diffs[rel] = "改动" if rel in rec else "新增"
    for rel in rec:
        if rel not in cur:
            diffs[rel] = "删除"
    return diffs


def in_scope(st, rel):
    scope = st.get("scope", {})
    return matches_any(rel, scope.get("allowed")) and not matches_any(rel, scope.get("forbidden"))


def outcome_digest(root, st):
    """检查结论绑定范围内的成果和快照；范围外差异由差异检查单独阻止。"""
    cur = {k: v for k, v in scan_project(root).items() if in_scope(st, k)}
    return text_hash(json.dumps([cur, st.get("snapshots", {})], sort_keys=True))


def git_head(root):
    try:
        r = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def make_baseline(root, st):
    """首次确认时记录全项目指纹。范围内文件另存副本，保证未提交的改动也能恢复。"""
    if not st.get("baseline"):
        st["fingerprints"] = scan_project(root)
    base = os.path.join(task_dir(root, st["id"]), "baseline", "v%d" % st["req_version"])
    total = 0
    skipped = []
    for rel in st["fingerprints"]:
        src = os.path.join(root, rel)
        if not in_scope(st, rel) or not os.path.isfile(src):
            continue
        total += os.path.getsize(src)
        if total > MAX_BASELINE_BYTES:
            skipped.append(rel)
            continue
        dst = os.path.join(base, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    st["baseline"] = {"version": st["req_version"], "git_head": git_head(root) if os.path.isdir(os.path.join(root, ".git")) else None, "too_large_to_copy": skipped}


# ---------- 检查记录 ----------

def latest_check(st, ctype):
    found = [c for c in st.get("checks", []) if c.get("type") == ctype]
    return found[-1] if found else None


def checks_valid(root, st, ctype="交付", step=None):
    digest = outcome_digest(root, st)
    idigest = items_digest(st)
    out = []
    for c in st.get("checks", []):
        if c.get("type") != ctype or (step and c.get("step") != step):
            continue
        if c.get("req_version") == st["req_version"] and c.get("outcome") == digest and c.get("items") == idigest:
            out.append(c)
    return out


def latest_valid_verdict(root, st):
    valid = checks_valid(root, st)
    return valid[-1].get("verdict") if valid else None


def has_valid_pass(root, st):
    for c in checks_valid(root, st):
        if c.get("verdict") in ("通过", "用户裁定通过"):
            return True
    return False


def steps_ready(st):
    steps = st.get("steps", [])
    if not steps:
        return False
    return all(s["status"] in ("待核验", "通过") for s in steps)


def open_blockers(st):
    """阻断问题未解决，或重要问题还没定去向，都不能关闭。"""
    out = []
    for i in st.get("issues", []):
        if i["status"] != "开放":
            continue
        if i["level"] == "阻断" or (i["level"] == "重要" and not i.get("disposition")):
            out.append(i)
    return out


# ---------- 任务单生成段 ----------

def render_generated(root, st):
    path = os.path.join(task_dir(root, st["id"]), "任务单.md")
    if not os.path.isfile(path):
        return
    lines = [GEN_START, "", "## 确认记录和当前进度（脚本生成，请勿修改）", ""]
    lines.append("- 任务状态：%s" % st["status"])
    lines.append("- 需求版本：v%d" % st["req_version"])
    for key, label in (("direction_confirm", "确认页确认"), ("result_confirm", "成果确认")):
        c = st.get(key)
        if c:
            lines.append("- %s：v%d，%s，原话：%s" % (label, c["version"], fmt_time(c["ts"]), c.get("quote") or "各项已分别引用用户原话"))
    u = st.get("understood")
    if u:
        lines.append("- 开工前的复述：%s" % u["text"].replace("\n", " "))
        lines.append("- 用户对复述的回复：%s（%s）" % (u["quote"], fmt_time(u["ts"])))
    if st.get("steps"):
        lines.append("- 步骤：" + "；".join("%s %s" % (s["id"], s["status"]) for s in st["steps"]))
    if st.get("items"):
        lines += ["", "### 关键项验证记录", "", "| 编号 | 要素 | 级别 | 谁验证 | 状态 | 证据或用户原话 |", "|---|---|---|---|---|---|"]
        for i in st["items"]:
            proof = i.get("quote") or ((i.get("evidence") or {}).get("file") and "evidence/" + i["evidence"]["file"]) or "—"
            status = i.get("status") or "待验证"
            if i["level"] == DECISIVE and status in ITEM_OK and not item_ok(root, st, i):
                status = "需复核（证据被改动）"
            lines.append("| %s | %s | %s | %s | %s | %s |" % (i["id"], i["name"], i["level"], i.get("verifier") or "—", status if i["level"] == DECISIVE else "—", proof))
    if st.get("issues"):
        lines += ["", "### 问题记录", "", "| 编号 | 级别 | 问题 | 去向 | 状态 | 退回次数 |", "|---|---|---|---|---|---|"]
        for i in st["issues"]:
            lines.append("| %s | %s | %s | %s | %s | %d |" % (i["id"], i["level"], i["text"], i.get("disposition") or "—", i["status"], i.get("bounces", 0)))
    if st.get("light_changes"):
        lines += ["", "### 小改动记录（声明没有动决定性的项，检查员和用户要核对）", "", "| 说明 | 改动的文件 |", "|---|---|"]
        for x in st["light_changes"]:
            lines.append("| %s | %s |" % (x["note"], "、".join(x.get("files") or []) or "—"))
    if st.get("accepted_diffs"):
        lines += ["", "### 差异记录（检查员要核对）", "", "| 路径 | 来源 | 理由 | 用户原话 |", "|---|---|---|---|"]
        for x in st["accepted_diffs"]:
            lines.append("| %s | %s | %s | %s |" % (x["path"], x["source"], x["reason"], x.get("quote") or "—"))
    lines += ["", GEN_END]
    block = "\n".join(lines)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if GEN_START in text and GEN_END in text:
        text = re.sub(re.escape(GEN_START) + r".*?" + re.escape(GEN_END), lambda m: block, text, flags=re.S)
    else:
        text = text.rstrip() + "\n\n" + block + "\n"
    with open(path, encoding="utf-8") as f:
        if f.read() == text:
            return
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
