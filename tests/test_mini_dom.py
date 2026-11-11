"""mini_dom 选择器与取值的单元测试。"""
from __future__ import annotations

import unittest

from procurement_aggregator.mini_dom import extract_field, parse_html


class MiniDomTests(unittest.TestCase):

    def test_selectors_and_extraction(self):
        html_doc = """
        <html><body>
          <ul class="notice-list">
            <li><span class="id">H001</span>
                <a href="/html/detail/H001">医疗设备采购公告</a>
                <span class="unit">市第一医院</span>
                <span class="no">YYCG-2026-001</span>
                <span class="region">华东</span>
                <span class="date">2026-11-01</span></li>
            <li><span class="id">H002</span>
                <a href="/html/detail/H002">物业采购公告</a>
                <span class="unit">城市图书馆</span></li>
          </ul>
          <div id="content">  正文  内容\n换行 </div>
          <ul class="attachments">
            <li><a href="/files/a.pdf">附件A</a></li>
            <li><a href="/files/b.docx">附件B</a></li>
          </ul>
        </body></html>"""
        root = parse_html(html_doc)

        items = root.select("ul.notice-list li")
        self.assertEqual(len(items), 2)
        self.assertEqual(extract_field(items[0], "a"), "医疗设备采购公告")
        self.assertEqual(extract_field(items[0], "a@href"), "/html/detail/H001")
        self.assertEqual(extract_field(items[0], "span.no"), "YYCG-2026-001")
        # 缺失字段返回 None，不抛异常
        self.assertIsNone(extract_field(items[1], "span.no"))
        # id 选择器 + 空白归一化
        self.assertEqual(root.select_one("div#content").text(), "正文 内容 换行")
        # 多附件
        atts = root.select("ul.attachments li a")
        self.assertEqual([a.text() for a in atts], ["附件A", "附件B"])
        self.assertEqual([a.attr("href") for a in atts],
                         ["/files/a.pdf", "/files/b.docx"])

    def test_tolerates_unclosed_tags(self):
        root = parse_html("<div><p>第一段<p>第二段</div>")
        self.assertEqual(len(root.select("div p")), 2)
        self.assertEqual(root.select("div p")[1].text(), "第二段")


if __name__ == "__main__":
    unittest.main()
