"""命令行入口。

用法：
  python -m procurement_aggregator collect [--source html_demo] [--base-url URL] [--db PATH]
  python -m procurement_aggregator web [--port 8088] [--db PATH]
  python -m procurement_aggregator status [--db PATH]
"""
from __future__ import annotations

import argparse
import sys

from .config import DEFAULT_BASE_URL, DEFAULT_DB_PATH, default_sources
from .db import Database, open_db
from .http_client import HttpClient
from .pipeline import Pipeline
from .webapp.server import serve


def cmd_collect(args: argparse.Namespace) -> int:
    sources = default_sources(args.base_url)
    if args.source:
        sources = [s for s in sources if s.code in args.source]
        if not sources:
            print(f"未知来源: {args.source}", file=sys.stderr)
            return 2
    conn = open_db(args.db)
    try:
        pipeline = Pipeline(Database(conn), HttpClient(
            timeout=args.timeout, retries=args.retries, backoff=args.backoff))
        results = pipeline.collect_all(sources)
    finally:
        conn.close()

    print(f"采集完成（来源 {len(results)} 个）：")
    ok = True
    for r in results:
        print(f"  [{r.status:>12}] {r.source_code}: 保存 {r.saved} 条来源记录，"
              f"新增版本 {r.new_versions} 个。{r.message}")
        if r.status not in ("success",):
            ok = False
    return 0 if ok else 1


def cmd_web(args: argparse.Namespace) -> int:
    server = serve(args.db, args.host, args.port)
    print(f"检索网页已启动: http://{args.host}:{args.port}  （Ctrl+C 退出）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    conn = open_db(args.db)
    db = Database(conn)
    try:
        print("数据统计:", db.counts())
        print("\n来源进度:")
        for row in conn.execute("SELECT * FROM source_progress").fetchall():
            print(f"  {row['source_code']}: 断点={row['last_cursor'] or '无'} "
                  f"上次成功={row['last_success_at'] or '-'}")
        print("\n最近执行:")
        for row in conn.execute(
                "SELECT id, source_code, status, list_checked, detail_ok, "
                "detail_failed, soft_blocked, message FROM run "
                "ORDER BY id DESC LIMIT 10").fetchall():
            print(f"  run#{row['id']} {row['source_code']} [{row['status']}] "
                  f"列表={row['list_checked']} 详情ok={row['detail_ok']} "
                  f"失败={row['detail_failed']} 软拦截={row['soft_blocked']} "
                  f"{row['message'] or ''}")
    finally:
        conn.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="procurement_aggregator",
                                     description="采购公告多来源采集与版本对比工具")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite 数据库路径")
    sub = parser.add_subparsers(dest="command", required=True)

    p_collect = sub.add_parser("collect", help="执行采集（可重复运行，自动去重/续采）")
    p_collect.add_argument("--source", action="append",
                           help="只采集指定来源，可多次给出；默认全部")
    p_collect.add_argument("--base-url", default=DEFAULT_BASE_URL,
                           help="模拟站点地址（默认 %(default)s）")
    p_collect.add_argument("--timeout", type=int, default=10)
    p_collect.add_argument("--retries", type=int, default=3)
    p_collect.add_argument("--backoff", type=float, default=0.4)
    p_collect.set_defaults(func=cmd_collect)

    p_web = sub.add_parser("web", help="启动检索网页")
    p_web.add_argument("--host", default="127.0.0.1")
    p_web.add_argument("--port", type=int, default=8088)
    p_web.set_defaults(func=cmd_web)

    p_status = sub.add_parser("status", help="查看采集进度与统计")
    p_status.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
