# 采购公告采集工具

把多个站点的采购公告汇总到一个 SQLite 库里，支持按关键词、地区、发布时间检索，
并为每条公告保留历次内容变化（版本）与各来源的原始链接。**纯 Python 标准库实现，
零第三方依赖，自带本地模拟站点，不访问任何真实网站。**

## 快速开始

```bash
# 方式一：一键演示（自动起模拟站点、跑全部场景、最后启动网页）
bash run_demo.sh

# 方式二：手动分步
python3 -m mock_site --port 8099 &              # 1. 启动本地模拟站点
python3 -m procurement_aggregator collect       # 2. 采集全部来源
python3 -m procurement_aggregator status        # 3. 查看进度与统计
python3 -m procurement_aggregator web --port 8088   # 4. 打开 http://127.0.0.1:8088
```

运行测试：

```bash
python3 -m unittest tests.test_mini_dom tests.test_merge_versions \
                     tests.test_failures tests.test_web
```

## 两种来源

| 来源 code | 类型 | 分页方式 | 说明 |
|---|---|---|---|
| `html_demo` | HTML 列表页 + 详情页 | 页码 `?page=N`，进度游标 `page:N` | 列表只有元数据，正文/截止时间/附件必须再抓详情页 |
| `json_demo` | 带游标的 JSON 接口 | 不透明游标 `?cursor=cur-N` | 返回 `{items, next_cursor}`，游标即采集进度 |

来源配置集中在 `procurement_aggregator/config.py`，字段映射、选择器、软拦截标记均可改。

## 数据模型与合并/版本规则

```
announcement（合并后的公告主记录）
  ├── source_record（各来源的原始记录：各自保留 url 与 raw_content）
  └── version（历次内容版本）
source / source_progress / run（来源、独立采集进度、每次执行记录）
```

- **合并**：同一公告按 `采购单位 || 公告编号` 合并（`announcement.merge_key` 唯一）。
  不同来源抓到同一公告时挂到同一条主记录下，但各自的链接和原始内容分别保留。
- **缺编号不合并**：公告编号缺失时 `merge_key` 为 NULL，永远单独保存；
  即使标题完全相同（演示数据 H003/H004）也不会合并。
- **版本化**：对「正文、截止时间、附件」三项规范化后算 SHA-256 指纹。
  指纹在该公告下已存在就**不新增版本**（重复抓取、跨来源相同内容都去重）；
  任一项变化才新增一个版本。附件比较前会按名称/URL 排序规范化，
  避免仅顺序不同就误判为变化。
- **来源级幂等**：`source_record` 按 `(source_code, remote_id)` 唯一，
  重复抓到同一条目只刷新链接、原始内容与最近抓取时间，不产生重复记录。

## 采集可靠性

- **每来源独立进度**：`source_progress.last_cursor` 单独记录每个来源的恢复点，
  每翻一页/推进一条游标就落库。
- **逐条入库**：公告详情抓取成功后立即写库（sink 流式处理），
  后续页面失败时前面已抓的数据不丢。
- **重试**：连接错误、读超时、HTTP 429/500/502/503/504 自动指数退避重试
  （默认 3 次）；4xx 其它状态码不重试。
- **详情不算完**：详情页抓取失败或缺少正文容器时，本次执行标记
  `failed`/`partial`，保留断点，下次续采，绝不当作采集成功。
- **软拦截识别**：HTTP 200 但内容命中维护页/登录页标记（如「系统维护」
  「用户登录」、JSON 的 `status: maintenance/login`）时抛 `SoftBlockedError`，
  记为 `soft_blocked`，不会写成正常公告。
- **来源隔离**：`collect_all` 逐个来源执行并捕获全部异常，
  一个来源失败不影响其他来源。

执行状态：`success / partial / failed / soft_blocked / running`，
网页「采集记录」页和 `status` 命令均可查看。

## 网页功能

- `/` 公告检索：关键词（空格分多词，AND）、地区下拉、发布时间区间；
  列表显示版本数与来源数。
- `/notice?id=` 公告详情：公告头信息、各来源记录（链接 + 可展开的原始内容）、
  历次版本列表。
- `/diff?a=&b=` 版本对比：正文逐行 diff（绿增红删）、截止时间与附件并列比较并标注是否变化。
- `/runs` 各来源独立进度（断点游标、上次成功时间）与最近执行记录。
- 另提供 `/api/search` JSON 接口，查询参数与网页一致。

## 模拟站点与故障注入

模拟站点 `mock_site` 通过 `GET /control` 注入各种场景，便于演示和测试：

| 控制 URL | 效果 |
|---|---|
| `/control?scenario=update_v2` / `update_v3` | 把公告 H002 切换到第 2/3 版（正文、截止时间、附件变化） |
| `/control?fail_after_html=2` | HTML 第 2 次列表请求起持续 503（翻页中断） |
| `/control?fail_after_json=2` | JSON 第 2 次列表请求起持续 503（游标中断） |
| `/control?flaky=2` | 接下来 2 次请求临时 503 后自动恢复（验证重试） |
| `/control?mode=maintenance` / `login` | 所有页面 200 返回维护页/登录页（验证软拦截） |
| `/control?slow=1.5` | 每请求延迟 1.5 秒（配合短超时验证超时处理） |
| `/control?reset` | 复位全部故障注入与场景 |
| `/control?state` | 查看当前故障状态 |

演示数据覆盖：跨来源重复公告（H001/J101 同单位同编号）、公告三次更新、
标题相同但无编号的两条公告（H003/H004）、多页翻页中断、维护页/登录页、瞬时限流。

## 目录结构

```
procurement_aggregator/
  config.py          来源配置（选择器、字段映射、软拦截标记）
  mini_dom.py        标准库实现的极简 HTML 解析与 CSS 选择器子集
  normalize.py       文本归一化、合并键、内容指纹
  http_client.py     重试、超时、软拦截识别
  db.py              SQLite 建表、合并/版本化、检索
  sources/           html_source.py（HTML 来源）、json_source.py（游标 JSON 来源）
  pipeline.py        采集编排：独立进度、断点续采、失败隔离
  webapp/            检索网页、详情、版本对比、采集记录
  __main__.py        CLI：collect / web / status
mock_site/           本地模拟站点与故障注入
tests/               端到端 unittest（18 个用例）
run_demo.sh          一键演示脚本
```

## 设计说明

- 选择器只实现了标签/`#id`/`.class`/后代组合与 `@attr` 取值，
  满足演示站点结构，不追求完整 CSS；解析器对未闭合标签做了容错。
- 时间以 UTC ISO-8601 存储；演示数据的发布时间是 `YYYY-MM-DD`，
  日期区间过滤按字符串比较即可正确工作。
- 数据库开启 WAL 与外键；写操作在 `BEGIN IMMEDIATE` 事务中进行，
  合并与版本判断在同一事务内完成，避免并发采集时重复建主记录。
