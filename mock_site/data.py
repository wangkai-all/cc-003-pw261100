"""本地模拟站点的演示数据与运行状态。

模拟站点不依赖任何真实网站，数据覆盖四类演示场景：
1. 重复公告：H001 与 J101 采购单位+公告编号相同，应跨来源合并为一条公告；
2. 公告更新：H002 提供 v1/v2/v3 三份内容，正文、截止时间、附件逐次变化；
3. 翻页中断：通过 /control 设置 fail_after，让第 N 次列表请求开始失败；
4. 维护页：通过 /control 进入 maintenance，所有页面 200 返回维护/登录内容；
另外 H003 / H004 只有标题相同、没有公告编号，必须保持为两条独立公告。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------- 基础数据
# 注意：H002 的内容是可变的（见 /control?scenario=update_*），
# 其余公告内容固定。
NOTICES: dict[str, dict] = {
    "H001": {
        "remote_id": "H001",
        "title": "市第一医院医疗设备采购公告",
        "purchaser": "市第一医院",
        "notice_no": "YYCG-2026-001",
        "region": "华东",
        "published_at": "2026-11-01",
        "deadline": "2026-11-30 17:00",
        "body": "市第一医院就CT机及配套设备进行公开招标，欢迎符合条件的供应商参加投标。",
        "attachments": [
            {"name": "招标参数.pdf", "url": "/files/H001-spec.pdf"},
        ],
    },
    "H002": {
        "remote_id": "H002",
        "title": "城市图书馆物业服务采购公告",
        "purchaser": "城市图书馆",
        "notice_no": "TSCG-2026-002",
        "region": "华北",
        "published_at": "2026-11-03",
        "deadline": "2026-11-25 18:00",
        "body": "城市图书馆采购年度物业服务，服务范围包括保洁、安保与绿化养护。",
        "attachments": [
            {"name": "物业服务需求书.pdf", "url": "/files/H002-req-v1.pdf"},
        ],
    },
    # H003 / H004：标题相同，均无公告编号，不可合并
    "H003": {
        "remote_id": "H003",
        "title": "办公耗材采购公告",
        "purchaser": "市政服务中心",
        "notice_no": "",
        "region": "华南",
        "published_at": "2026-11-05",
        "deadline": "2026-11-20 12:00",
        "body": "市政服务中心采购打印纸、硒鼓等办公耗材一批，按季度配送。",
        "attachments": [],
    },
    "H004": {
        "remote_id": "H004",
        "title": "办公耗材采购公告",
        "purchaser": "市档案馆",
        "notice_no": "",
        "region": "西北",
        "published_at": "2026-11-06",
        "deadline": "2026-11-22 12:00",
        "body": "市档案馆采购档案盒、打印纸等办公耗材，仅限本地供应商报名。",
        "attachments": [],
    },
    "H005": {
        "remote_id": "H005",
        "title": "实验中学智慧教室建设采购公告",
        "purchaser": "实验中学",
        "notice_no": "JYCG-2026-005",
        "region": "西南",
        "published_at": "2026-11-08",
        "deadline": "2026-12-01 17:30",
        "body": "实验中学智慧教室项目采购交互平板、录播设备与配套软件，含安装调试。",
        "attachments": [
            {"name": "技术方案模板.docx", "url": "/files/H005-tpl.docx"},
        ],
    },
}

# H002 的三个版本，由 /control 切换
H002_VERSIONS = {
    "v1": {
        "body": "城市图书馆采购年度物业服务，服务范围包括保洁、安保与绿化养护。",
        "deadline": "2026-11-25 18:00",
        "attachments": [
            {"name": "物业服务需求书.pdf", "url": "/files/H002-req-v1.pdf"},
        ],
    },
    "v2": {
        "body": "城市图书馆采购年度物业服务，服务范围包括保洁、安保、绿化养护及前台接待。",
        "deadline": "2026-12-05 18:00",
        "attachments": [
            {"name": "物业服务需求书.pdf", "url": "/files/H002-req-v1.pdf"},
        ],
    },
    "v3": {
        "body": "城市图书馆采购年度物业服务，服务范围包括保洁、安保、绿化养护及前台接待，"
                "合同期延长为两年。",
        "deadline": "2026-12-05 18:00",
        "attachments": [
            {"name": "物业服务需求书-v2.pdf", "url": "/files/H002-req-v2.pdf"},
            {"name": "答疑纪要.pdf", "url": "/files/H002-qa.pdf"},
        ],
    },
}

# JSON 来源数据：J101 与 H001 同单位同编号（跨来源重复），J102 独立
JSON_NOTICES: dict[str, dict] = {
    "J101": {
        "remote_id": "J101",
        "title": "市第一医院医疗设备采购公告（转载）",
        "purchaser": "市第一医院",
        "notice_no": "YYCG-2026-001",
        "region": "华东",
        "published_at": "2026-11-02",
        "url": "/json/detail/J101",
        "body": "市第一医院就CT机及配套设备进行公开招标，欢迎符合条件的供应商参加投标。"
                "报名需提供营业执照与医疗器械经营许可证。",
        "deadline": "2026-11-30 17:00",
        "attachments": [
            {"name": "招标参数.pdf", "url": "/files/J101-spec.pdf"},
            {"name": "报名须知.docx", "url": "/files/J101-guide.docx"},
        ],
    },
    "J102": {
        "remote_id": "J102",
        "title": "交通局信号灯升级采购公告",
        "purchaser": "市交通运输局",
        "notice_no": "JTCG-2026-102",
        "region": "华中",
        "published_at": "2026-11-07",
        "url": "/json/detail/J102",
        "body": "市交通运输局对主城区23个路口交通信号灯进行智能化升级，含施工与三年运维。",
        "deadline": "2026-12-10 17:00",
        "attachments": [
            {"name": "信号灯清单.xlsx", "url": "/files/J102-list.xlsx"},
        ],
    },
    "J103": {
        "remote_id": "J103",
        "title": "水利局泵站改造采购公告",
        "purchaser": "市水利局",
        "notice_no": "SLCG-2026-103",
        "region": "东北",
        "published_at": "2026-11-09",
        "url": "/json/detail/J103",
        "body": "市水利局对三座排涝泵站的水泵与电控系统进行改造，要求汛期前完工。",
        "deadline": "2026-12-15 17:00",
        "attachments": [
            {"name": "泵站图纸.zip", "url": "/files/J103-dwg.zip"},
        ],
    },
    "J104": {
        "remote_id": "J104",
        "title": "政务云扩容服务采购公告",
        "purchaser": "市大数据局",
        "notice_no": "DSCG-2026-104",
        "region": "华东",
        "published_at": "2026-11-10",
        "url": "/json/detail/J104",
        "body": "市大数据局采购政务云资源扩容与三年运维服务，含等保测评配合。",
        "deadline": "2026-12-20 17:00",
        "attachments": [],
    },
}

HTML_IDS = ["H001", "H002", "H003", "H004", "H005"]
JSON_IDS = ["J101", "J102", "J103", "J104"]
PAGE_SIZE = 2


# ---------------------------------------------------------------- 运行状态
@dataclass
class MockState:
    """线程间共享的故障注入状态。"""

    mode: str = "normal"            # normal | maintenance | login
    scenario: str = ""              # update_v1 | update_v2 | update_v3
    fail_after_html: int | None = None     # HTML 第 N 次列表请求起持续 503
    fail_after_json: int | None = None     # JSON 第 N 次列表请求起持续 503
    flaky_fail_remaining: int = 0          # 剩余瞬时 503 次数（之后自动恢复）
    html_list_request_count: int = 0       # HTML 逻辑列表请求计数（重试不增加）
    json_list_request_count: int = 0       # JSON 逻辑列表请求计数（重试不增加）
    request_count: int = 0
    slow_delay: float = 0.0

    def reset(self) -> None:
        self.mode = "normal"
        self.scenario = ""
        self.fail_after_html = None
        self.fail_after_json = None
        self.flaky_fail_remaining = 0
        self.html_list_request_count = 0
        self.json_list_request_count = 0
        self.request_count = 0
        self.slow_delay = 0.0


STATE = MockState()


def effective_notice(notice_id: str) -> dict:
    """返回某条公告当前应展示的内容（H002 受 scenario 控制）。"""
    base = dict(NOTICES[notice_id])
    if notice_id == "H002":
        # scenario 形如 update_v2，映射到 H002_VERSIONS 里的 v2 键
        version_key = STATE.scenario.replace("update_", "") if STATE.scenario else ""
        if version_key in H002_VERSIONS:
            base.update(H002_VERSIONS[version_key])
    return base


def html_page(page: int) -> list[dict]:
    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    ids = HTML_IDS[start:end]
    return [effective_notice(i) for i in ids]


def json_page(cursor: str | None) -> tuple[list[dict], str | None]:
    """游标分页。

    游标是不透明 token：cur-<index> 表示「从该下标开始返回」，
    调用方不应把它解析成公告 ID。返回 (本页数据, 下一个游标)。
    """
    start = 0
    if cursor and cursor.startswith("cur-"):
        try:
            start = int(cursor.split("-", 1)[1])
        except ValueError:
            start = 0
    page_items = [dict(JSON_NOTICES[i]) for i in JSON_IDS[start:start + PAGE_SIZE]]
    next_pos = start + PAGE_SIZE
    next_cursor = f"cur-{next_pos}" if next_pos < len(JSON_IDS) else None
    return page_items, next_cursor
