#!/usr/bin/env python3
"""qflow：任务状态和关卡。状态只通过这个脚本修改。"""
import argparse
import json
import os
import re
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flowlib import *  # noqa: F401,F403
import flowlib as L

ROOT_ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "skills", "quality-workflow", "assets")
VERDICTS = {"通过", "不通过", "弃权"}
ISSUE_DISPOSITIONS = {
    "阻断": {"修复", "交用户决定"},
    "重要": {"修复", "延后", "交用户决定"},
    "建议": {"修复", "只记录"},
}


def die(msg):
    raise L.FlowError(msg)


def transition(st, target, reason=""):
    st.setdefault("history", []).append({"from": st["status"], "to": target, "ts": L.now(), "reason": reason})
    st["status"] = target
    st["last_transition"] = L.now()
    st["stop_blocks"] = 0


def get_task(root, a):
    if getattr(a, "task", None):
        return L.load_task(root, a.task)
    pin = os.path.join(L.flow_root(root), "active")
    if os.path.isfile(pin):
        with open(pin, encoding="utf-8") as f:
            tid = f.read().strip()
        candidate = L.read_json(os.path.join(L.task_dir(root, tid), "state.json"))
        if candidate and (candidate.get("status") in L.ACTIVE or candidate.get("status") == "已暂停"):
            return candidate
    st, err = L.resolve_task(root)
    if err:
        die(err)
    if not st:
        die("没有进行中的任务。用 qflow start 建档。")
    return st


