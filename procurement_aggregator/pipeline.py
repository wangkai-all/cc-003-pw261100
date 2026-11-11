"""采集编排：逐来源独立执行，单个来源失败不影响其他来源。

每个来源维护独立的采集进度（游标/页码）：
- 完整成功：进度清空，下次从头增量扫描（靠 remote_id + 内容哈希去重）；
- 中途失败/超时：保存恢复点，下次从恢复点继续；
- 维护页/登录页（SoftBlockedError）：记为 soft_blocked，不算采集成功；
- 详情页取不到：抛出异常，本次采集标记未完成。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .config import SourceConfig
from .db import Database
from .http_client import FetchError, FetchTimeoutError, HttpClient, SoftBlockedError
from .sources import BaseSource
from .sources.html_source import HtmlSource
from .sources.json_source import JsonSource

log = logging.getLogger("aggregator.pipeline")


def build_source(config: SourceConfig, client: HttpClient) -> BaseSource:
    if config.kind == "html":
        return HtmlSource(config, client)
    if config.kind == "json":
        return JsonSource(config, client)
    raise ValueError(f"未知来源类型: {config.kind}")


@dataclass
class SourceResult:
    source_code: str
    status: str
    saved: int
    new_versions: int
    message: str = ""


class Pipeline:
    def __init__(self, db: Database, client: HttpClient | None = None):
        self.db = db
        self.client = client or HttpClient()

    def collect_all(self, sources: list[SourceConfig]) -> list[SourceResult]:
        """依次采集所有来源；任何来源的异常都被隔离，不影响后续来源。"""
        results: list[SourceResult] = []
        for config in sources:
            try:
                results.append(self.collect_one(config))
            except Exception as exc:  # 兜底：来源之间互不影响
                log.exception("来源 %s 采集发生未预期异常", config.code)
                results.append(SourceResult(
                    config.code, "failed", 0, 0, f"未预期异常: {exc}"
                ))
        return results

    def collect_one(self, config: SourceConfig) -> SourceResult:
        self.db.ensure_source(
            config.code, config.name, config.kind, config.base_url, config.settings
        )
        progress = self.db.get_progress(config.code) or {}
        resume_cursor = progress.get("last_cursor")
        run_id = self.db.start_run(config.code, resume_cursor)
        source = build_source(config, self.client)

        saved = 0
        new_versions = 0
        stats = {"list_checked": 0, "detail_ok": 0,
                 "detail_failed": 0, "soft_blocked": 0}

        def progress_cb(cursor: str | None) -> None:
            # 每推进一页/一条游标就落库，中断后可从这里恢复
            self.db.save_progress(config.code, cursor, run_id, success=False)

        def sink(notice) -> None:
            # 详情抓取成功后立即入库：后续页面失败时，已抓数据不丢
            nonlocal saved, new_versions
            if not notice.remote_id:
                log.warning("来源 %s 存在缺少标识的条目，已跳过", config.code)
                return
            res = self.db.save_notice(notice)
            saved += 1
            if res["new_version"]:
                new_versions += 1

        try:
            outcome = source.fetch(resume_cursor, progress_cb, sink)
        except SoftBlockedError as exc:
            stats["soft_blocked"] = 1
            current = self.db.get_progress(config.code) or {}
            self.db.finish_run(run_id, "soft_blocked", stats,
                               current.get("last_cursor"), str(exc))
            return SourceResult(config.code, "soft_blocked", saved, new_versions, str(exc))
        except FetchTimeoutError as exc:
            current = self.db.get_progress(config.code) or {}
            self.db.finish_run(run_id, "failed", stats,
                               current.get("last_cursor"), f"超时: {exc}")
            return SourceResult(config.code, "failed", saved, new_versions, f"超时: {exc}")
        except FetchError as exc:
            current = self.db.get_progress(config.code) or {}
            self.db.finish_run(run_id, "failed", stats,
                               current.get("last_cursor"), str(exc))
            return SourceResult(config.code, "failed", saved, new_versions, str(exc))

        stats = {
            "list_checked": outcome.list_checked,
            "detail_ok": outcome.detail_ok,
            "detail_failed": outcome.detail_failed,
            "soft_blocked": outcome.soft_blocked,
        }
        if outcome.detail_failed:
            # 详情没取全不能算采集完成：保留恢复点，下次续采
            current = self.db.get_progress(config.code) or {}
            msg = f"{outcome.detail_failed} 条详情页抓取失败，保留断点待续采"
            self.db.finish_run(run_id, "partial", stats,
                               current.get("last_cursor"), msg)
            return SourceResult(config.code, "partial", saved, new_versions, msg)

        self.db.finish_run(run_id, "success", stats, None)
        self.db.save_progress(config.code, None, run_id, success=True)
        return SourceResult(
            config.code, "success", saved, new_versions,
            f"列表 {outcome.list_checked} 条，详情成功 {outcome.detail_ok} 条",
        )
