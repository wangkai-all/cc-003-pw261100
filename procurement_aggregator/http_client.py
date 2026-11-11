"""带重试与软拦截识别的 HTTP 客户端（仅用 urllib，无第三方依赖）。

重试范围（临时故障）：连接错误、读超时、HTTP 429 / 500 / 502 / 503 / 504。
不重试：4xx 其它状态码、以及 200 但内容是维护页/登录页（SoftBlockedError）。
"""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from typing import Callable

RETRY_STATUS = {429, 500, 502, 503, 504}
DEFAULT_TIMEOUT = 10
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF = 0.4  # 秒，第 n 次重试等待 backoff * 2**(n-1)，测试可注入替换


class FetchError(Exception):
    """非临时性的抓取失败（重试后仍失败，或 4xx）。"""


class FetchTimeoutError(FetchError):
    """重试后仍然超时。"""


class SoftBlockedError(Exception):
    """HTTP 200 但返回的是维护页/登录页，不是真正的公告内容。"""


class HttpClient:
    def __init__(self, timeout: int = DEFAULT_TIMEOUT, retries: int = DEFAULT_RETRIES,
                 backoff: float = DEFAULT_BACKOFF,
                 sleep: Callable[[float], None] = time.sleep):
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.sleep = sleep

    def get_text(self, url: str, soft_markers: list[str] | None = None) -> str:
        text, _ = self._get(url, soft_markers)
        return text

    def get_json(self, url: str, soft_markers: list[str] | None = None) -> dict:
        text, _ = self._get(url, soft_markers)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise FetchError(f"JSON 解析失败 {url}: {exc}") from exc

    def _get(self, url: str, soft_markers: list[str] | None) -> tuple[str, int]:
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "procurement-aggregator/1.0",
                             "Accept": "text/html, application/json"},
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    charset = resp.headers.get_content_charset() or "utf-8"
                    text = resp.read().decode(charset, errors="replace")
                    status = resp.status
            except urllib.error.HTTPError as exc:
                if exc.code in RETRY_STATUS and attempt < self.retries:
                    last_exc = exc
                    self._wait(attempt)
                    continue
                raise FetchError(f"HTTP {exc.code} 抓取失败: {url}") from exc
            except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
                # 连接拒绝、读超时等均视为临时故障
                if attempt < self.retries:
                    last_exc = exc
                    self._wait(attempt)
                    continue
                if isinstance(exc, (socket.timeout, TimeoutError)):
                    raise FetchTimeoutError(f"抓取超时（已重试{self.retries}次）: {url}") from exc
                raise FetchError(f"网络错误: {url}: {exc}") from exc

            self._raise_if_soft_blocked(text, soft_markers, url)
            return text, status

        # 理论上不会走到这里
        raise FetchError(f"抓取失败: {url}: {last_exc}")

    @staticmethod
    def _raise_if_soft_blocked(text: str, markers: list[str] | None, url: str) -> None:
        if not markers:
            return
        head = text[:4000]
        for marker in markers:
            if marker and marker in head:
                raise SoftBlockedError(
                    f"返回内容命中维护/登录标记「{marker}」，不是正常公告: {url}"
                )

    def _wait(self, attempt: int) -> None:
        self.sleep(self.backoff * (2 ** attempt))
