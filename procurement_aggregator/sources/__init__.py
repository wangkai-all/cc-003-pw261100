"""来源适配器公共定义。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable

from ..config import SourceConfig
from ..db import NoticeInput
from ..http_client import HttpClient

# 每抓到一条完整公告（含详情）就回调一次：流水线据此立即入库，
# 保证后续页面失败时前面的数据不丢、进度可恢复。
NoticeSink = Callable[[NoticeInput], None]
ProgressCallback = Callable[[str | None], None]


@dataclass
class FetchStats:
    """一次来源采集的统计（公告本身通过 sink 逐条交付）。"""

    list_checked: int = 0
    detail_ok: int = 0
    detail_failed: int = 0
    soft_blocked: int = 0
    saved: int = 0
    new_versions: int = 0


class BaseSource(ABC):
    kind = "base"

    def __init__(self, config: SourceConfig, client: HttpClient):
        self.config = config
        self.client = client
        self.s = config.settings

    @property
    def code(self) -> str:
        return self.config.code

    @property
    def markers(self) -> list[str]:
        return list(self.s.get("soft_markers", []))

    @abstractmethod
    def fetch(self, resume_cursor: str | None,
              progress_cb: ProgressCallback,
              sink: NoticeSink) -> "FetchStats":
        """执行采集。

        progress_cb(cursor) 在每翻一页/推进游标时调用；
        sink(notice) 在每条公告详情抓取成功后立即调用（逐条落库）。
        """
        raise NotImplementedError
