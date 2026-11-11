"""HTML 渲染辅助：转义、页面骨架与简易 diff 渲染。"""
from __future__ import annotations

import difflib
import html
import json


def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def page_shell(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} - 采购公告采集</title>
<style>
:root {{ --line:#e3e7ee; --mut:#6b7280; --brand:#1d4ed8; --bg:#f6f8fb; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; font-family:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
       color:#1f2937; background:var(--bg); }}
header {{ background:#0f2a5c; color:#fff; padding:14px 24px; }}
header a {{ color:#cfe0ff; text-decoration:none; margin-right:18px; font-size:14px; }}
header b {{ font-size:18px; }}
main {{ max-width:1100px; margin:22px auto; padding:0 18px; }}
.card {{ background:#fff; border:1px solid var(--line); border-radius:10px; padding:18px 20px; margin-bottom:18px; }}
table {{ width:100%; border-collapse:collapse; font-size:14px; }}
th,td {{ text-align:left; padding:9px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ color:var(--mut); font-weight:600; background:#fafbfd; }}
form.filters {{ display:flex; flex-wrap:wrap; gap:10px; align-items:center; }}
form.filters input, form.filters select {{ padding:8px 10px; border:1px solid var(--line);
       border-radius:8px; font-size:14px; min-width:130px; }}
button {{ padding:8px 18px; border:0; border-radius:8px; background:var(--brand); color:#fff;
       font-size:14px; cursor:pointer; }}
.mut {{ color:var(--mut); font-size:13px; }}
.tag {{ display:inline-block; padding:2px 9px; border-radius:999px; font-size:12px;
       background:#eef2ff; color:#3730a3; margin-right:6px; }}
.tag.green {{ background:#e7f6ec; color:#166534; }}
.tag.red {{ background:#fdeaea; color:#991b1b; }}
.tag.amber {{ background:#fef3e2; color:#92400e; }}
.tag.gray {{ background:#eef0f3; color:#475569; }}
pre.body {{ white-space:pre-wrap; word-break:break-word; background:#fafbfd;
       border:1px solid var(--line); border-radius:8px; padding:12px; font-size:14px; }}
.diff-add {{ background:#dcfce7; }}
.diff-del {{ background:#fee2e2; text-decoration:line-through; }}
.diff-hunk {{ color:var(--mut); font-size:12px; margin-top:10px; }}
a {{ color:var(--brand); }}
.stat {{ display:inline-block; margin-right:22px; font-size:14px; }}
.stat b {{ font-size:20px; }}
</style>
</head>
<body>
<header>
  <b>采购公告采集工具</b>
  <nav style="display:inline-block;margin-left:26px">
    <a href="/">公告检索</a><a href="/runs">采集记录</a>
  </nav>
</header>
<main>{body}</main>
</body>
</html>"""


def status_tag(status: str) -> str:
    cls = {
        "success": "green", "running": "", "partial": "amber",
        "failed": "red", "soft_blocked": "red",
    }.get(status, "gray")
    return f'<span class="tag {cls}">{esc(status)}</span>'


def render_text_diff(old: str, new: str) -> str:
    """逐行（这里按句/换行切分）对比正文，高亮删除与新增。"""
    old_lines = (old or "").splitlines() or [""]
    new_lines = (new or "").splitlines() or [""]
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
    parts = ['<pre class="body">']
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for line in old_lines[i1:i2]:
                parts.append(f"{esc(line)}\n")
        elif tag == "delete":
            for line in old_lines[i1:i2]:
                parts.append(f'<span class="diff-del">- {esc(line)}</span>\n')
        elif tag == "insert":
            for line in new_lines[j1:j2]:
                parts.append(f'<span class="diff-add">+ {esc(line)}</span>\n')
        else:  # replace
            for line in old_lines[i1:i2]:
                parts.append(f'<span class="diff-del">- {esc(line)}</span>\n')
            for line in new_lines[j1:j2]:
                parts.append(f'<span class="diff-add">+ {esc(line)}</span>\n')
    parts.append("</pre>")
    return "".join(parts)


def render_json_pretty(raw: str | None) -> str:
    if not raw:
        return '<p class="mut">无</p>'
    try:
        obj = json.loads(raw)
        pretty = json.dumps(obj, ensure_ascii=False, indent=2)
    except (json.JSONDecodeError, TypeError):
        pretty = raw
    return f'<pre class="body">{esc(pretty)}</pre>'