def cmd_start(root, a):
    fr = L.flow_root(root)
    others = L.active_tasks(root)
    tid = time.strftime("%Y%m%d") + "-" + re.sub(r"[^\w-]+", "-", a.title or "task").strip("-")[:30]
    base = tid
    n = 1
    while os.path.exists(L.task_dir(root, tid)):
        n += 1
        tid = "%s-%d" % (base, n)
    d = L.task_dir(root, tid)
    for sub in ("evidence", "drafts"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    shutil.copy(os.path.join(ROOT_ASSETS, "任务单模板.md"), os.path.join(d, "任务单.md"))
    L.import_recent_messages(root)
    t = L.now()
    st = {
        "id": tid, "title": a.title or "", "status": "澄清中", "created": t, "last_transition": t,
        "items": [], "req_version": 1, "request": a.request or "",
        "scope": {"allowed": [], "forbidden": [], "no_file_output": False}, "inject": {},
        "understood": None, "direction_confirm": None, "result_confirm": None, "review": False, "steps": [], "issues": [], "fingerprints": {},
        "pending_diffs": {}, "checks": [], "captured": [], "history": [], "changes": [], "stop_blocks": 0, "unverified": False,
    }
    L.save_task(root, st)
    os.makedirs(fr, exist_ok=True)
    with open(os.path.join(fr, "active"), "w", encoding="utf-8") as f:
        f.write(tid)
    gitdir = os.path.join(root, ".git")
    if not os.environ.get("QFLOW_DIR") and os.path.isdir(gitdir):
        exclude = os.path.join(gitdir, "info", "exclude")
        os.makedirs(os.path.dirname(exclude), exist_ok=True)
        existing = ""
        if os.path.isfile(exclude):
            with open(exclude, encoding="utf-8") as f:
                existing = f.read()
        if ".flow/" not in existing:
            with open(exclude, "a", encoding="utf-8") as f:
                f.write("\n.flow/\n")
    print("已建档：%s\n任务单：%s" % (tid, os.path.join(d, "任务单.md")))
    if others:
        print("注意：还有进行中的任务（%s），现在的写入规则按新任务判断。需要切回时用 qflow use。" % "、".join(t["id"] for t in others))


NOT_SMALL = "不算小事的情况：要写文档或方案但用户没说给谁用、用来做什么；要统计或计算，而口径、范围要用户定；要依据外部规定或资料来改；要改多个文件或分几步做。这些要建档：qflow start。"


def cmd_direct(root, a):
    """声明这件事可以直接做。没有任务记录时，写文件前必须先声明，声明只对用户的这条消息有效。"""
    if L.active_tasks(root):
        die("已经有进行中的任务，按任务的流程做，不需要这个声明。")
    restate, items, checked = (a.restate or "").strip(), (a.items or "").strip(), (a.checked or "").strip()
    if len(restate) < 6:
        die("--restate 要用一句话复述用户要你做什么。")
    if not items:
        die("--items 要写这件事的结果由什么决定（数据、数字、事实、引用、算法、对用户意思的理解）；确实没有就写“无”。")
    if items != "无" and not checked:
        die("有决定结果的东西，就要写明当场怎么核对的（--checked）。当场核对不了，或者要用户来定，就不是小事。" + NOT_SMALL)
    if L.find_user_quote(root, a.quote) is None:
        die("--quote 要填用户说清这件事的那句原话（整条消息或其中完整的一句）。找不到这样一句，说明用户的意思还没说清，不能直接做。" + NOT_SMALL)
    L.write_json(L.direct_path(root, "declared"), {"ts": L.now(), "restate": restate, "items": items, "checked": checked, "quote": a.quote})
    log = os.path.join(L.state_home(), "direct", "log.jsonl")
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": L.now(), "project": root, "restate": restate, "items": items, "checked": checked, "quote": a.quote}, ensure_ascii=False) + "\n")
    print("已记录：这件事直接做。只对用户的这条消息有效。做完后用一两句话告诉用户做了什么、怎么核对的，并说明没有经过人工确认。")
    print("提醒：" + NOT_SMALL)


def cmd_use(root, a):
    L.load_task(root, a.id)
    with open(os.path.join(L.flow_root(root), "active"), "w", encoding="utf-8") as f:
        f.write(a.id)
    print("当前任务：%s" % a.id)


def item_line(root, st, i):
    status = i.get("status") or "待验证"
    if i["level"] == L.DECISIVE and status in L.ITEM_OK and not L.item_ok(root, st, i):
        status = "需复核（证据被改动）"
    return "关键项 %s（%s，%s验证）：%s｜%s" % (i["id"], i["level"], i.get("verifier") or "—", i["name"], status if i["level"] == L.DECISIVE else "不单独验证")


def cmd_status(root, a):
    st = get_task(root, a)
    print("任务：%s\n状态：%s\n确认页版本：v%d" % (st["id"], st["status"], st["req_version"]))
    for i in st.get("items", []):
        print(item_line(root, st, i))
    for s in st.get("steps", []):
        print("步骤 %s：%s（用到 %s）" % (s["id"], s["status"], "、".join(s.get("uses", [])) or "无"))
    for i in st.get("issues", []):
        print("问题 %s（%s）：%s｜%s｜%s" % (i["id"], i["level"], i["text"], i.get("disposition") or "未定去向", i["status"]))
    diffs = L.diff_fingerprints(root, st) if st["status"] not in ("澄清中", "待确认") else {}
    if diffs:
        print("未记录的差异：" + "、".join("%s（%s）" % kv for kv in diffs.items()))
    print("交付检查有效：%s" % ("是" if L.has_valid_pass(root, st) else "否"))
    uncaptured = [c["path"] for c in st.get("captured", []) if not c.get("used")]
    if uncaptured:
        print("待记录的检查报告：" + "、".join(uncaptured))
    if st.get("unverified") or st.get("stop_cap_hit"):
        print("注意：任务目前未核验。")


def sync_from_sheet(root, st, approved=False):
    """关键项以任务单为准。任务单还没写完时只同步清单。"""
    rows = L.parse_items(L.sections(L.read_sheet(root, st)).get("关键项清单"))
    return L.sync_items(st, rows, approved)


def names(items):
    return "、".join("%s %s" % (i["id"], i["name"]) for i in items)


def enter_ready(root, st, a):
    parsed = L.check_sheet_complete(L.read_sheet(root, st))
    L.sync_items(st, parsed["items"])
    if not st.get("understood"):
        die("还没有记录开工前的复述确认。先把你对需求的理解和简要步骤发给用户，得到回复后运行 qflow understand --text 复述内容 --quote 用户原话。")
    last = L.latest_check(st, "清单")
    if not last:
        die("请用户确认前，要先让独立检查员检查关键项清单，并用 qflow check 记录。")
    if last.get("verdict") != "通过":
        die("最近一次清单检查的结论是“%s”。按报告改好任务单后，要让检查员重新检查，通过了才能往下走。" % last.get("verdict"))
    st["direction_sig"] = L.direction_signature(parsed)
    st["page_sig"] = L.page_signature(parsed)
    transition(st, "待确认", a.reason or "")


def require_same_direction(root, st):
    """用户确认的内容不能事后替换。"""
    parsed = L.check_sheet_complete(L.read_sheet(root, st))
    if L.direction_signature(parsed) != st.get("direction_sig"):
        die("任务单的目标、范围或判定标准和用户确认的不一致。请先运行 qflow change。")


def settle_light_change(root, st):
    """小改动：没动决定性的项时，之前通过的交付检查继续有效，改动清单留给检查员和用户核对。"""
    lp = st.pop("light_pending", None)
    if not lp:
        return
    before, after = lp.get("fingerprints", {}), st.get("fingerprints", {})
    files = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    st.setdefault("light_changes", []).append({"note": lp["note"], "ts": lp["ts"], "files": files})
    if lp.get("check_ts") is None:
        return
    for c in st["checks"]:
        if c.get("type") == "交付" and c.get("ts") == lp["check_ts"]:
            c.update(req_version=st["req_version"], outcome=L.outcome_digest(root, st), items=L.items_digest(st), carried=True)


def cmd_understand(root, a):
    st = get_task(root, a)
    if st["status"] != "澄清中":
        die("复述确认在澄清中记录，当前是 %s。" % st["status"])
    if len((a.text or "").strip()) < 10:
        die("--text 要写你发给用户的复述：要做成什么、给谁用、不动什么，以及打算分哪几步做。")
    ts = L.verify_quote(root, a.quote, st["created"])
    st["understood"] = {"text": a.text.strip(), "quote": a.quote, "ts": ts}
    L.save_task(root, st)
    print("已记录：用户确认了你的复述。接着做输入检验、列关键项、排步骤。检验中发现必须由用户决定的事，集中再问一次；没有就不用再打扰用户。")


def require_items_verified(root, st, what):
    pend = L.pending_decisive(root, st)
    if pend:
        die("还有决定性的项没有验证或确认：%s。%s前要先处理：能客观核对的用 qflow item verify --evidence，答案在用户那里的用 --quote，核对不了的交用户决定后用 qflow item waive。" % (names(pend), what))


def cmd_state(root, a):
    st = get_task(root, a)
    cur, tgt = st["status"], a.target
    if tgt in ("已暂停", "已取消"):
        if cur not in L.ACTIVE and not (cur == "已暂停" and tgt == "已取消"):
            die("当前状态不能这样切换。")
        q_ts = L.verify_quote(root, a.quote, st["last_transition"])
        if tgt == "已暂停" and cur != "受阻":
            st["resume_to"] = cur
        transition(st, tgt, "用户原话：%s（%s）" % (a.quote, L.fmt_time(q_ts)))
    elif tgt == "恢复":
        if cur == "受阻":
            if not a.reason:
                die("恢复要写明原因。")
            transition(st, st.get("resume_to") or "执行中", a.reason)
        elif cur == "已暂停":
            L.verify_quote(root, a.quote, st["last_transition"])
            transition(st, st.get("resume_to") or "执行中", "用户原话：%s" % a.quote)
        else:
            die("只有受阻和已暂停的任务能恢复。")
    elif tgt == "受阻":
        if cur not in L.ACTIVE or cur == "受阻":
            die("当前状态不能进入受阻。")
        if not a.reason:
            die("受阻要写明原因。")
        st["resume_to"] = cur
        transition(st, "受阻", a.reason)
    elif tgt == "待确认" and cur == "澄清中":
        enter_ready(root, st, a)
    elif tgt == "澄清中" and cur == "待确认":
        transition(st, "澄清中", a.reason or "用户要求修改")
    elif tgt == "待关闭" and cur == "执行中":
        if not L.steps_ready(st):
            die("步骤还不满足进入待关闭的条件：全部至少待核验。")
        sync_from_sheet(root, st)
        require_items_verified(root, st, "送检")
        block_on_diffs(root, st)
        require_same_direction(root, st)
        settle_light_change(root, st)
        transition(st, "待关闭", a.reason or "")
    else:
        die("不允许从 %s 切换到 %s。" % (cur, tgt))
    L.save_task(root, st)
    print("状态：%s" % st["status"])


def cmd_confirm(root, a):
    st = get_task(root, a)
    if st["status"] != "待确认":
        die("只有待确认的任务能确认，当前是 %s。" % st["status"])
    parsed = L.check_sheet_complete(L.read_sheet(root, st))
    if L.page_signature(parsed) != st.get("page_sig"):
        transition(st, "澄清中", "任务单在用户确认前被改动")
        L.save_task(root, st)
        die("任务单和发给用户确认的内容不一致，已退回澄清中。请把最新内容重新发给用户。")
    ts = None
    if a.quote:
        ts = L.verify_quote(root, a.quote, st["last_transition"])
        for i in st["items"]:
            if i["level"] == L.DECISIVE and i["verifier"] == "用户" and not L.item_ok(root, st, i):
                i.update(status="用户已确认", quote=a.quote, verified_ts=ts)
    elif st.get("direction_confirm"):
        die("这个任务确认之后又有变化，需要用户重新确认。请把变化告诉用户，得到新的回复后用 qflow confirm --quote。之前的回复不能再用。")
    else:
        # 第一次确认：用户已经确认过复述，“理解”类的项沿用那次回复；其余要用户定的项仍要有原话。
        u = st["understood"]
        for i in st["items"]:
            if i["level"] == L.DECISIVE and i["verifier"] == "用户" and i["type"] == "理解" and not L.item_ok(root, st, i):
                i.update(status="用户已确认", quote=u["quote"], verified_ts=u["ts"])
    lack = [i for i in L.pending_decisive(root, st) if i["verifier"] == "用户"]
    if lack:
        die("这些决定性的项要由用户决定，还没有用户原话：%s。请把它们集中问用户（带选项和推荐），得到回复后用 qflow confirm --quote；用户之前已经说清的，用 qflow item verify <编号> --quote 引用那句原话。" % names(lack))
    if st.get("baseline"):
        diffs = L.diff_fingerprints(root, st)
        if diffs:
            die("有未处理的差异：%s。这些改动用户并不知情，请先用 qflow fingerprint accept 处理，再确认。" % "、".join("%s（%s）" % kv for kv in diffs.items()))
    L.apply_parsed(st, parsed)
    st["direction_confirm"] = {"version": st["req_version"], "ts": ts or L.now(), "quote": a.quote}
    if a.review:
        st["review"] = True
    L.make_baseline(root, st)
    transition(st, "执行中", "用户确认" if a.quote else "用户已确认复述，没有其他要用户决定的项")
    L.save_task(root, st)
    print("已确认 v%d，可以开始执行。\n允许改动：%s\n禁止改动：%s" % (st["req_version"], "、".join(st["scope"]["allowed"]) or "无文件成果", "、".join(st["scope"]["forbidden"]) or "无"))
    pend = L.pending_decisive(root, st)
    if pend:
        print("还要验证的决定性的项：%s。用到它们的步骤开始前要先验证。" % names(pend))


def cmd_change(root, a):
    st = get_task(root, a)
    if st["status"] not in ("执行中", "待关闭"):
        die("当前状态 %s 不走变更流程。澄清中和待确认直接改任务单。" % st["status"])
    parsed = L.check_sheet_complete(L.read_sheet(root, st))
    if a.quote:
        L.verify_quote(root, a.quote, st["last_transition"])
    reset, need = L.sync_items(st, parsed["items"], approved=bool(a.quote))
    for iid in a.recheck or []:
        it = next((i for i in st["items"] if i["id"] == iid), None)
        if not it:
            die("没有关键项 %s。" % iid)
        it["status"] = "待验证"
        it.pop("evidence", None)
        it.pop("quote", None)
        reset.append(iid)
    L.mark_steps_recheck(st, reset)
    changed = L.direction_signature(parsed) != st.get("direction_sig")
    if a.light:
        if changed or reset or a.reconfirm or need:
            die("这次改动涉及目标、范围、成功标准或决定性的项，不能按小改动处理。去掉 --light，按正常变更走。")
        base = [c for c in L.checks_valid(root, st) if c.get("verdict") in ("通过", "用户裁定通过")]
        st["light_pending"] = {"note": a.note, "ts": L.now(), "check_ts": base[-1]["ts"] if base else None, "fingerprints": dict(st.get("fingerprints", {}))}
    st["req_version"] += 1
    st["changes"].append({"version": st["req_version"], "ts": L.now(), "note": a.note, "reconfirm": bool(a.reconfirm or changed), "recheck": reset, "approved": need, "quote": a.quote})
    L.apply_parsed(st, parsed)
    if a.reconfirm or changed:
        st["direction_sig"] = L.direction_signature(parsed)
        st["page_sig"] = L.page_signature(parsed)
        transition(st, "待确认", "变更：%s" % a.note)
        msg = "目标、范围或判定标准有变化，已退回待确认，请让用户确认。"
    else:
        if st["status"] != "执行中":
            transition(st, "执行中", "变更：%s" % a.note)
        msg = "确认页已升到 v%d，继续执行。" % st["req_version"]
        if a.light:
            msg += "已按小改动登记：改完后自己核对“这一处改对了、别处没动”，把差异存进 evidence/，再运行 qflow state 待关闭。之前的验证和检查结论继续有效，不用再调检查员。"
    if st.get("light_pending") is not None and not a.light:
        st.pop("light_pending", None)
    L.save_task(root, st)
    print(msg)
    if reset:
        print("这些关键项要重新验证：%s。用到它们的步骤已标为需复核。" % "、".join(reset))


def cmd_item(root, a):
    st = get_task(root, a)
    if st["status"] not in L.ACTIVE:
        die("任务不在进行中。")
    sync_from_sheet(root, st)
    L.save_task(root, st)
    if a.action == "list":
        for i in st["items"]:
            print(item_line(root, st, i))
        return
    it = next((i for i in st["items"] if i["id"] == a.id), None)
    if not it:
        die("关键项清单里没有 %s。先把它写进任务单的“关键项清单”。" % a.id)
    if it["level"] != L.DECISIVE:
        die("%s 不是决定性的项，不需要单独验证。" % a.id)
    created = it.get("created", st["created"])
    if a.action == "verify":
        if a.evidence:
            if it["verifier"] == "用户":
                die("%s 的答案在用户那里，要用用户原话确认（--quote），不能自己核对。" % a.id)
            real = os.path.realpath(a.evidence if os.path.isabs(a.evidence) else os.path.join(os.getcwd(), a.evidence))
            evdir = os.path.realpath(os.path.join(L.task_dir(root, st["id"]), "evidence"))
            if not real.startswith(evdir + os.sep) or not os.path.isfile(real) or os.path.getsize(real) == 0:
                die("证据要是这个任务 evidence/ 目录下已有的、非空的文件：%s" % evdir)
            used = [i["id"] for i in st["items"] if i is not it and (i.get("evidence") or {}).get("file") == os.path.relpath(real, evdir)]
            it.update(status="已验证", evidence={"file": os.path.relpath(real, evdir), "hash": L.sha(real)}, verified_ts=L.now())
            it.pop("quote", None)
            print("%s 已验证，证据：%s" % (a.id, os.path.relpath(real, evdir)))
            if used:
                print("提醒：这份证据也用于 %s，请确认它确实分别支持这几项。" % "、".join(used))
        elif a.quote:
            ts = L.verify_quote(root, a.quote, 0, short_after=max(created, st["last_transition"] if st["status"] == "待确认" else created))
            it.update(status="用户已确认", quote=a.quote, verified_ts=ts)
            it.pop("evidence", None)
            print("%s 已由用户确认。" % a.id)
        else:
            die("要给出证据（--evidence 文件）或用户原话（--quote）。")
    elif a.action == "waive":
        ts = L.verify_quote(root, a.quote, created)
        it.update(status=L.WAIVED, quote=a.quote, verified_ts=ts)
        it.pop("evidence", None)
        print("%s 记为未验证，用户同意继续使用。最终交付必须声明，并由用户确认成果。" % a.id)
    elif a.action == "recheck":
        if not a.reason:
            die("要写明原因（--reason）。")
        it["status"] = "待验证"
        it.pop("evidence", None)
        it.pop("quote", None)
        L.mark_steps_recheck(st, [a.id])
        if st["status"] == "待关闭":
            transition(st, "执行中", "关键项 %s 要重新验证：%s" % (a.id, a.reason))
        print("%s 退回待验证。用到它的步骤已标为需复核。" % a.id)
    L.save_task(root, st)


STEP_FLOW = {
    "待开始": {"进行中", "受阻"},
    "进行中": {"待核验", "受阻"},
    "待核验": {"进行中", "需复核"},
    "不合格": {"返工"},
    "返工": {"待核验", "受阻"},
    "受阻": {"进行中", "返工"},
    "通过": {"需复核"},
    "未核验": {"需复核"},
    "需复核": {"进行中", "待核验"},
}


def block_on_diffs(root, st):
    diffs = L.diff_fingerprints(root, st)
    if diffs:
        st["pending_diffs"] = {k: {"kind": v, "ts": L.now()} for k, v in diffs.items()}
        L.save_task(root, st)
        die("发现未记录的差异：%s。请先说明来源，并用 qflow fingerprint accept 处理。" % "、".join("%s（%s）" % kv for kv in diffs.items()))


def cmd_step(root, a):
    st = get_task(root, a)
    if st["status"] not in ("执行中", "受阻"):
        die("只有执行中的任务能更新步骤。")
    step = next((s for s in st["steps"] if s["id"] == a.id), None)
    if not step:
        die("没有步骤 %s。" % a.id)
    if a.status == "通过":
        die("“通过”只能来自独立检查结果，请用 qflow check。")
    allowed = STEP_FLOW.get(step["status"], set())
    if a.status not in allowed:
        die("步骤不能从 %s 变为 %s。可选：%s" % (step["status"], a.status, "、".join(sorted(allowed))))
    if a.status in ("进行中", "返工"):
        sync_from_sheet(root, st)
        pend = L.pending_decisive(root, st, set(step.get("uses", [])))
        if pend:
            L.save_task(root, st)
            die("这一步用到的决定性的项还没有验证或确认：%s。先验证，再开始这一步。" % names(pend))
    if a.status == "待核验":
        block_on_diffs(root, st)
    step["status"] = a.status
    L.save_task(root, st)
    print("步骤 %s：%s" % (a.id, a.status))


def cmd_issue(root, a):
    st = get_task(root, a)
    if a.action == "add":
        if a.level not in ISSUE_DISPOSITIONS:
            die("级别只能是阻断、重要或建议。")
        iid = "Q%d" % (len(st["issues"]) + 1)
        st["issues"].append({"id": iid, "level": a.level, "text": a.text or "", "disposition": None, "status": "开放", "bounces": 0})
        print("已记录 %s" % iid)
    else:
        issue = next((i for i in st["issues"] if i["id"] == a.id), None)
        if not issue:
            die("没有问题 %s。" % a.id)
        if a.action == "route":
            if a.disposition not in ISSUE_DISPOSITIONS[issue["level"]]:
                die("%s问题不能选“%s”。可选：%s" % (issue["level"], a.disposition, "、".join(sorted(ISSUE_DISPOSITIONS[issue["level"]]))))
            if a.disposition in ("交用户决定", "延后"):
                L.verify_quote(root, a.quote, st["last_transition"])
                issue["status"] = "用户已决定"
                issue["user_quote"] = a.quote
            elif a.disposition == "只记录":
                issue["status"] = "已记录"
            issue["disposition"] = a.disposition
            print("%s 去向：%s" % (issue["id"], a.disposition))
            if a.disposition == "修复" and issue["level"] in ("阻断", "重要") and st["status"] == "待关闭":
                transition(st, "执行中", "问题 %s 要修复" % issue["id"])
                for s in st["steps"]:
                    if s["status"] in ("通过", "待核验", "未核验"):
                        s["status"] = "需复核"
                print("任务已退回执行中。修复后相关步骤要重新检查：qflow step <编号> 待核验。")
        elif a.action == "bounce":
            issue["bounces"] = issue.get("bounces", 0) + 1
            if issue["bounces"] >= 2:
                if st["status"] != "受阻":
                    st["resume_to"] = st["status"]
                    transition(st, "受阻", "问题 %s 退回 2 次仍未解决" % issue["id"])
                print("问题 %s 已退回 2 次，任务转为受阻，请交给用户：说明问题、试过什么、可能根因和可选方案。" % issue["id"])
            else:
                print("问题 %s 退回 %d 次。" % (issue["id"], issue["bounces"]))
    L.save_task(root, st)


ITEM_VERDICTS = ("成立", "不成立", "无法判断", "未验证")


def parse_report(text):
    def field(name):
        m = re.search(r"^\s*(?:[-*]\s*)?\**%s\**\s*[:：]\s*\**\s*(.+?)\s*\**\s*$" % name, text, flags=re.M)
        return m.group(1).strip() if m else None
    verdict = field("结论") or ""
    m = re.match(r"(不通过|通过|弃权)", verdict)
    items = {}
    for k, v in re.findall(r"^\s*(?:[-*]\s*)?\**(K\d+)\**\s*[:：]\s*\**\s*(不成立|成立|无法判断|未验证)", text, flags=re.M):
        items[k] = v
    return {"verdict": m.group(1) if m else None, "type": field("类型"), "items": items}


def cmd_check(root, a):
    st = get_task(root, a)
    if a.user_ruling:
        if st["status"] != "待关闭":
            die("用户裁定只用于待关闭的任务。")
        L.verify_quote(root, a.quote, st["last_transition"])
        if not a.issues:
            die("用户裁定要写明问题编号（--issues）。")
        if not [c for c in st["checks"] if c.get("type") == "交付" and c.get("req_version") == st["req_version"]]:
            die("用户裁定前要先有一次交付检查。")
        for iid in a.issues or []:
            issue = next((i for i in st["issues"] if i["id"] == iid), None)
            if not issue:
                die("没有问题 %s。" % iid)
            issue.update({"status": "用户已决定", "disposition": "交用户决定", "user_quote": a.quote})
        if L.open_blockers(st):
            die("还有未处理的阻断问题：%s" % "、".join(i["id"] for i in L.open_blockers(st)))
        require_items_verified(root, st, "裁定")
        record_check(root, st, "交付", "用户裁定通过", "用户裁定", None)
        finish_overall(root, st, "用户裁定通过", {})
        return
    if not a.report:
        die("需要 --report <报告路径>。")
    rel, real = L.rel_to(root, a.report)
    if not os.path.isfile(real):
        die("找不到报告：%s" % a.report)
    try:
        with open(real, encoding="utf-8") as f:
            text = f.read()
    except UnicodeDecodeError:
        die("报告必须是 UTF-8 文本。")
    info = parse_report(text)
    verdict = info["verdict"]
    if verdict not in VERDICTS:
        die("报告要有一行“结论：通过|不通过|弃权”。")
    ctype = info["type"]
    if ctype not in ("清单", "交付"):
        die("报告要有一行“类型：清单|交付”。")
    meta = L.sha(real)
    captured = next((c for c in st.get("captured", []) if os.path.realpath(c["path"]) == os.path.realpath(real) and c["hash"] == meta), None)
    if captured:
        if captured.get("used"):
            die("这份报告已经记录过。请重新调用检查员。")
        if ctype == "交付":
            if captured.get("tampered"):
                die("这次检查期间成果被改动，报告作废，请重新检查。")
            if captured.get("req_version") != st["req_version"] or captured.get("outcome") != L.outcome_digest(root, st) or captured.get("items") != L.items_digest(st):
                die("这份报告对应的是旧版本的确认页、成果或关键项，已失效。请重新调用检查员。")
        source = "hook 捕获"
    else:
        L.verify_quote(root, a.quote, st["last_transition"]) if a.quote else die("这份报告不是由 hook 捕获的，作者不能自己判定通过。如果确实无法捕获，要让用户同意后用 --quote 记录。")
        source = "作者保存"
    if ctype == "清单":
        if captured:
            captured["used"] = True
        record_check(root, st, ctype, verdict, source, rel)
        st["checks"][-1]["report_hash"] = meta
        L.save_task(root, st)
        print("清单检查已记录：%s。%s" % (verdict, "请按报告补全关键项清单和理解，再请用户确认。" if verdict != "通过" else ""))
        return
    if st["status"] != "待关闭":
        die("交付检查要先进入待关闭（qflow state 待关闭）。")
    decisive = [i for i in st["items"] if i["level"] == L.DECISIVE]
    missing = [i for i in decisive if i["id"] not in info["items"]]
    if verdict != "弃权" and missing:
        die("报告没有逐项给出这些决定性的项的结论：%s。请让检查员按“K1：成立|不成立|无法判断”的格式补全后重新检查。" % names(missing))
    failed = [i for i in decisive if info["items"].get(i["id"]) == "不成立"]
    unsure = [i for i in decisive if info["items"].get(i["id"]) == "无法判断" or (info["items"].get(i["id"]) == "未验证" and i.get("status") != L.WAIVED)]
    if verdict == "通过" and (failed or unsure):
        die("报告的结论是通过，但这些决定性的项不成立或无法判断：%s。结论和逐项结果矛盾，请重新检查。" % names(failed + unsure))
    if captured:
        captured["used"] = True
    record_check(root, st, ctype, verdict, source, rel)
    st["checks"][-1]["report_hash"] = meta
    st["checks"][-1]["item_results"] = info["items"]
    finish_overall(root, st, verdict, {i["id"] for i in failed})


def record_check(root, st, ctype, verdict, source, rel):
    st["checks"].append({"type": ctype, "verdict": verdict, "source": source, "report": rel, "ts": L.now(), "req_version": st["req_version"], "outcome": L.outcome_digest(root, st), "items": L.items_digest(st)})


def needs_result_confirm(st):
    return bool(st.get("review")) or any(i["level"] == L.DECISIVE and i.get("status") == L.WAIVED for i in st["items"])


def finish_overall(root, st, verdict, failed_items):
    if verdict == "不通过":
        transition(st, "执行中", "交付检查不通过")
        for i in st["items"]:
            if i["id"] in failed_items:
                i["status"] = "待验证"
                i.pop("evidence", None)
                i.pop("quote", None)
        for s in st["steps"]:
            s["status"] = "不合格" if s["status"] in ("待核验", "通过") else s["status"]
        print("交付检查不通过，已退回执行中。")
        if failed_items:
            print("这些决定性的项验证不成立，已退回待验证，要回到这一项重新验证，不要在下游打补丁：%s" % "、".join(sorted(failed_items)))
    elif verdict == "弃权":
        print("检查员弃权：这部分需要人工判断。请交给用户，由用户裁定或同意标为未核验。")
    else:
        if verdict == "通过":
            for i in st["issues"]:
                if i["status"] == "开放" and i.get("disposition") == "修复":
                    i["status"] = "已解决"
        for s in st["steps"]:
            s["status"] = "通过"
        if needs_result_confirm(st):
            print("交付检查%s。这个任务的成果要由用户确认，得到回复后用 qflow close --quote。" % verdict)
        else:
            print("交付检查%s，可以关闭。" % verdict)
    L.save_task(root, st)


def cmd_fingerprint(root, a):
    st = get_task(root, a)
    if a.action == "scan":
        diffs = L.diff_fingerprints(root, st)
        if not diffs:
            print("没有未记录的差异。")
        else:
            st["pending_diffs"] = {k: {"kind": v, "ts": L.now()} for k, v in diffs.items()}
            L.save_task(root, st)
            print("未记录的差异：" + "、".join("%s（%s）" % kv for kv in diffs.items()))
    elif a.action == "snapshot":
        if not a.path:
            die("要写明快照文件路径。")
        real = os.path.realpath(a.path)
        evidence = os.path.realpath(os.path.join(L.task_dir(root, st["id"]), "evidence"))
        if not real.startswith(evidence + os.sep) or not os.path.isfile(real):
            die("快照只能是这个任务 evidence/ 目录下已有的文件。")
        st.setdefault("snapshots", {})[os.path.basename(real)] = L.sha(real)
        L.save_task(root, st)
        print("已记录快照：%s" % os.path.basename(real))
    elif a.action == "accept":
        if a.source not in ("own", "user", "unknown"):
            die("--source 只能是 own、user 或 unknown。")
        if not (a.reason or "").strip():
            die("要写明来源和理由（--reason）。")
        diffs = L.diff_fingerprints(root, st)
        if a.all_pending:
            targets = sorted(diffs)
        else:
            if not a.path:
                die("要写明路径，或用 --all-pending。")
            rel, _ = L.rel_to(root, a.path)
            if rel is None:
                die("路径不在项目内。")
            targets = [k for k in sorted(diffs) if k == rel or k.startswith(rel.rstrip("/") + "/")]
        if not targets:
            die("这个路径没有未记录的差异。")
        outside = [t for t in targets if not L.in_scope(st, t)]
        if a.source == "own" and outside:
            die("这些改动超出了已确认的范围：%s。立即停止并告诉用户：能用基线恢复就恢复；用户同意保留时，用 --source user 和用户原话记录。" % "、".join(outside))
        if a.source != "own":
            times = [st.get("pending_diffs", {}).get(t, {}).get("ts", st["last_transition"]) for t in targets]
            L.verify_quote(root, a.quote, min(times))
        current = L.scan_project(root)
        for t in targets:
            if t in current:
                st["fingerprints"][t] = current[t]
            else:
                st["fingerprints"].pop(t, None)
            st.get("pending_diffs", {}).pop(t, None)
            st.setdefault("accepted_diffs", []).append({"path": t, "source": a.source, "reason": a.reason, "ts": L.now(), "quote": a.quote})
        L.save_task(root, st)
        print("已接受差异：%s（来源：%s）" % ("、".join(targets), a.source))


def cmd_close(root, a):
    st = get_task(root, a)
    if st["status"] != "待关闭":
        die("要先进入待关闭并通过交付检查，当前是 %s。" % st["status"])
    label = ""
    block_on_diffs(root, st)
    require_same_direction(root, st)
    require_items_verified(root, st, "关闭")
    if a.unverified:
        L.verify_quote(root, a.quote, st["last_transition"])
        if L.open_blockers(st):
            die("还有未处理的问题：%s" % "、".join(i["id"] for i in L.open_blockers(st)))
        st["unverified"] = True
        record_check(root, st, "交付", "未核验", "用户同意", None)
        label = "未经独立核验"
        st["result_confirm"] = {"version": st["req_version"], "ts": L.now(), "quote": a.quote}
    else:
        if not L.has_valid_pass(root, st):
            die("没有绑定当前确认页、成果和关键项的交付检查通过记录。它们变化后，要重新检查。")
        if L.open_blockers(st):
            die("还有未处理的阻断问题：%s" % "、".join(i["id"] for i in L.open_blockers(st)))
        if needs_result_confirm(st):
            last = max(c["ts"] for c in L.checks_valid(root, st))
            if not a.quote:
                die("这个任务的成果要由用户确认（有未验证而继续使用的决定性的项，或用户要求过目）。请把成果和验证情况发给用户，得到回复后用 --quote。")
            ts = L.verify_quote(root, a.quote, last)
            st["result_confirm"] = {"version": st["req_version"], "ts": ts, "quote": a.quote}
    transition(st, "已关闭", label or "完成")
    L.save_task(root, st)
    notes = []
    waived = [i for i in st["items"] if i["level"] == L.DECISIVE and i.get("status") == L.WAIVED]
    if waived:
        notes.append("最终交付必须声明：这些决定性的项未验证，用户同意继续使用：%s。" % names(waived))
    if st.get("unverified"):
        notes.append("最终交付必须声明：未经独立核验。")
    if any(c.get("verdict") == "用户裁定通过" for c in st["checks"]):
        notes.append("最终交付必须声明：用户接受已知问题，并列出问题。")
    if st.get("light_changes"):
        notes.append("最终交付必须列出按小改动处理、没有重新检查的改动：%s。" % "；".join(x["note"] for x in st["light_changes"]))
    if st["scope"].get("no_file_output"):
        notes.append("最终交付必须声明：程序无法核查这部分成果。")
    if not st.get("result_confirm"):
        notes.append("最终交付必须声明：成果没有经过人工确认。")
    if any(c.get("source") == "作者保存" for c in st["checks"] if c.get("req_version") == st["req_version"]):
        notes.append("最终交付要说明：检查报告由作者保存，无法防止改动。")
    notes.append("最终交付要逐项写明每个决定性的项是怎么验证的、由谁验证的。")
    print("任务已关闭。" + " ".join(notes))


def build_parser():
    p = argparse.ArgumentParser(prog="qflow")
    p.add_argument("--task")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start"); s.add_argument("--title", required=True); s.add_argument("--request", default=""); s.set_defaults(fn=cmd_start)
    s = sub.add_parser("direct"); s.add_argument("--restate", required=True); s.add_argument("--items", required=True); s.add_argument("--checked", default=""); s.add_argument("--quote", required=True); s.set_defaults(fn=cmd_direct)
    s = sub.add_parser("use"); s.add_argument("id"); s.set_defaults(fn=cmd_use)
    s = sub.add_parser("status"); s.set_defaults(fn=cmd_status)
    s = sub.add_parser("understand"); s.add_argument("--text", required=True); s.add_argument("--quote", required=True); s.set_defaults(fn=cmd_understand)
    s = sub.add_parser("state"); s.add_argument("target"); s.add_argument("--reason"); s.add_argument("--quote"); s.set_defaults(fn=cmd_state)
    s = sub.add_parser("item"); s.add_argument("action", choices=["list", "verify", "waive", "recheck"]); s.add_argument("id", nargs="?"); s.add_argument("--evidence"); s.add_argument("--quote"); s.add_argument("--reason"); s.set_defaults(fn=cmd_item)
    s = sub.add_parser("confirm"); s.add_argument("--quote"); s.add_argument("--review", action="store_true"); s.set_defaults(fn=cmd_confirm)
    s = sub.add_parser("change"); s.add_argument("--note", required=True); s.add_argument("--reconfirm", action="store_true"); s.add_argument("--light", action="store_true"); s.add_argument("--quote"); s.add_argument("--recheck", nargs="*"); s.set_defaults(fn=cmd_change)
    s = sub.add_parser("step"); s.add_argument("id"); s.add_argument("status"); s.set_defaults(fn=cmd_step)
    s = sub.add_parser("issue"); s.add_argument("action", choices=["add", "route", "bounce"]); s.add_argument("--id"); s.add_argument("--level"); s.add_argument("--text"); s.add_argument("--disposition"); s.add_argument("--quote"); s.set_defaults(fn=cmd_issue)
    s = sub.add_parser("check"); s.add_argument("--report"); s.add_argument("--quote"); s.add_argument("--user-ruling", action="store_true"); s.add_argument("--issues", nargs="*"); s.set_defaults(fn=cmd_check)
    s = sub.add_parser("fingerprint"); s.add_argument("action", choices=["scan", "accept", "snapshot"]); s.add_argument("path", nargs="?"); s.add_argument("--source"); s.add_argument("--reason"); s.add_argument("--quote"); s.add_argument("--all-pending", action="store_true"); s.set_defaults(fn=cmd_fingerprint)
    s = sub.add_parser("close"); s.add_argument("--quote"); s.add_argument("--unverified", action="store_true"); s.set_defaults(fn=cmd_close)
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    root = L.project_root()
    try:
        with L.flow_lock(root):
            a.fn(root, a)
    except L.FlowError as e:
        print("未通过：%s" % e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
