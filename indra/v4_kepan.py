# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lunar Society Technologies
"""
v4_kepan.py — 科判树（自带校验）。
注疏里"分三：初……二……三……"构成一棵树。解析规则只认一种结构：
    「分N」「分為N」「文分N」…… 后面接 N 个依次编号的条目（初/一、二、三…，后随 者、、，：）。
自检：说"分三"就必须恰好找到三个次第相连的条目；通不过的整棵不出（宁缺勿错）。
每个条目的疏文区间再递归解析；条目挂到经文上的办法见 attach()：取该区间内已定位段落的经文区间并集。
输入是带标点的原文（标点是结构线索），偏移是原文内的下标。
"""
import re

NUM = "一二三四五六七八九十"
HEAD = re.compile(r"(?:文|此|今|科|大科|次)?分(?:為|爲)?(?P<n>[二三四五六七八九十])(?:[：:，,]|者|\b|(?=[初一]))")
ITEM = re.compile(r"(?:^|(?<=[。；：，、」』]))(?P<k>初|一|二|三|四|五|六|七|八|九|十)(?:者|、|，|：|\.)?")


def _val(k):
    return 1 if k in "初一" else NUM.index(k) + 1


def parse(raw, base=0, depth=0, maxdepth=4):
    """返回节点列表 [{c0,c1,label,n,items:[{c0,c1,k,children:[…]}]}]；c0/c1 为 raw 内下标（加 base）。"""
    out = []
    for m in HEAD.finditer(raw):
        n = NUM.index(m.group("n")) + 1
        tail = raw[m.end():m.end() + 800]
        items, want = [], 1
        for it in ITEM.finditer(tail):
            if _val(it.group("k")) == want:
                items.append(it); want += 1
                if want > n:
                    break
        if len(items) != n:
            continue                                               # 自检不过：不出
        spans = []
        for q, it in enumerate(items):
            a = m.end() + it.start()
            b = m.end() + (items[q + 1].start() if q + 1 < n else _end(tail, items[q].end(), n))
            spans.append((a, b, it.group("k")))
        node = {"c0": base + m.start(), "c1": base + spans[-1][1], "n": n, "items": []}
        for a, b, k in spans:
            ch = parse(raw[a:b][2:], base + a + 2, depth + 1, maxdepth) if depth < maxdepth else []
            node["items"].append({"c0": base + a, "c1": base + b, "k": k, "children": ch})
        out.append(node)
    return _outer(out)


def _end(tail, start, n):
    """最后一个条目的止点：下一个同级"分"字起句，或再走 120 字。"""
    m = HEAD.search(tail, start)
    return min(m.start() if m else len(tail), start + 120) if True else len(tail)


def _outer(nodes):
    """去掉被别的节点完全包含的节点（它们已作为 children 出现）。"""
    keep = []
    for a in nodes:
        if not any(b is not a and b["c0"] <= a["c0"] and a["c1"] <= b["c1"] for b in nodes):
            keep.append(a)
    return keep


def attach(tree, segs, norm_index):
    """给每个条目挂经文区间。segs 的 c0/c1 是 norm 后下标，norm_index 把 raw 下标映到 norm 下标（见 v4_grammar.raw_to_norm）。
    返回同形的树，条目多一个 s=(s0,s1) 或 None。"""
    def one(it):
        a, b = norm_index[min(it["c0"], len(norm_index) - 1)], norm_index[min(it["c1"], len(norm_index) - 1)]
        hit = [s for s in segs if s["c0"] < b and a < s["c1"]]
        it["s"] = (min(s["s0"] for s in hit), max(s["s1"] for s in hit)) if hit else None
        for n in it["children"]:
            for i2 in n["items"]:
                one(i2)
    for n in tree:
        for it in n["items"]:
            one(it)
    return tree


if __name__ == "__main__":
    t = "此經分三：初序分，二正宗分，分二：初明人空，二明法空，三流通分。"
    tr = parse(t)
    assert len(tr) == 1 and tr[0]["n"] == 3, tr
    assert [i["k"] for i in tr[0]["items"]] == ["初", "二", "三"]
    assert tr[0]["items"][1]["children"] and tr[0]["items"][1]["children"][0]["n"] == 2, tr
    assert parse("此經分三：初序分，二正宗分。") == [], "说分三却只有两项：必须不出"
    print("OK")
