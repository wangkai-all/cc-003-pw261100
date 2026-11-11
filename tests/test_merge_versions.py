"""核心场景：跨来源合并、无编号不合并、重复抓取不新增版本、内容变化新增版本。"""
from __future__ import annotations

from procurement_aggregator.normalize import merge_key

from .base import MockSiteTestCase


class MergeAndVersionTests(MockSiteTestCase):

    def test_01_cross_source_merge_and_no_number_not_merged(self):
        results = self.run_collect()
        statuses = {r.source_code: r.status for r in results}
        self.assertEqual(statuses, {"html_demo": "success", "json_demo": "success"})

        notices = self.notices()
        # 5 个 HTML 公告 + 4 个 JSON 公告；J101 与 H001 同单位同编号被合并 => 8 条
        self.assertEqual(len(notices), 8)

        # 合并键规则本身
        self.assertIsNone(merge_key("某单位", ""))
        self.assertEqual(merge_key("市第一医院", "YYCG-2026-001"),
                         "市第一医院||YYCG-2026-001")

        # 找到 H001/J101 合并后的公告
        merged = next(n for n in notices if n["notice_no"] == "YYCG-2026-001")
        records = self.db.list_records(merged["id"])
        record_sources = {r["source_code"] for r in records}
        self.assertEqual(record_sources, {"html_demo", "json_demo"})
        # 两个来源各自的链接都保留
        urls = {r["url"] for r in records}
        self.assertTrue(any("/html/detail/H001" in u for u in urls))
        self.assertTrue(any("/json/detail/J101" in u for u in urls))
        # 两个来源各自的原始内容都保留
        self.assertTrue(all(r["raw_content"] for r in records))

        # 两个来源正文不同（J101 多一句报名要求）=> 应产生 2 个版本
        versions = self.db.list_versions(merged["id"])
        self.assertEqual(len(versions), 2)
        self.assertEqual([v["version_no"] for v in versions], [1, 2])

        # 标题相同但都没有编号的两条公告不能合并
        no_number = [n for n in notices if not n["notice_no"]]
        self.assertEqual(len(no_number), 2)
        self.assertEqual({n["purchaser"] for n in no_number},
                         {"市政服务中心", "市档案馆"})
        for n in no_number:
            self.assertIsNone(n["merge_key"])

    def test_02_repeated_crawl_creates_no_new_version(self):
        self.run_collect()
        before = self.db.counts()
        results = self.run_collect()  # 立刻再抓一遍，内容完全相同
        for r in results:
            self.assertEqual(r.status, "success")
            self.assertEqual(r.new_versions, 0, f"{r.source_code} 不应产生新版本")
        after = self.db.counts()
        # 公告、来源记录、版本数量都不变；来源记录的 last_seen 会刷新
        self.assertEqual(before, after)

    def test_03_body_change_creates_new_version(self):
        self.run_collect("html_demo")
        ann = next(n for n in self.notices() if n["notice_no"] == "TSCG-2026-002")
        v1 = self.db.list_versions(ann["id"])
        self.assertEqual(len(v1), 1)
        self.assertIn("保洁、安保与绿化养护", v1[0]["body"])
        self.assertEqual(v1[0]["deadline"], "2026-11-25 18:00")

        # 站点更新公告：正文与截止时间都变了
        self.control("scenario=update_v2")
        self.run_collect("html_demo")
        versions = self.db.list_versions(ann["id"])
        self.assertEqual(len(versions), 2)
        self.assertIn("前台接待", versions[1]["body"])
        self.assertEqual(versions[1]["deadline"], "2026-12-05 18:00")

        # 再抓相同内容：仍只有 2 个版本
        self.run_collect("html_demo")
        self.assertEqual(len(self.db.list_versions(ann["id"])), 2)

        # 第三次更新：正文再变、附件增加（截止时间不变）
        self.control("scenario=update_v3")
        self.run_collect("html_demo")
        versions = self.db.list_versions(ann["id"])
        self.assertEqual(len(versions), 3)
        self.assertEqual(versions[2]["deadline"], "2026-12-05 18:00")
        self.assertEqual(len(versions[2]["attachments"]), 2)
        # 历史版本的附件仍保留旧状态
        self.assertEqual(len(versions[1]["attachments"]), 1)

    def test_04_search_filters(self):
        self.run_collect()
        # 关键词
        hits = self.db.search_announcements(keyword="物业")
        self.assertEqual(len(hits), 1)
        # 多关键词 AND
        self.assertEqual(len(self.db.search_announcements(keyword="医院 招标")), 1)
        self.assertEqual(len(self.db.search_announcements(keyword="医院 不存在的词")), 0)
        # 地区
        east = self.db.search_announcements(region="华东")
        self.assertEqual(
            {n["purchaser"] for n in east},
            {"市第一医院", "市大数据局"},  # H001 与 J104 同属华东
        )
        # 发布时间区间（含边界）：H004(11-06)、J102(11-07)、H005(11-08)
        self.assertEqual(
            len(self.db.search_announcements(date_from="2026-11-06",
                                             date_to="2026-11-08")),
            3,
        )
        # 更窄的区间
        self.assertEqual(
            len(self.db.search_announcements(date_from="2026-11-07",
                                             date_to="2026-11-07")),
            1,
        )
        # 组合条件
        hits = self.db.search_announcements(keyword="信号灯", region="华中")
        self.assertEqual(len(hits), 1)
