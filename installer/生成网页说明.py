#!/usr/bin/env python3
"""把 安装说明.md 转成 安装说明.html。只处理说明里用到的 Markdown 写法，只用标准库。"""
import html
import os
import sys
import re

HERE = os.path.dirname(os.path.abspath(__file__))


def inline(t):
    t = html.escape(t, quote=False)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2" target="_blank">\1</a>', t)
    return t


def convert(md):
    out, lines, i = [], md.splitlines(), 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            i += 1
            buf = []
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(lines[i])
                i += 1
            out.append('<pre><button class="copy">复制</button><code>%s</code></pre>' % html.escape("\n".join(buf)))
            i += 1
            continue
        if line.strip() == "---":
            out.append("<hr>")
        elif m := re.match(r"^(#{1,4})\s+(.*)", line):
            n = len(m.group(1))
            out.append("<h%d>%s</h%d>" % (n, inline(m.group(2)), n))
        elif line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                if not re.match(r"^\|[\s:|-]+\|$", lines[i]):
                    rows.append([c.strip() for c in lines[i].strip("|").split("|")])
                i += 1
            out.append("<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>" % (
                "".join("<th>%s</th>" % inline(c) for c in rows[0]),
                "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % inline(c) for c in r) for r in rows[1:])))
            continue
        elif re.match(r"^\s*([-*]|\d+\.)\s+", line):
            items = []
            while i < len(lines) and (re.match(r"^\s*([-*]|\d+\.)\s+", lines[i]) or (lines[i].startswith("   ") and lines[i].strip() and not lines[i].strip().startswith("```"))):
                items.append(lines[i])
                i += 1
            out.append(render_list(items))
            continue
        elif line.strip():
            buf = [line]
            while i + 1 < len(lines) and lines[i + 1].strip() and not re.match(r"^(#|\||```|---|\s*([-*]|\d+\.)\s)", lines[i + 1]):
                i += 1
                buf.append(lines[i])
            out.append("<p>%s</p>" % "<br>".join(inline(b) for b in buf))
        i += 1
    return "\n".join(out)


def render_list(items):
    html_out, stack = [], []
    for raw in items:
        m = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)", raw)
        if not m:
            html_out.append("<p>%s</p>" % inline(raw.strip()))
            continue
        depth = len(m.group(1)) // 2
        tag = "ol" if m.group(2)[0].isdigit() else "ul"
        while len(stack) > depth + 1:
            html_out.append("</li></%s>" % stack.pop())
        if len(stack) < depth + 1:
            start = m.group(2)[:-1] if tag == "ol" else "1"
            html_out.append("<ol start=\"%s\">" % start if tag == "ol" and start != "1" else "<%s>" % tag)
            stack.append(tag)
        elif html_out and html_out[-1] != "<%s>" % tag:
            html_out.append("</li>")
        html_out.append("<li>%s" % inline(m.group(3)))
    while stack:
        html_out.append("</li></%s>" % stack.pop())
    return "".join(html_out)


CSS = """
:root{--bg:#fbfaf7;--fg:#1f2328;--muted:#5b6470;--line:#e3e0d8;--code:#f1eee6;--accent:#2f5d8a;--th:#f4f1ea}
@media (prefers-color-scheme:dark){:root{--bg:#17191c;--fg:#e6e3dc;--muted:#a3a9b1;--line:#30343a;--code:#22262b;--accent:#8fb6dd;--th:#1f2226}}
body{background:var(--bg);color:var(--fg);font:16px/1.75 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;margin:0}
main{max-width:860px;margin:0 auto;padding:32px 16px 80px}
h1{font-size:28px;margin:0 0 4px}h2{font-size:22px;margin:40px 0 12px;padding-top:8px}h3{font-size:18px;margin:28px 0 8px}h4{font-size:16px;margin:22px 0 6px}
hr{border:0;border-top:1px solid var(--line);margin:32px 0}
table{border-collapse:collapse;width:100%;margin:12px 0;font-size:15px;display:block;overflow-x:auto}
th,td{border:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top}th{background:var(--th)}
code{background:var(--code);padding:1px 5px;border-radius:4px;font:14px/1.5 ui-monospace,Menlo,Consolas,monospace}
pre{position:relative;background:var(--code);padding:14px 16px;border-radius:8px;overflow-x:auto}
pre code{background:none;padding:0;white-space:pre-wrap;word-break:break-all}
.copy{position:absolute;top:8px;right:8px;border:1px solid var(--line);background:var(--bg);color:var(--fg);border-radius:6px;padding:2px 10px;cursor:pointer;font-size:13px}
a{color:var(--accent)}li{margin:4px 0}
"""
JS = """document.querySelectorAll('.copy').forEach(b=>b.onclick=()=>{const t=b.nextElementSibling.innerText;
(navigator.clipboard?navigator.clipboard.writeText(t):Promise.reject()).then(()=>{b.textContent='已复制';setTimeout(()=>b.textContent='复制',1500)}).catch(()=>{const r=document.createRange();r.selectNodeContents(b.nextElementSibling);const s=getSelection();s.removeAllRanges();s.addRange(r);b.textContent='已选中，请按复制键'});});"""

TARGET = sys.argv[1] if len(sys.argv) > 1 else HERE
for name in ("安装说明", "快速安装", "升级说明"):
    md = open(os.path.join(TARGET, name + ".md"), encoding="utf-8").read()
    title = md.splitlines()[0].lstrip("# ").strip()
    page = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>%s</title><style>%s</style></head><body><main>%s</main><script>%s</script></body></html>' % (html.escape(title), CSS, convert(md), JS)
    open(os.path.join(TARGET, name + ".html"), "w", encoding="utf-8").write(page)
    print("已生成 %s.html" % name)
