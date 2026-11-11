"""HTML 列表页 + 详情页来源。

翻页参数是页码，进度以 "page:<N>" 形式记录（页码即游标）。
列表页只能拿到元数据，正文/截止时间/附件必须再抓详情页；
详情页抓取失败会抛出异常由采集流程标记为未完成，绝不当作采集成功。
"""
from __future__ import annotations

from urllib.parse import urljoin

from ..mini_dom import extract_field, parse_html
from . import BaseSource, FetchStats
from ..db import NoticeInput
from ..http_client import FetchError, SoftBlockedError


class HtmlSource(BaseSource):
    kind = "html"

    def fetch(self, resume_cursor: str | None, progress_cb, sink) -> FetchStats:
        start_page = self._cursor_to_page(resume_cursor)
        page = start_page
        max_pages = int(self.s.get("max_pages", 20))
        stats = FetchStats()

        while page < start_page + max_pages:
            list_url = urljoin(self.config.base_url + "/",
                               self.s["list_path"].format(page=page))
            html = self.client.get_text(list_url, self.markers)
            root = parse_html(html)
            items = root.select(self.s["item_selector"])
            if not items:
                # 空列表页视为正常结束
                break
            stats.list_checked += len(items)

            for item in items:
                notice = self._parse_item(item)
                self._fill_detail(notice, stats)
                sink(notice)  # 详情成功立即入库，后续页面失败也不丢数据
                stats.saved += 1

            # 本页全部处理完，再把游标推进到下一页；
            # 这样中断时保存的页码一定指向「尚未处理」的页
            page += 1
            progress_cb(f"page:{page}")

        return stats

    def _parse_item(self, item) -> NoticeInput:
        f = self.s["fields"]

        def field(name: str):
            spec = f.get(name)
            return extract_field(item, spec) if spec else None

        href = field("url")
        url = urljoin(self.config.base_url + "/", href) if href else None
        return NoticeInput(
            source_code=self.code,
            remote_id=field("remote_id") or "",
            url=url,
            title=field("title"),
            purchaser=field("purchaser"),
            notice_no=field("notice_no") or "",
            region=field("region") or "",
            published_at=field("published_at"),
            body=None,
            deadline=None,
            attachments=None,
            raw=None,
        )

    def _fill_detail(self, notice: NoticeInput, stats: FetchStats) -> None:
        if not notice.url:
            raise FetchError(
                f"来源 {self.code} 条目 {notice.remote_id} 缺少详情页链接，无法采集正文"
            )
        try:
            html = self.client.get_text(notice.url, self.markers)
        except SoftBlockedError:
            stats.soft_blocked += 1
            raise
        except FetchError:
            stats.detail_failed += 1
            raise

        root = parse_html(html)
        d = self.s["detail"]
        body_node = root.select_one(d["body_selector"])
        if body_node is None:
            stats.detail_failed += 1
            raise FetchError(
                f"详情页缺少正文容器 {d['body_selector']}: {notice.url}"
            )
        notice.body = body_node.text()
        notice.raw = body_node.raw_html()

        deadline_node = root.select_one(d["deadline_selector"])
        notice.deadline = deadline_node.text() if deadline_node else None

        attachments = []
        for a in root.select(d["attachment_selector"]):
            href = a.attr("href")
            attachments.append({
                "name": a.text(),
                "url": urljoin(self.config.base_url + "/", href) if href else None,
            })
        notice.attachments = attachments
        stats.detail_ok += 1

    @staticmethod
    def _cursor_to_page(cursor: str | None) -> int:
        if not cursor:
            return 1
        if cursor.startswith("page:"):
            try:
                return max(1, int(cursor.split(":", 1)[1]))
            except ValueError:
                return 1
        return 1
