"""Web 页面冒烟测试：检索、详情、版本对比、采集记录均可渲染。"""
from __future__ import annotations

import threading
import urllib.parse
import urllib.request

from procurement_aggregator.webapp.server import serve

from .base import MockSiteTestCase


class WebPagesTests(MockSiteTestCase):

    def setUp(self) -> None:
        super().setUp()
        self.web = serve(self.db_path, "127.0.0.1", 0)
        self.web_port = self.web.server_address[1]
        self.web_thread = threading.Thread(target=self.web.serve_forever, daemon=True)
        self.web_thread.start()
        self.addCleanup(self.web.shutdown)
        self.run_collect()
        # 制造一次更新，保证有两个版本可对比
        self.control("scenario=update_v2")
        self.run_collect("html_demo")

    def _get(self, path: str) -> str:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.web_port}{path}", timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            return resp.read().decode("utf-8")

    def test_search_page_and_filters(self):
        page = self._get("/")
        self.assertIn("公告检索", page)
        page = self._get("/?keyword=" + urllib.parse.quote("物业"))
        self.assertIn("城市图书馆", page)
        page = self._get("/?region=" + urllib.parse.quote("华东"))
        self.assertIn("市第一医院", page)
    def test_notice_detail_keeps_sources_and_versions(self):
        ann = next(n for n in self.notices() if n["notice_no"] == "YYCG-2026-001")
        page = self._get(f"/notice?id={ann['id']}")
        self.assertIn("html_demo", page)
        self.assertIn("json_demo", page)
        self.assertIn("/html/detail/H001", page)
        self.assertIn("/json/detail/J101", page)
        self.assertIn("历次变化", page)

        h002 = next(n for n in self.notices() if n["notice_no"] == "TSCG-2026-002")
        page = self._get(f"/notice?id={h002['id']}")
        self.assertIn("v1", page)
        self.assertIn("v2", page)

    def test_diff_page_highlights_changes(self):
        h002 = next(n for n in self.notices() if n["notice_no"] == "TSCG-2026-002")
        versions = self.db.list_versions(h002["id"])
        page = self._get(f"/diff?a={versions[1]['id']}&b={versions[0]['id']}")
        self.assertIn("版本对比", page)
        self.assertIn("diff-add", page)
        self.assertIn("diff-del", page)
        self.assertIn("已变化", page)  # 截止时间变化标记

    def test_runs_page_shows_progress(self):
        page = self._get("/runs")
        self.assertIn("html_demo", page)
        self.assertIn("json_demo", page)
        self.assertIn("success", page)

    def test_json_api(self):
        import json
        url = ("http://127.0.0.1:%d/api/search?" % self.web_port
               + urllib.parse.urlencode({"keyword": "医院"}))
        with urllib.request.urlopen(url, timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        self.assertTrue(payload["results"])
        self.assertEqual(payload["results"][0]["purchaser"], "市第一医院")
