"""文本归一化、合并键与内容哈希。

版本化的判断依据是「正文、截止时间、附件」三项的规范化结果；
合并公告的依据是「采购单位 + 公告编号」，缺少编号的公告永远不自动合并。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

_WS_RE = re.compile(r"\s+")


def clean(value: str | None) -> str:
    """去除首尾空白并把连续空白压成单个空格；None 转为空串。"""
    if value is None:
        return ""
    return _WS_RE.sub(" ", str(value)).strip()


def normalize_text(value: str | None) -> str:
    """正文归一化：统一空白，便于发现「只有空格/换行差异」的重复抓取。"""
    return clean(value)


def canonical_attachments(attachments: list[dict] | None) -> str:
    """把附件列表转成稳定的规范字符串，作为版本比较的一部分。"""
    if not attachments:
        return ""
    norm = [
        {
            "name": clean(item.get("name")),
            "url": clean(item.get("url")),
        }
        for item in attachments
    ]
    norm.sort(key=lambda x: (x["name"], x["url"]))
    return json.dumps(norm, ensure_ascii=False, sort_keys=True)


def merge_key(purchaser: str | None, notice_no: str | None) -> str | None:
    """生成合并键。编号缺失时返回 None，表示该公告必须单独保存。"""
    no = clean(notice_no)
    unit = clean(purchaser)
    if not no:
        return None
    return f"{unit}||{no}"


@dataclass
class ContentFingerprint:
    """一次详情抓取的内容指纹，决定是否需要新增版本。"""

    body_norm: str
    deadline: str
    attachments_norm: str
    content_hash: str

    @classmethod
    def build(
        cls,
        body: str | None,
        deadline: str | None,
        attachments: list[dict] | None,
    ) -> "ContentFingerprint":
        body_norm = normalize_text(body)
        dl = clean(deadline)
        att_norm = canonical_attachments(attachments)
        digest = hashlib.sha256(
            "\x1f".join([body_norm, dl, att_norm]).encode("utf-8")
        ).hexdigest()
        return cls(body_norm, dl, att_norm, digest)


def raw_content_hash(raw: str | None) -> str:
    """原始内容哈希，用于来源记录层面的去重（整条抓取是否重复）。"""
    return hashlib.sha256(clean(raw).encode("utf-8")).hexdigest()
