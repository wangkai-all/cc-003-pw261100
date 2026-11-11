"""极简 HTML DOM 与 CSS 选择器的子集实现，仅依赖标准库 html.parser。

支持的选择器语法（够用即可，不追求完整 CSS）：
- 标签名：      li
- id：          div#content
- class：       span.unit / a.external
- 后代组合：    ul.notice-list li
- 属性取值：    a@href（在字段配置中使用，不是标准 CSS）

节点提供 text()、attr()、select()、select_one()。
"""
from __future__ import annotations

from html.parser import HTMLParser

# 这些标签没有闭合标签，解析时不要等待它们的 end tag
_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent", "_text_parts")

    def __init__(self, tag: str, attrs: dict[str, str], parent: "Node | None" = None):
        self.tag = tag
        self.attrs = attrs
        self.children: list[Node | str] = []
        self.parent = parent
        self._text_parts: list[str] | None = None

    def append_text(self, text: str) -> None:
        self.children.append(text)

    # ---- 基本取数 ----
    def text(self) -> str:
        """递归拼接所有后代文本，并做空白归一化。"""
        parts: list[str] = []

        def walk(node: Node) -> None:
            for child in node.children:
                if isinstance(child, str):
                    parts.append(child)
                else:
                    walk(child)

        walk(self)
        return _normalize_space("".join(parts))

    def raw_html(self) -> str:
        """递归重建内部 HTML（用于保存 HTML 来源正文原文）。"""
        parts: list[str] = []

        def walk(node: Node) -> None:
            for child in node.children:
                if isinstance(child, str):
                    parts.append(child)
                else:
                    attr_str = "".join(
                        f' {k}="{v}"' for k, v in child.attrs.items()
                    )
                    parts.append(f"<{child.tag}{attr_str}>")
                    walk(child)
                    if child.tag not in _VOID_TAGS:
                        parts.append(f"</{child.tag}>")

        walk(self)
        return "".join(parts).strip()

    def attr(self, name: str) -> str | None:
        return self.attrs.get(name)

    # ---- 选择器 ----
    def select(self, selector: str) -> list["Node"]:
        groups = [_parse_selector(part) for part in selector.split(",")]
        results: list[Node] = []
        seen: set[int] = set()
        for group in groups:
            for node in self._select_group(group):
                if id(node) not in seen:
                    seen.add(id(node))
                    results.append(node)
        return results

    def select_one(self, selector: str) -> "Node | None":
        nodes = self.select(selector)
        return nodes[0] if nodes else None

    def _select_group(self, steps: list[dict]) -> list[Node]:
        current = [self]
        for step in steps:
            nxt: list[Node] = []
            for node in current:
                nxt.extend(node._descendants_matching(step))
            current = nxt
        return current

    def _descendants_matching(self, step: dict) -> list[Node]:
        matched: list[Node] = []

        def walk(node: Node) -> None:
            for child in node.children:
                if isinstance(child, str):
                    continue
                if _matches(child, step):
                    matched.append(child)
                walk(child)

        walk(self)
        return matched


def _normalize_space(text: str) -> str:
    return " ".join(text.split())


def _matches(node: Node, step: dict) -> bool:
    if step["tag"] and node.tag != step["tag"]:
        return False
    if step["id"] is not None and node.attrs.get("id") != step["id"]:
        return False
    if step["classes"]:
        node_classes = set(node.attrs.get("class", "").split())
        if not set(step["classes"]).issubset(node_classes):
            return False
    return True


def _parse_selector(selector: str) -> list[dict]:
    """把 'ul.notice-list li' 解析成 [{tag:'ul',classes:['notice-list']}, {tag:'li'}]。"""
    steps: list[dict] = []
    for token in selector.split():
        tag = ""
        elem_id = None
        classes: list[str] = []
        buf = ""
        mode = "tag"
        for ch in token:
            if ch == "#":
                if buf:
                    if mode == "class":
                        classes.append(buf)
                    else:
                        tag = buf
                buf = ""
                mode = "id"
            elif ch == ".":
                if buf:
                    if mode == "class":
                        classes.append(buf)
                    else:
                        tag = buf
                buf = ""
                mode = "class"
            else:
                buf += ch
        if buf:
            if mode == "id":
                elem_id = buf
            elif mode == "class":
                classes.append(buf)
            else:
                tag = buf
        steps.append({"tag": tag, "id": elem_id, "classes": classes})
    return steps


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {})
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = {k: (v if v is not None else "") for k, v in attrs}
        node = Node(tag, attr_dict, parent=self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in _VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = {k: (v if v is not None else "") for k, v in attrs}
        self.stack[-1].children.append(Node(tag, attr_dict, parent=self.stack[-1]))

    def handle_endtag(self, tag: str) -> None:
        # 容错：向上找到最近的同名未闭合标签
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].append_text(data)


def parse_html(html: str) -> Node:
    """解析 HTML 字符串，返回虚拟根节点。"""
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


def extract_field(node: Node, spec: str) -> str | None:
    """按字段配置取值。

    spec 形式：
    - "a"        -> 匹配节点的文本
    - "a@href"   -> 匹配节点的 href 属性
    - "@href"    -> 当前节点自身的属性（空选择器）
    """
    if "@" in spec:
        selector, attr_name = spec.rsplit("@", 1)
    else:
        selector, attr_name = spec, None
    if selector:
        target = node.select_one(selector)
        if target is None:
            return None
    else:
        target = node
    if attr_name is not None:
        return target.attr(attr_name)
    return target.text()
