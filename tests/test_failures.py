"""故障场景：临时限流重试、翻页中断断点续采、维护页/登录页软拦截、来源失败隔离、超时。"""
from __future__ import annotations

from procurement_aggregator.config import default_sources
from procurement_aggregator.db import Database, open_db
from procurement_aggregator.http_client import HttpClient
from procurement_aggregator.pipeline import Pipeline

from .base import MockSiteTestCase


class FailureHandlingTests(MockSiteTestCase):

    def test_01_transient_rate_limit_is_retried(self):
        # 前两次列表请求返回 503，之后自动恢复；重试后应整体成功
        self.control("flaky=2")
        results = self.run_collect("html_demo")
        self.assertEqual(results[0].status, "success")
        self.assertGreaterEqual(results[0].saved, 5)

    def test_02_paging_interruption_resumes_from_cursor(self):
        # 第 2 次 HTML 列表请求起持续 503：第一页（2 条）之后中断
        self.control("fail_after_html=2")
        results = self.run_collect("html_demo")
        self.assertEqual(results[0].status, "failed")
        progress = self.db.get_progress("html_demo")
        self.assertIsNotNone(progress["last_cursor"])

        # 已抓到的第一页数据仍然入库
        titles = {n["title"] for n in self.notices()}
        self.assertTrue(any("医疗设备" in t for t in titles))
        self.assertEqual(len(self.notices()), 2)  # 只有第一页 2 条

        # 恢复站点后重新采集，从断点继续，最终抓全 5 条
        self.control("reset")
        results = self.run_collect("html_demo")
        self.assertEqual(results[0].status, "success")
        self.assertEqual(len(self.notices()), 5)
        # 成功完成后断点清空
        self.assertIsNone(self.db.get_progress("html_demo")["last_cursor"])

    def test_03_json_cursor_interruption_and_resume(self):
        # JSON 两页数据：第 2 次逻辑列表请求（第二页）持续失败，第一页 J101/J102 已入库
        self.control("fail_after_json=2")
        results = self.run_collect("json_demo")
        self.assertEqual(results[0].status, "failed")
        self.assertEqual(results[0].saved, 2)
        cursor = self.db.get_progress("json_demo")["last_cursor"]
        self.assertTrue(cursor and cursor.startswith("cursor:cur-"))

        self.control("reset")
        results = self.run_collect("json_demo")
        self.assertEqual(results[0].status, "success")
        ids = {r["remote_id"] for r in self.db.conn.execute(
            "SELECT remote_id FROM source_record WHERE source_code='json_demo'")}
        self.assertEqual(ids, {"J101", "J102", "J103", "J104"})
        # 已抓过的 J101/J102 重抓内容相同，不产生新版本
        self.assertEqual(results[0].new_versions, 2)
    def test_04_maintenance_page_is_soft_blocked_not_saved(self):
        self.control("mode=maintenance")
        results = self.run_collect("html_demo")
        self.assertEqual(results[0].status, "soft_blocked")
        self.assertEqual(len(self.notices()), 0)  # 维护页不能写成公告
        run = self.db.list_runs("html_demo")[0]
        self.assertEqual(run["status"], "soft_blocked")
        self.assertEqual(run["soft_blocked"], 1)

    def test_05_login_page_json_is_soft_blocked(self):
        self.control("mode=login")
        results = self.run_collect("json_demo")
        self.assertEqual(results[0].status, "soft_blocked")
        self.assertEqual(len(self.notices()), 0)

    def test_06_one_source_failure_does_not_block_others(self):
        # 只让 HTML 来源从第 1 次列表请求起持续 503，JSON 来源完全正常，
        # 验证采集流程的来源级故障隔离：一个来源失败，其他来源照常完成。
        self.control("fail_after_html=1")
        results = self.run_collect()
        by_code = {r.source_code: r for r in results}
        self.assertEqual(by_code["html_demo"].status, "failed")
        self.assertEqual(by_code["json_demo"].status, "success")
        # JSON 的四条公告完整入库
        json_records = self.db.conn.execute(
            "SELECT COUNT(*) c FROM source_record WHERE source_code='json_demo'"
        ).fetchone()["c"]
        self.assertEqual(json_records, 4)

        # 两个来源在同一次采集中各自独立，HTML 失败不会中断整个流程
        self.assertEqual(len(results), 2)

    def test_07_detail_failure_marks_run_incomplete(self):
        """详情页取不到不能当作采集完成（通过软拦截详情页模拟）。"""
        # 先正常采集，再让详情接口进入维护；清掉 HTML 进度强制全量重采
        self.run_collect("html_demo")
        self.control("mode=maintenance")
        # 直接重置来源进度，模拟新一轮采集撞上维护
        self.db.conn.execute(
            "UPDATE source_progress SET last_cursor=NULL WHERE source_code='html_demo'")
        self.db.conn.commit()
        results = self.run_collect("html_demo")
        self.assertIn(results[0].status, ("soft_blocked", "partial", "failed"))
        self.assertNotEqual(results[0].status, "success")

    def test_08_timeout_is_retried_then_marked_failed(self):
        """读超时属于临时故障可重试；持续超时后标记 failed 且保留断点，不算成功。"""
        import tempfile
        from pathlib import Path
        # 每个请求睡 2 秒，客户端超时 0.3 秒、重试 1 次、不睡眠
        self.control("slow=2")
        slow_db_path = str(Path(self.tmp.name) / "slow.db")
        slow_conn = open_db(slow_db_path)
        self.addCleanup(slow_conn.close)
        slow_pipeline = Pipeline(
            Database(slow_conn),
            HttpClient(timeout=0.3, retries=1, backoff=0.01, sleep=lambda _s: None),
        )
        sources = [s for s in default_sources(self.base_url) if s.code == "html_demo"]
        results = slow_pipeline.collect_all(sources)
        self.assertEqual(results[0].status, "failed")
        self.assertIn("超时", results[0].message)
        # 超时不允许写入任何公告
        self.assertEqual(Database(slow_conn).counts()["announcements"], 0)
        run = Database(slow_conn).list_runs("html_demo")[0]
        self.assertEqual(run["status"], "failed")
