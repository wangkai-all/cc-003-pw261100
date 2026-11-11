"""本地模拟站点 HTTP 服务（纯标准库实现）。

故障注入全部通过 GET /control 控制：
  /control?mode=maintenance        所有页面 200 返回维护页（验证软拦截识别）
  /control?mode=login              所有页面 200 返回登录页
  /control?mode=normal             恢复正常
  /control?scenario=update_v2      把 H002 切换到第 2 版内容（验证版本化）
  /control?fail_after=2            第 2 次列表请求起返回 503（验证翻页中断+续采）
  /control?flaky=2                 接下来 2 次请求临时 503 后自动恢复（验证重试）
  /control?slow=1.5                每次请求前 sleep（验证超时，配合短超时）
  /control?reset                   全部复位
  /control?state                  查看当前状态
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import data


class MockHandler(BaseHTTPRequestHandler):
    server_version = "MockProcurement/1.0"

    # ---- 通用响应 ----
    def _send(self, body: str, status: int = 200,
              content_type: str = "text/html; charset=utf-8") -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, obj: dict, status: int = 200) -> None:
        self._send(json.dumps(obj, ensure_ascii=False), status,
                   "application/json; charset=utf-8")

    def _maybe_slow(self) -> None:
        if data.STATE.slow_delay:
            time.sleep(data.STATE.slow_delay)

    def _check_list_failure(self, kind: str) -> bool:
        """列表请求的故障注入，返回 True 表示已响应故障。

        kind 区分 html/json，阈值与计数都互相独立：可以只让一个来源持续故障，
        另一个来源正常，用来验证采集流程的来源级故障隔离。
        - flaky：只按「逻辑列表请求」计数，HTTP 自动重试不消耗 flaky 次数；
        - fail_after_<kind>：该类型第 N 次列表请求起持续 503，模拟翻页中断。
        """
        if kind == "html":
            data.STATE.html_list_request_count += 1
            nth = data.STATE.html_list_request_count
            threshold = data.STATE.fail_after_html
        else:
            data.STATE.json_list_request_count += 1
            nth = data.STATE.json_list_request_count
            threshold = data.STATE.fail_after_json
        if data.STATE.flaky_fail_remaining > 0:
            data.STATE.flaky_fail_remaining -= 1
            self._send("temporary overload, retry later", 503, "text/plain")
            return True
        if threshold is not None and nth >= threshold:
            self._send("service unavailable (paging interrupted)", 503, "text/plain")
            return True
        return False

    def _check_soft_block(self) -> bool:
        """200 维护页/登录页，返回 True 表示已拦截。"""
        if data.STATE.mode == "maintenance":
            self._send(
                "<html><body><h1>网站维护公告</h1><p>系统维护中，请稍后再试。</p></body></html>"
            )
            return True
        if data.STATE.mode == "login":
            self._send(
                "<html><body><h1>用户登录</h1><form>LOGIN_REQUIRED</form></body></html>"
            )
            return True
        return False

    # ---- 路由 ----
    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler 约定)
        self._maybe_slow()
        data.STATE.request_count += 1
        parsed = urlparse(self.path)
        path = parsed.path
        # keep_blank_values：/control?reset 这类无值开关也需要保留
        query = parse_qs(parsed.query, keep_blank_values=True)

        if path == "/control":
            self._handle_control(query)
            return
        if path == "/health":
            self._send_json({"status": "ok", "mode": data.STATE.mode})
            return

        if self._check_soft_block():
            return

        if path == "/html/list" or path == "/html/list/":
            if self._check_list_failure("html"):
                return
            page = int(query.get("page", ["1"])[0])
            self._send_html_list(page)
            return
        if path.startswith("/html/detail/"):
            self._send_html_detail(path.rsplit("/", 1)[-1])
            return
        if path == "/json/notices":
            if self._check_list_failure("json"):
                return
            cursor = query.get("cursor", [""])[0] or None
            self._send_json_list(cursor)
            return
        if path.startswith("/json/detail/"):
            self._send_json_detail(path.rsplit("/", 1)[-1])
            return

        self._send("not found", 404, "text/plain; charset=utf-8")

    # ---- 控制接口 ----
    def _handle_control(self, query: dict) -> None:
        if "reset" in query:
            # reset 只复位故障注入；公告内容场景（scenario）单独由 scenario 控制，
            # 否则「先更新公告、再复位故障」会把更新一起冲掉
            data.STATE.mode = "normal"
            data.STATE.fail_after_html = None
            data.STATE.fail_after_json = None
            data.STATE.flaky_fail_remaining = 0
            data.STATE.html_list_request_count = 0
            data.STATE.json_list_request_count = 0
            data.STATE.request_count = 0
            data.STATE.slow_delay = 0.0
            data.STATE.scenario = ""
        if "mode" in query:
            mode = query["mode"][0]
            if mode in ("normal", "maintenance", "login"):
                data.STATE.mode = mode
        if "scenario" in query:
            data.STATE.scenario = query["scenario"][0]
        if "fail_after" in query:
            # 全局开关：两种来源都从第 N 次列表请求起失败
            try:
                nth = int(query["fail_after"][0])
            except ValueError:
                nth = None
            data.STATE.fail_after_html = nth
            data.STATE.fail_after_json = nth
        if "fail_after_html" in query:
            data.STATE.fail_after_html = int(query["fail_after_html"][0])
        if "fail_after_json" in query:
            data.STATE.fail_after_json = int(query["fail_after_json"][0])
        if "flaky" in query:
            data.STATE.flaky_fail_remaining = int(query["flaky"][0])
        if "slow" in query:
            data.STATE.slow_delay = float(query["slow"][0])
        if "state" in query:
            self._send_json({
                "mode": data.STATE.mode,
                "scenario": data.STATE.scenario,
                "fail_after_html": data.STATE.fail_after_html,
                "fail_after_json": data.STATE.fail_after_json,
                "flaky_fail_remaining": data.STATE.flaky_fail_remaining,
                "request_count": data.STATE.request_count,
                "html_list_request_count": data.STATE.html_list_request_count,
                "json_list_request_count": data.STATE.json_list_request_count,
                "slow_delay": data.STATE.slow_delay,
            })
            return
        self._send_json({"ok": True, "state": _state_dict()})

    # ---- HTML 页面 ----
    def _send_html_list(self, page: int) -> None:
        items = data.html_page(page)
        rows = []
        for n in items:
            rows.append(
                '<li>'
                f'<span class="id">{n["remote_id"]}</span>'
                f'<a href="/html/detail/{n["remote_id"]}">{n["title"]}</a>'
                f'<span class="unit">{n["purchaser"]}</span>'
                f'<span class="no">{n["notice_no"]}</span>'
                f'<span class="region">{n["region"]}</span>'
                f'<span class="date">{n["published_at"]}</span>'
                '</li>'
            )
        body = (
            "<html><head><meta charset='utf-8'><title>采购公告列表</title></head><body>"
            f"<h2>第 {page} 页</h2><ul class='notice-list'>"
            + "".join(rows)
            + "</ul></body></html>"
        )
        self._send(body)

    def _send_html_detail(self, notice_id: str) -> None:
        if notice_id not in data.NOTICES:
            self._send("not found", 404, "text/plain; charset=utf-8")
            return
        n = data.effective_notice(notice_id)
        atts = "".join(
            f'<li><a href="{a["url"]}">{a["name"]}</a></li>'
            for a in n["attachments"]
        )
        body = (
            "<html><head><meta charset='utf-8'></head><body>"
            f"<h1>{n['title']}</h1>"
            f"<p>采购单位：<b>{n['purchaser']}</b>　公告编号：{n['notice_no']}　"
            f"地区：{n['region']}　发布时间：{n['published_at']}</p>"
            f"<p>投标截止时间：<span class='deadline'>{n['deadline']}</span></p>"
            f"<div id='content'>{n['body']}</div>"
            f"<ul class='attachments'>{atts}</ul>"
            "</body></html>"
        )
        self._send(body)

    # ---- JSON 接口 ----
    def _send_json_list(self, cursor: str | None) -> None:
        items, next_cursor = data.json_page(cursor)
        # 列表项以 id 作为稳定标识（适配器按 fields.remote_id="id" 取值）
        for item in items:
            item.setdefault("id", item["remote_id"])
        self._send_json({"items": items, "next_cursor": next_cursor})

    def _send_json_detail(self, notice_id: str) -> None:
        if notice_id not in data.JSON_NOTICES:
            self._send_json({"status": "error", "message": "notice not found"}, 404)
            return
        n = data.JSON_NOTICES[notice_id]
        self._send_json({"status": "ok", "data": n})

    def log_message(self, fmt: str, *args) -> None:  # 静音默认访问日志
        pass


def _state_dict() -> dict:
    return {
        "mode": data.STATE.mode,
        "scenario": data.STATE.scenario,
        "fail_after_html": data.STATE.fail_after_html,
        "fail_after_json": data.STATE.fail_after_json,
        "flaky_fail_remaining": data.STATE.flaky_fail_remaining,
        "html_list_request_count": data.STATE.html_list_request_count,
        "json_list_request_count": data.STATE.json_list_request_count,
    }


def serve(host: str = "127.0.0.1", port: int = 8099) -> tuple[ThreadingHTTPServer, threading.Thread]:
    """启动模拟站点（后台线程），返回 (server, thread)，便于测试与脚本控制。"""
    data.STATE.reset()
    server = ThreadingHTTPServer((host, port), MockHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="采购公告模拟站点")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8099)
    args = parser.parse_args()
    data.STATE.reset()
    server = ThreadingHTTPServer((args.host, args.port), MockHandler)
    print(f"模拟站点运行在 http://{args.host}:{args.port}")
    print("控制接口示例: /control?mode=maintenance | /control?scenario=update_v2 | "
          "/control?fail_after=2 | /control?flaky=2 | /control?reset")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
