"""来源与路径配置。

两个内置来源都指向本地模拟站点：
- html_demo：HTML 列表页 + 详情页（分页参数 page）
- json_demo：带游标的 JSON 接口（cursor 分页）
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

DEFAULT_BASE_URL = os.environ.get("PROCUREMENT_BASE_URL", "http://127.0.0.1:8099")
DEFAULT_DB_PATH = os.environ.get("PROCUREMENT_DB", "data/procurement.db")


@dataclass
class SourceConfig:
    code: str
    name: str
    kind: str  # "html" | "json"
    base_url: str
    settings: dict[str, Any] = field(default_factory=dict)


def default_sources(base_url: str = DEFAULT_BASE_URL) -> list[SourceConfig]:
    """返回两个内置来源的配置。"""
    html_settings = {
        "list_path": "/html/list?page={page}",
        "start_page": 1,
        # 列表项选择器（支持 tag / #id / .class 与后代组合）
        "item_selector": "ul.notice-list li",
        "fields": {
            "remote_id": "span.id",
            "url": "a@href",
            "title": "a",
            "purchaser": "span.unit",
            "notice_no": "span.no",
            "region": "span.region",
            "published_at": "span.date",
        },
        "detail": {
            "body_selector": "div#content",
            "deadline_selector": "span.deadline",
            "attachment_selector": "ul.attachments li a",
        },
        # 200 但其实是维护页/登录页时的识别标记
        "soft_markers": ["系统维护", "用户登录", "网站维护公告", "LOGIN_REQUIRED"],
        "max_pages": 20,
    }
    json_settings = {
        "list_path": "/json/notices?cursor={cursor}",
        "items_field": "items",
        "cursor_field": "next_cursor",        "fields": {
            "remote_id": "id",
            "url": "url",
            "title": "title",
            "purchaser": "purchaser",
            "notice_no": "notice_no",
            "region": "region",
            "published_at": "published_at",
        },
        "detail_path": "/json/detail/{remote_id}",
        "detail_data_field": "data",
        "detail": {
            "body_field": "body",
            "deadline_field": "deadline",
            "attachments_field": "attachments",
        },
        "soft_markers": ["系统维护", "用户登录", "LOGIN_REQUIRED", "UNAUTHORIZED"],
    }
    return [
        SourceConfig("html_demo", "演示来源-HTML站点", "html", base_url, html_settings),
        SourceConfig("json_demo", "演示来源-JSON接口", "json", base_url, json_settings),
    ]
