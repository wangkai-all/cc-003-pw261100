"""Web 服务入口：检索、公告详情/版本对比、采集记录。"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from ..db import open_db
from .render import (esc, page_shell, render_json_pretty, render_text_diff,
                     status_tag)


class WebHandler(BaseHTTPRequestHandler):
    server_version = "ProcurementWeb/1.0"

    @property
    def db_path(self) -> str:
        return self.server.db_path  # type: ignore[attr-defined]

    def _send(self, body: str, status: int = 200) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        try:
            if parsed.path == "/":
                self._page_search(query)
            elif parsed.path == "/notice":
                self._page_notice(query)
            elif parsed.path == "/diff":
                self._page_diff(query)
            elif parsed.path == "/runs":
                self._page_runs()
            elif parsed.path == "/api/search":
                self._api_search(query)
            else:
                self._send(page_shell("未找到", "<p>404</p>"), 404)
        except Exception as exc:  # 页面异常不应让整个服务退出
            self._send(
                page_shell("出错了", f'<div class="card"><p>页面渲染失败：{esc(exc)}</p>'
                                    '<p><a href="/">返回检索</a></p></div>'),
                500,
            )

    # ---------------- 检索页 ----------------
    def _page_search(self, q: dict) -> None:
        db = open_db(self.db_path)
        try:
            rows = self._query(db, q)
            counts = {
                "announcements": db.execute(
                    "SELECT COUNT(*) c FROM announcement").fetchone()["c"],
                "records": db.execute(
                    "SELECT COUNT(*) c FROM source_record").fetchone()["c"],
                "versions": db.execute(
                    "SELECT COUNT(*) c FROM version").fetchone()["c"],
            }
            region_rows = db.execute(
                "SELECT DISTINCT region FROM announcement WHERE region != '' ORDER BY region"
            ).fetchall()
            regions = [r["region"] for r in region_rows]
        finally:
            db.close()

        opts = "".join(
            f'<option value="{esc(r)}"{" selected" if q.get("region") == r else ""}>'
            f'{esc(r)}</option>' for r in regions
        )
        rows_html = "".join(
            "<tr>"
            f'<td><a href="/notice?id={r["id"]}">{esc(r["title"])}</a></td>'
            f'<td>{esc(r["purchaser"] or "-")}</td>'
            f'<td>{esc(r["notice_no"] or "<span class=mut>无编号</span>")}</td>'
            f'<td>{esc(r["region"] or "-")}</td>'
            f'<td>{esc(r["published_at"] or "-")}</td>'
            f'<td><span class="tag">{r["version_count"]} 个版本</span>'
            f'<span class="tag gray">{r["source_count"]} 个来源</span></td>'
            "</tr>" for r in rows
        ) or '<tr><td colspan="6" class="mut">没有匹配的公告</td></tr>'

        body = f"""
        <div class="card">
          <span class="stat">公告总数 <b>{counts["announcements"]}</b></span>
          <span class="stat">来源记录 <b>{counts["records"]}</b></span>
          <span class="stat">历史版本 <b>{counts["versions"]}</b></span>
        </div>
        <div class="card">
          <form class="filters" method="get" action="/">
            <input type="text" name="keyword" placeholder="关键词（空格分多词，AND）"
                   value="{esc(q.get("keyword", ""))}" style="min-width:240px">
            <select name="region"><option value="">全部地区</option>{opts}</select>
            <input type="date" name="date_from" value="{esc(q.get("date_from", ""))}">
            <span>至</span>
            <input type="date" name="date_to" value="{esc(q.get("date_to", ""))}">
            <button type="submit">筛选</button>
            <a class="mut" href="/">清空条件</a>
          </form>
        </div>
        <div class="card">
          <table>
            <thead><tr><th>标题</th><th>采购单位</th><th>公告编号</th><th>地区</th>
            <th>发布时间</th><th>版本/来源</th></tr></thead>
            <tbody>{rows_html}</tbody>
          </table>
        </div>"""
        self._send(page_shell("公告检索", body))

    @staticmethod
    def _query(db, q: dict):
        sql = (
            "SELECT DISTINCT a.*, "
            "(SELECT COUNT(*) FROM version v WHERE v.announcement_id=a.id) AS version_count, "
            "(SELECT COUNT(*) FROM source_record s WHERE s.announcement_id=a.id) AS source_count "
            "FROM announcement a LEFT JOIN version v ON v.announcement_id=a.id WHERE 1=1"
        )
        params: list = []
        if q.get("keyword"):
            for word in q["keyword"].split():
                sql += (" AND (COALESCE(a.title,'') || COALESCE(a.purchaser,'') || "
                        "COALESCE(a.notice_no,'') || COALESCE(a.region,'') || "
                        "COALESCE(v.body,'')) LIKE ?")
                params.append(f"%{word}%")
        if q.get("region"):
            sql += " AND a.region LIKE ?"
            params.append(f"%{q['region']}%")
        if q.get("date_from"):
            sql += " AND COALESCE(a.published_at,'') >= ?"
            params.append(q["date_from"])
        if q.get("date_to"):
            sql += " AND COALESCE(a.published_at,'') <= ?"
            params.append(q["date_to"])
        sql += " ORDER BY a.published_at DESC, a.id DESC LIMIT 200"
        return [dict(r) for r in db.execute(sql, params).fetchall()]

    def _api_search(self, q: dict) -> None:
        db = open_db(self.db_path)
        try:
            rows = self._query(db, q)
        finally:
            db.close()
        payload = json.dumps({"results": rows}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    # ---------------- 公告详情 ----------------
    def _page_notice(self, q: dict) -> None:
        ann_id = int(q["id"])
        db = open_db(self.db_path)
        try:
            ann = db.execute("SELECT * FROM announcement WHERE id=?", (ann_id,)).fetchone()
            if not ann:
                self._send(page_shell("未找到", "<p>公告不存在</p>"), 404)
                return
            records = db.execute(
                "SELECT * FROM source_record WHERE announcement_id=? ORDER BY id",
                (ann_id,)).fetchall()
            versions = db.execute(
                "SELECT * FROM version WHERE announcement_id=? ORDER BY version_no",
                (ann_id,)).fetchall()
        finally:
            db.close()

        record_rows = "".join(
            "<tr>"
            f'<td>{esc(r["source_code"])}</td>'
            f'<td>{esc(r["remote_id"])}</td>'
            f'<td>{("<a href=" + esc(r["url"]) + " target=_blank>" + esc(r["url"]) + "</a>") if r["url"] else "-"}</td>'
            f'<td>{esc(r["first_seen_at"])}</td><td>{esc(r["last_seen_at"])}</td>'
            f'<td><details><summary>查看原始内容</summary>{render_json_pretty(r["raw_content"])}</details></td>'
            "</tr>" for r in records
        )

        ver_rows = "".join(
            "<tr>"
            f'<td>v{v["version_no"]}</td><td>{esc(v["source_code"])}</td>'
            f'<td>{esc(v["deadline"] or "-")}</td>'
            f'<td>{esc(json.loads(v["attachments_json"] or "[]")) or "-"}</td>'
            f'<td>{esc(v["created_at"])}</td>'
            f'<td><a href="/diff?a={v["id"]}&b={versions[max(0, i - 1)]["id"] if i else v["id"]}">对比</a></td>'
            "</tr>" for i, v in enumerate(versions)
        )
        latest = versions[-1] if versions else None
        body = f"""
        <div class="card">
          <h2 style="margin-top:0">{esc(ann["title"])}</h2>
          <p class="mut">采购单位：{esc(ann["purchaser"] or "-")} ｜ 公告编号：
          {esc(ann["notice_no"] or "无（未与任何公告自动合并）")} ｜ 地区：{esc(ann["region"] or "-")}
          ｜ 发布时间：{esc(ann["published_at"] or "-")}</p>
          {'<h3>最新正文</h3><pre class="body">' + esc(latest["body"]) + "</pre>" if latest else "<p class=mut>暂无版本</p>"}
        </div>
        <div class="card">
          <h3>各来源记录（保留各自链接与原始内容）</h3>
          <table><thead><tr><th>来源</th><th>来源内ID</th><th>链接</th><th>首次发现</th>
          <th>最近抓到</th><th>原始内容</th></tr></thead><tbody>{record_rows}</tbody></table>
        </div>
        <div class="card">
          <h3>历次变化（{len(versions)} 个版本；内容完全相同的重复抓取不会产生新版本）</h3>
          <table><thead><tr><th>版本</th><th>来源</th><th>截止时间</th><th>附件</th>
          <th>采集时间</th><th>对比</th></tr></thead><tbody>{ver_rows}</tbody></table>
        </div>
        <p><a href="/">← 返回检索</a></p>"""
        self._send(page_shell(ann["title"], body))

    # ---------------- 版本对比 ----------------
    def _page_diff(self, q: dict) -> None:
        a_id, b_id = int(q["a"]), int(q["b"])
        db = open_db(self.db_path)
        try:
            va = db.execute("SELECT * FROM version WHERE id=?", (a_id,)).fetchone()
            vb = db.execute("SELECT * FROM version WHERE id=?", (b_id,)).fetchone()
            if not va or not vb:
                self._send(page_shell("未找到", "<p>版本不存在</p>"), 404)
                return
            ann = db.execute("SELECT * FROM announcement WHERE id=?",
                             (va["announcement_id"],)).fetchone()
        finally:
            db.close()

        att_a = json.loads(va["attachments_json"] or "[]")
        att_b = json.loads(vb["attachments_json"] or "[]")
        att_changed = json.dumps(att_a, ensure_ascii=False) != json.dumps(att_b, ensure_ascii=False)
        dl_changed = (va["deadline"] or "") != (vb["deadline"] or "")

        def att_list(rows):
            return "".join(
                f'<li>{esc(a.get("name"))}（{esc(a.get("url"))}）</li>' for a in rows
            ) or '<li class="mut">无附件</li>'

        body = f"""
        <div class="card">
          <h2 style="margin-top:0">版本对比：{esc(ann["title"])}</h2>
          <p class="mut">v{vb["version_no"]}（{esc(vb["source_code"])}） →
          v{va["version_no"]}（{esc(va["source_code"])}）</p>
          <h3>截止时间 {"<span class='tag red'>已变化</span>" if dl_changed else '<span class="tag green">未变化</span>'}</h3>
          <table><tr><th>旧</th><td>{esc(vb["deadline"] or "-")}</td></tr>
          <tr><th>新</th><td>{esc(va["deadline"] or "-")}</td></tr></table>
          <h3>附件 {"<span class='tag red'>已变化</span>" if att_changed else '<span class="tag green">未变化</span>'}</h3>
          <table><tr><th>旧</th><td><ul>{att_list(att_b)}</ul></td></tr>
          <tr><th>新</th><td><ul>{att_list(att_a)}</ul></td></tr></table>
        </div>
        <div class="card">
          <h3>正文变化</h3>
          {render_text_diff(vb["body"], va["body"])}
          <p class="diff-hunk">红色删除行为旧版内容，绿色新增行为新版内容。</p>
        </div>
        <p><a href="/notice?id={ann["id"]}">← 返回公告详情</a></p>"""
        self._send(page_shell("版本对比", body))

    # ---------------- 采集记录 ----------------
    def _page_runs(self) -> None:
        db = open_db(self.db_path)
        try:
            sources = db.execute("SELECT * FROM source ORDER BY code").fetchall()
            progress = {
                r["source_code"]: dict(r)
                for r in db.execute("SELECT * FROM source_progress").fetchall()
            }
            runs = db.execute("SELECT * FROM run ORDER BY id DESC LIMIT 30").fetchall()
        finally:
            db.close()

        src_rows = "".join(
            "<tr>"
            f'<td>{esc(s["code"])}</td><td>{esc(s["name"])}</td><td>{esc(s["kind"])}</td>'
            f'<td>{esc((progress.get(s["code"]) or {}).get("last_cursor") or "已完成/无断点")}</td>'
            f'<td>{esc((progress.get(s["code"]) or {}).get("last_success_at") or "-")}</td>'
            "</tr>" for s in sources
        ) or '<tr><td colspan="5" class="mut">还没有采集过</td></tr>'

        run_rows = "".join(
            "<tr>"
            f'<td>{r["id"]}</td><td>{esc(r["source_code"])}</td>{status_tag(r["status"])}'
            f'<td>{r["list_checked"]}</td><td>{r["detail_ok"]}</td>'
            f'<td>{r["detail_failed"]}</td><td>{r["soft_blocked"]}</td>'
            f'<td>{esc(r["resume_cursor"] or "-")}</td>'
            f'<td>{esc(r["started_at"])}</td><td>{esc(r["finished_at"] or "-")}</td>'
            f'<td>{esc(r["message"] or "")}</td>'
            "</tr>" for r in runs
        ) or '<tr><td colspan="11" class="mut">暂无执行记录</td></tr>'

        body = f"""
        <div class="card">
          <h3 style="margin-top:0">各来源独立采集进度</h3>
          <table><thead><tr><th>来源</th><th>名称</th><th>类型</th><th>当前断点（游标）</th>
          <th>上次成功时间</th></tr></thead><tbody>{src_rows}</tbody></table>
        </div>
        <div class="card">
          <h3>最近采集执行记录</h3>
          <table><thead><tr><th>#</th><th>来源</th><th>状态</th><th>列表条数</th>
          <th>详情成功</th><th>详情失败</th><th>软拦截</th><th>恢复点</th>
          <th>开始</th><th>结束</th><th>消息</th></tr></thead>
          <tbody>{run_rows}</tbody></table>
        </div>"""
        self._send(page_shell("采集记录", body))

    def log_message(self, fmt: str, *args) -> None:
        pass


def serve(db_path: str, host: str = "127.0.0.1", port: int = 8088) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), WebHandler)
    server.db_path = db_path  # type: ignore[attr-defined]
    return server
