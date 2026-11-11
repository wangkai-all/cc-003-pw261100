"""端到端测试的公共夹具：在随机端口启动模拟站点，提供独立临时数据库。"""
from __future__ import annotations

import tempfile
import unittest
import urllib.request
from pathlib import Path

from mock_site import data
from mock_site.server import MockHandler, ThreadingHTTPServer

from procurement_aggregator.config import default_sources
from procurement_aggregator.db import Database, open_db
from procurement_aggregator.http_client import HttpClient
from procurement_aggregator.pipeline import Pipeline


class MockSiteTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
        cls.port = cls.httpd.server_address[1]
        import threading
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        # 等待端口真正可用
        for _ in range(50):
            try:
                urllib.request.urlopen(f"{cls.base_url}/health", timeout=2).read()
                break
            except OSError:
                pass
        else:
            raise RuntimeError("模拟站点启动失败")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self) -> None:
        data.STATE.reset()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = str(Path(self.tmp.name) / "test.db")
        conn = open_db(self.db_path)
        self.db = Database(conn)
        self.addCleanup(conn.close)
        # 测试中重试不睡眠，加快速度
        self.client = HttpClient(timeout=5, retries=3, backoff=0.01,
                                 sleep=lambda _s: None)
        self.pipeline = Pipeline(self.db, self.client)
        self.sources = default_sources(self.base_url)

    def control(self, query: str) -> dict:
        import json
        with urllib.request.urlopen(
                f"{self.base_url}/control?{query}", timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def run_collect(self, code: str | None = None):
        sources = self.sources
        if code:
            sources = [s for s in sources if s.code == code]
        return self.pipeline.collect_all(sources)

    def notices(self):
        return self.db.search_announcements()
