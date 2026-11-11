"""SQLite 数据访问层。

核心表：
- source / source_progress：来源与每个来源独立的采集进度（游标）
- run：每次采集执行记录（成功/部分成功/失败/软拦截，可断点续采）
- announcement：合并后的公告主记录（merge_key 为 采购单位||公告编号；编号缺失为 NULL）
- source_record：各来源下的原始公告记录，各自保留链接与原始内容
- version：公告版本，按 (announcement_id, content_hash) 去重，相同内容不产生新版本
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .normalize import ContentFingerprint, clean, merge_key, raw_content_hash

SCHEMA = """
CREATE TABLE IF NOT EXISTS source (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    base_url TEXT NOT NULL,
    settings_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_code TEXT NOT NULL REFERENCES source(code),
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    list_checked INTEGER NOT NULL DEFAULT 0,
    detail_ok INTEGER NOT NULL DEFAULT 0,
    detail_failed INTEGER NOT NULL DEFAULT 0,
    soft_blocked INTEGER NOT NULL DEFAULT 0,
    resume_cursor TEXT,
    message TEXT
);
CREATE INDEX IF NOT EXISTS idx_run_source ON run(source_code);

CREATE TABLE IF NOT EXISTS source_progress (
    source_code TEXT PRIMARY KEY REFERENCES source(code),
    last_cursor TEXT,
    last_success_at TEXT,
    last_run_id INTEGER REFERENCES run(id),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS announcement (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    merge_key TEXT UNIQUE,
    title TEXT NOT NULL,
    purchaser TEXT,
    notice_no TEXT,
    region TEXT,
    published_at TEXT,
    latest_hash TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_record (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    announcement_id INTEGER NOT NULL REFERENCES announcement(id),
    source_code TEXT NOT NULL REFERENCES source(code),
    remote_id TEXT NOT NULL,
    url TEXT,
    raw_content TEXT,
    last_raw_hash TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE(source_code, remote_id)
);
CREATE INDEX IF NOT EXISTS idx_record_announcement ON source_record(announcement_id);

CREATE TABLE IF NOT EXISTS version (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    announcement_id INTEGER NOT NULL REFERENCES announcement(id),
    source_record_id INTEGER NOT NULL REFERENCES source_record(id),
    source_code TEXT NOT NULL,
    version_no INTEGER NOT NULL,
    body TEXT,
    deadline TEXT,
    attachments_json TEXT NOT NULL DEFAULT '[]',
    content_hash TEXT NOT NULL,
    raw_content TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(announcement_id, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_version_announcement ON version(announcement_id);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def open_db(path: str) -> sqlite3.Connection:
    """打开（必要时创建）数据库，启用外键与字典式行访问。"""
    db_path = Path(path)
    if db_path.parent and not db_path.parent.exists():
        db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    return conn


class Database:
    """采集器使用的数据库包装；Web 端按请求各自 open_db 后同样可用。"""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    @contextmanager
    def transaction(self):
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # ---------------- 来源与进度 ----------------
    def ensure_source(self, code: str, name: str, kind: str, base_url: str, settings: dict) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO source(code, name, kind, base_url, settings_json, created_at) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET "
                "name=excluded.name, kind=excluded.kind, base_url=excluded.base_url, "
                "settings_json=excluded.settings_json",
                (code, name, kind, base_url, json.dumps(settings, ensure_ascii=False), utc_now()),
            )
            conn.execute(
                "INSERT OR IGNORE INTO source_progress(source_code, updated_at) VALUES(?,?)",
                (code, utc_now()),
            )

    def get_progress(self, source_code: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM source_progress WHERE source_code=?", (source_code,)
        ).fetchone()
        return dict(row) if row else None

    def save_progress(self, source_code: str, cursor: str | None, run_id: int | None,
                      success: bool) -> None:
        with self.transaction() as conn:
            if success:
                conn.execute(
                    "UPDATE source_progress SET last_cursor=?, last_success_at=?, "
                    "last_run_id=?, updated_at=? WHERE source_code=?",
                    (cursor, utc_now(), run_id, utc_now(), source_code),
                )
            else:
                conn.execute(
                    "UPDATE source_progress SET last_cursor=COALESCE(?, last_cursor), "
                    "last_run_id=?, updated_at=? WHERE source_code=?",
                    (cursor, run_id, utc_now(), source_code),
                )

    # ---------------- 采集执行记录 ----------------
    def start_run(self, source_code: str, resume_cursor: str | None) -> int:
        cur = self.conn.execute(
            "INSERT INTO run(source_code, status, started_at, resume_cursor) "
            "VALUES(?, 'running', ?, ?)",
            (source_code, utc_now(), resume_cursor),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, stats: dict, resume_cursor: str | None,
                   message: str = "") -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE run SET status=?, finished_at=?, list_checked=?, detail_ok=?, "
                "detail_failed=?, soft_blocked=?, resume_cursor=?, message=? WHERE id=?",
                (
                    status, utc_now(),
                    stats.get("list_checked", 0), stats.get("detail_ok", 0),
                    stats.get("detail_failed", 0), stats.get("soft_blocked", 0),
                    resume_cursor, message, run_id,
                ),
            )

    def list_runs(self, source_code: str | None = None) -> list[dict]:
        sql = "SELECT * FROM run"
        params: tuple = ()
        if source_code:
            sql += " WHERE source_code=?"
            params = (source_code,)
        sql += " ORDER BY id DESC LIMIT 50"
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    # ---------------- 公告保存（合并 + 版本化） ----------------
    def save_notice(self, notice: "NoticeInput") -> dict[str, Any]:
        """保存一条抓取到的公告，返回 {'announcement_id', 'new_version': bool}。

        合并规则：有公告编号时按 采购单位||公告编号 合并；无编号一律新建，
        绝不因标题相同而合并。版本规则：正文/截止时间/附件的指纹已存在则不新增版本。
        """
        fp = ContentFingerprint.build(notice.body, notice.deadline, notice.attachments)
        key = merge_key(notice.purchaser, notice.notice_no)
        now = utc_now()
        rhash = raw_content_hash(notice.raw)

        with self.transaction() as conn:
            record = conn.execute(
                "SELECT * FROM source_record WHERE source_code=? AND remote_id=?",
                (notice.source_code, notice.remote_id),
            ).fetchone()

            if record is not None:
                record_id = int(record["id"])
                ann_id = int(record["announcement_id"])
                # 同一来源同一条目：刷新链接与原始内容（原始内容同时逐版本留存）
                conn.execute(
                    "UPDATE source_record SET url=?, raw_content=?, last_raw_hash=?, "
                    "last_seen_at=? WHERE id=?",
                    (notice.url, notice.raw, rhash, now, record_id),
                )
                ann = conn.execute(
                    "SELECT * FROM announcement WHERE id=?", (ann_id,)
                ).fetchone()
                # 编号是后来才出现的：补上 merge_key，让后续来源能合并进来
                if key and not ann["merge_key"]:
                    conn.execute(
                        "UPDATE announcement SET merge_key=? WHERE id=?", (key, ann_id)
                    )
            else:
                ann = None
                if key is not None:
                    ann = conn.execute(
                        "SELECT * FROM announcement WHERE merge_key=?", (key,)
                    ).fetchone()
                if ann is None:
                    cur = conn.execute(
                        "INSERT INTO announcement(merge_key, title, purchaser, notice_no, "
                        "region, published_at, latest_hash, created_at, updated_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (
                            key, clean(notice.title), clean(notice.purchaser),
                            clean(notice.notice_no), clean(notice.region),
                            clean(notice.published_at), fp.content_hash, now, now,
                        ),
                    )
                    ann_id = int(cur.lastrowid)
                else:
                    ann_id = int(ann["id"])
                cur = conn.execute(
                    "INSERT INTO source_record(announcement_id, source_code, remote_id, "
                    "url, raw_content, last_raw_hash, first_seen_at, last_seen_at) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (ann_id, notice.source_code, notice.remote_id, notice.url,
                     notice.raw, rhash, now, now),
                )
                record_id = int(cur.lastrowid)

            self._update_header(conn, ann_id, notice, now)

            # 版本去重：同公告下内容指纹一致（无论来自哪个来源）都不新增版本
            dup = conn.execute(
                "SELECT id FROM version WHERE announcement_id=? AND content_hash=?",
                (ann_id, fp.content_hash),
            ).fetchone()
            new_version = dup is None
            if new_version:
                row = conn.execute(
                    "SELECT COALESCE(MAX(version_no), 0) AS m FROM version "
                    "WHERE announcement_id=?", (ann_id,)
                ).fetchone()
                next_no = int(row["m"]) + 1
                conn.execute(
                    "INSERT INTO version(announcement_id, source_record_id, source_code, "
                    "version_no, body, deadline, attachments_json, content_hash, "
                    "raw_content, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        ann_id, record_id, notice.source_code, next_no,
                        fp.body_norm, fp.deadline,
                        json.dumps(notice.attachments or [], ensure_ascii=False),
                        fp.content_hash, notice.raw, now,
                    ),
                )
            conn.execute(
                "UPDATE announcement SET latest_hash=?, updated_at=? WHERE id=?",
                (fp.content_hash, now, ann_id),
            )
        return {"announcement_id": ann_id, "new_version": new_version}

    def _update_header(self, conn: sqlite3.Connection, ann_id: int,
                       notice: "NoticeInput", now: str) -> None:
        """刷新公告头信息：发布时间保留最早的一条，其他字段补齐/更新。"""
        row = conn.execute(
            "SELECT published_at FROM announcement WHERE id=?", (ann_id,)
        ).fetchone()
        old_pub = row["published_at"] or ""
        new_pub = clean(notice.published_at)
        published = min(p for p in (old_pub, new_pub) if p) if (old_pub or new_pub) else ""
        conn.execute(
            "UPDATE announcement SET title=?, purchaser=?, notice_no=?, region=?, "
            "published_at=?, updated_at=? WHERE id=?",
            (
                clean(notice.title), clean(notice.purchaser), clean(notice.notice_no),
                clean(notice.region), published, now, ann_id,
            ),
        )

    # ---------------- 检索与查看 ----------------
    def search_announcements(self, keyword: str | None = None, region: str | None = None,
                             date_from: str | None = None, date_to: str | None = None,
                             limit: int = 100) -> list[dict]:
        sql = (
            "SELECT DISTINCT a.*, "
            "(SELECT COUNT(*) FROM version v WHERE v.announcement_id=a.id) AS version_count, "
            "(SELECT COUNT(*) FROM source_record s WHERE s.announcement_id=a.id) AS source_count "
            "FROM announcement a "
            "LEFT JOIN version v ON v.announcement_id=a.id "
            "WHERE 1=1"
        )
        params: list[Any] = []
        if keyword:
            for word in keyword.split():
                sql += (
                    " AND (COALESCE(a.title,'') || COALESCE(a.purchaser,'') || "
                    "COALESCE(a.notice_no,'') || COALESCE(a.region,'') || "
                    "COALESCE(v.body,'')) LIKE ?"
                )
                params.append(f"%{word}%")
        if region:
            sql += " AND a.region LIKE ?"
            params.append(f"%{clean(region)}%")
        if date_from:
            sql += " AND COALESCE(a.published_at,'') >= ?"
            params.append(clean(date_from))
        if date_to:
            sql += " AND COALESCE(a.published_at,'') <= ?"
            params.append(clean(date_to))
        sql += " ORDER BY a.published_at DESC, a.id DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def get_announcement(self, ann_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM announcement WHERE id=?", (ann_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_records(self, ann_id: int) -> list[dict]:
        return [
            dict(r) for r in self.conn.execute(
                "SELECT * FROM source_record WHERE announcement_id=? ORDER BY id", (ann_id,)
            ).fetchall()
        ]

    def list_versions(self, ann_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM version WHERE announcement_id=? ORDER BY version_no", (ann_id,)
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["attachments"] = json.loads(d.get("attachments_json") or "[]")
            result.append(d)
        return result

    def get_version(self, version_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM version WHERE id=?", (version_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["attachments"] = json.loads(d.get("attachments_json") or "[]")
        return d

    def counts(self) -> dict[str, int]:
        return {
            "announcements": self.conn.execute(
                "SELECT COUNT(*) c FROM announcement").fetchone()["c"],
            "records": self.conn.execute(
                "SELECT COUNT(*) c FROM source_record").fetchone()["c"],
            "versions": self.conn.execute(
                "SELECT COUNT(*) c FROM version").fetchone()["c"],
        }


class NoticeInput:
    """来源适配器产出的标准公告结构。"""

    def __init__(self, source_code: str, remote_id: str, url: str | None, title: str | None,
                 purchaser: str | None, notice_no: str | None, region: str | None,
                 published_at: str | None, body: str | None, deadline: str | None,
                 attachments: list[dict] | None, raw: str | None):
        self.source_code = source_code
        self.remote_id = clean(remote_id)
        self.url = url
        self.title = title
        self.purchaser = purchaser
        self.notice_no = notice_no
        self.region = region
        self.published_at = published_at
        self.body = body
        self.deadline = deadline
        self.attachments = attachments or []
        self.raw = raw
