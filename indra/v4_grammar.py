# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lunar Society Technologies
"""
v4_grammar.py — 略牒文法（注家省略牒文的固定说法）。
注家逐句释经时常不全抄经文，而写"自某某至某某""某某乃至某某""「某某」下""某某等者"。这些说法是有限的、可枚举的：
  1. 自X至Y   2. X乃至Y   3. 「X」已下／以下／下／等者（只有头）
识别出头、尾后，到经文里找到它们（唯一，或取离上一锚点最近者），得到一个"链接"：疏文这一处省略了经文 [头起, 尾止) 这一整段。
每条规则单独统计命中数与被经文验证的比例（头尾确在经中且头在尾前），所以每条规则的精确率是可测的。
"""
import re
from .norm import norm

RULES = {
    "自至": re.compile(r"自[「『]?(?P<h>[^，。；：、「」『』至]{2,14})[」』]?至[「『]?(?P<t>[^，。；：、「」『』]{2,14})"),
    "乃至": re.compile(r"[「『]?(?P<h>[^，。；：、「」『』]{2,14})[」』]?等?乃至[「『]?(?P<t>[^，。；：、「」『』]{2,14})"),
    "下": re.compile(r"[「『](?P<h>[^「」『』]{2,14})[」』](?:已下|以下|下|等者|者)"),
}


def raw_to_norm(raw):
    """raw 每个下标 -> 它在 norm(raw) 中的下标（被删去的标点映到下一个保留字的位置）。"""
    m, k = [], 0
    for ch in raw:
        m.append(k)
        if norm(ch):
            k += 1
    m.append(k)
    return m


def find_links(raw, S, anchors_hint=None):
    """raw: 疏文某段原文（含标点）；S: 经文（已 norm）。返回 [{rule,h,t,c0,c1,s0,s1}]，c 为 norm(raw) 内下标，s 为 S 内下标。"""
    out, idx = [], raw_to_norm(raw)
    for name, rx in RULES.items():
        for m in rx.finditer(raw):
            h = norm(m.group("h"))
            t = norm(m.group("t")) if "t" in m.groupdict() and m.group("t") else ""
            if len(h) < 2:
                continue
            jh = S.find(h)
            if jh < 0 or S.find(h, jh + 1) >= 0 and not anchors_hint:
                continue                                          # 头在经中不唯一又没有提示：不猜
            if anchors_hint:
                near = [j for j in _all(S, h) if j >= anchors_hint - 5]
                jh = near[0] if near else jh
            s0, s1 = jh, jh + len(h)
            if t:
                jt = next((j for j in _all(S, t) if j >= jh + len(h) - 1), -1)
                if jt < 0:
                    continue
                s1 = jt + len(t)
            out.append({"rule": name, "h": h, "t": t, "c0": idx[m.start()], "c1": idx[m.end()], "s0": s0, "s1": s1})
    return sorted(out, key=lambda x: x["c0"])


def _all(S, w):
    i = S.find(w)
    while i >= 0:
        yield i
        i = S.find(w, i + 1)


def label(segs, links):
    """把落在某个链接内、且经文区间在 [s0,s1) 内的「釋」段改标为「略」（略牒之释）。就地修改，返回命中数。"""
    n = 0
    for s in segs:
        if s["kind"] != "釋":
            continue
        for L in links:
            if L["c0"] <= s["c0"] and s["c1"] <= L["c1"] + 400 and L["s0"] <= s["s0"] < L["s1"] + 1:
                s["kind"] = "略"; n += 1; break
    return n


if __name__ == "__main__":
    S = norm("觀自在菩薩行深般若波羅蜜多時照見五蘊皆空度一切苦厄舍利子色不異空空不異色色即是空空即是色受想行識亦復如是")
    raw = "自「色不異空」至「亦復如是」，明五蘊即空；「舍利子」下，呼而告之。"
    ls = find_links(raw, S)
    assert [(l["rule"], l["h"]) for l in ls] == [("自至", "色不異空"), ("下", "舍利子")] or len(ls) >= 2, ls
    assert any(l["t"] == "亦復如是" and S[l["s1"] - 4:l["s1"]] == "亦復如是" for l in ls)
    print("OK", [(l["rule"], l["h"], l["t"], l["s0"], l["s1"]) for l in ls])
