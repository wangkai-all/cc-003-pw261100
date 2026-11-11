"""带游标的 JSON 接口来源。

列表接口返回 {"items": [...], "next_cursor": "..."}；
next_cursor 为空/缺失表示没有下一页。游标即采集进度，中断后从上次游标继续。
JSON 详情接口同样可能在 200 响应里返回维护/登录标记，需要识别。
"""
from __future__ import annotations

from urllib.parse import urljoin

from . import BaseSource, FetchStats
from ..db import NoticeInput
from ..http_client import FetchError, SoftBlockedError


class JsonSource(BaseSource):
    kind = "json"

    def fetch(self, resume_cursor: str | None, progress_cb, sink) -> FetchStats:
        cursor = resume_cursor
        # 进度表里存的是 "cursor:<token>" 形式，去掉前缀还原接口游标
        if cursor and cursor.startswith("cursor:"):
            cursor = cursor.split(":", 1)[1]
        stats = FetchStats()
        max_pages = int(self.s.get("max_pages", 100))

        for _ in range(max_pages):
            list_url = urljoin(
                self.config.base_url + "/",
                self.s["list_path"].format(cursor=cursor or ""),
            )
            payload = self.client.get_json(list_url, self.markers)
            self._check_json_soft_blocked(payload, list_url)

            items = payload.get(self.s["items_field"])
            if not isinstance(items, list) or not items:
                break
            stats.list_checked += len(items)

            for item in items:
                notice = self._parse_item(item)
                # 记录当前页起点游标，再抓详情；失败时从该游标恢复
                progress_cb(f"cursor:{cursor or ''}")
                self._fill_detail(notice, stats)
                sink(notice)  # 逐条落库，后续页失败也不丢已抓数据
                stats.saved += 1

            next_cursor = payload.get(self.s["cursor_field"])
            if not next_cursor:
                cursor = None
                break
            cursor = next_cursor
            progress_cb(f"cursor:{cursor}")

        return stats

    def _parse_item(self, item: dict) -> NoticeInput:
        f = self.s["fields"]

        def field(name: str, default=""):
            # fields 映射的是 JSON 字段名；未配置映射时回退为同名键
            return item.get(f.get(name, name), default)

        href = field("url", "")
        url = urljoin(self.config.base_url + "/", href) if href else None
        return NoticeInput(
            source_code=self.code,
            remote_id=str(field("remote_id")),
            url=url,
            title=field("title", None),
            purchaser=field("purchaser", None),
            notice_no=field("notice_no", ""),
            region=field("region", ""),
            published_at=field("published_at", None),
            body=None,
            deadline=None,
            attachments=None,
            raw=None,
        )

    def _fill_detail(self, notice: NoticeInput, stats: FetchStats) -> None:
        detail_url = urljoin(
            self.config.base_url + "/",
            self.s["detail_path"].format(remote_id=notice.remote_id),
        )
        try:
            payload = self.client.get_json(detail_url, self.markers)
        except SoftBlockedError:
            stats.soft_blocked += 1
            raise
        except FetchError:
            stats.detail_failed += 1
            raise

        self._check_json_soft_blocked(payload, detail_url)
        data = payload
        field_name = self.s.get("detail_data_field")
        if field_name:
            data = payload.get(field_name)
        if not isinstance(data, dict):
            stats.detail_failed += 1
            raise FetchError(f"详情接口缺少数据字段 {field_name}: {detail_url}")

        d = self.s["detail"]
        notice.body = data.get(d["body_field"])
        if not notice.body:
            stats.detail_failed += 1
            raise FetchError(f"详情数据缺少正文字段 {d['body_field']}: {detail_url}")
        notice.deadline = data.get(d["deadline_field"])
        attachments = data.get(d["attachments_field"]) or []
        if not isinstance(attachments, list):
            attachments = []
        notice.attachments = [
            {"name": str(a.get("name", "")), "url": a.get("url")} for a in attachments
        ]
        # JSON 来源的「原文」即详情数据序列化
        import json as _json
        notice.raw = _json.dumps(data, ensure_ascii=False, sort_keys=True)
        stats.detail_ok += 1

    def _check_json_soft_blocked(self, payload: object, url: str) -> None:
        """识别 200 响应里的维护/登录 JSON。"""
        if not isinstance(payload, dict):
            return
        marker_set = set(self.markers)
        # 约定结构：{"status": "maintenance"/"login", "message": "..."}
        status_value = str(payload.get("status", "")).upper()
        if status_value in {"MAINTENANCE", "LOGIN", "UNAUTHORIZED"}:
            raise SoftBlockedError(f"接口返回 {status_value} 状态，不是正常公告: {url}")
        for key in ("message", "error", "hint"):
            value = str(payload.get(key, ""))
            for marker in marker_set:
                if marker and marker in value:
                    raise SoftBlockedError(
                        f"接口返回内容命中维护/登录标记「{marker}」: {url}"
                    )
