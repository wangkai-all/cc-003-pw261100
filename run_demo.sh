#!/usr/bin/env bash
# 一键演示：启动本地模拟站点 -> 分场景采集 -> 启动检索网页。
# 全程不访问任何真实网站，只用标准库，无需 pip 安装。
set -euo pipefail

cd "$(dirname "$0")"
PORT_MOCK=${PORT_MOCK:-8099}
PORT_WEB=${PORT_WEB:-8088}
DB=${DB:-data/demo.db}
BASE="http://127.0.0.1:${PORT_MOCK}"

export PYTHONWARNINGS=ignore
rm -f "$DB" "$DB-wal" "$DB-shm"

echo "==> [1/7] 启动本地模拟站点 :$PORT_MOCK"
python3 -m mock_site --port "$PORT_MOCK" &
MOCK_PID=$!
trap 'kill $MOCK_PID 2>/dev/null || true' EXIT
sleep 1

echo "==> [2/7] 首次全量采集（HTML 列表+详情页 / JSON 游标接口）"
python3 -m procurement_aggregator --db "$DB" collect --base-url "$BASE"

echo "==> [3/7] 重复采集：内容未变，不应产生新版本"
python3 -m procurement_aggregator --db "$DB" collect --base-url "$BASE"

echo "==> [4/7] 模拟公告更新（H002 正文/截止时间/附件变化）后再次采集"
curl -s "$BASE/control?scenario=update_v2" >/dev/null
python3 -m procurement_aggregator --db "$DB" collect --source html_demo --base-url "$BASE"
curl -s "$BASE/control?scenario=update_v3" >/dev/null
python3 -m procurement_aggregator --db "$DB" collect --source html_demo --base-url "$BASE"

echo "==> [5/7] 模拟翻页中断（HTML 第 2 页起 503），验证断点保留"
curl -s "$BASE/control?reset" >/dev/null
curl -s "$BASE/control?fail_after_html=2" >/dev/null
python3 -m procurement_aggregator --db "$DB" collect --source html_demo --base-url "$BASE" || true
curl -s "$BASE/control?reset" >/dev/null
echo "    恢复后续采："
python3 -m procurement_aggregator --db "$DB" collect --source html_demo --base-url "$BASE"

echo "==> [6/7] 模拟维护页（HTTP 200 但内容是维护页），验证软拦截识别"
curl -s "$BASE/control?mode=maintenance" >/dev/null
python3 -m procurement_aggregator --db "$DB" collect --base-url "$BASE" || true
curl -s "$BASE/control?reset" >/dev/null

echo "==> [7/7] 采集统计与进度"
python3 -m procurement_aggregator --db "$DB" status

echo
echo "演示数据已就绪。启动检索网页：http://127.0.0.1:${PORT_WEB}"
echo "按 Ctrl+C 退出。"
python3 -m procurement_aggregator --db "$DB" web --port "$PORT_WEB"
