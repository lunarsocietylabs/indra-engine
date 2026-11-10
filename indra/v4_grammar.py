# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lunar Society Technologies
"""
v4_grammar.py — 略牒文法：注家不全抄经文时的固定说法。

句式取自真实注疏（法藏《心經略疏》、智旭《要解》及其鈔等）：
  起止   從「如是」訖列眾 ／ 自「觀自在菩薩」至「度一切苦厄」 ／ 「如是我聞」下乃至「大眾俱」 ／ 自五蘊至十二因緣
  到底   從「舍利弗如我今者」訖經
  只有头 「舍利子」下 ／ 「爾時佛告」已下 ／ 「照見五蘊」等
规则是有限的、逐条可数的。每条链接都回到经文里核对：头（和尾）必须确实在经里，头在尾前；
头在经里出现多次时，取"当前讲到的位置"之后最近的一处（当前位置由 v4_locate 的確层锚点给出）；没有当前位置又不唯一 → 不猜。
"X乃至Y"若整句本来就是经文原话（如"無眼界乃至無意識界"），那是引文，不是省略。
"""
import bisect, re

from .norm import norm

_Q = r"[「『](?P<%s>[^「」『』]{2,40})[」』]"
_B = r"(?P<%s>[^，。；：、！？「」『』\s]{2,12}?)"
_TO = r"(?:乃至|下至|至|訖|盡|迄)"
_DOWN = r"(?:已下|以下|下)"
RULES = [
    ("到底", re.compile(r"(?:自|從)?" + _Q % "h" + _DOWN + r"?(?:訖|至|盡)(?:經末|經終|文末|卷末|經)(?![「『])")),
    ("起止", re.compile(r"(?:自|從)?" + _Q % "h" + _DOWN + r"?[，、]?" + _TO + _Q % "t")),
    ("起止", re.compile(r"(?:自|從)" + _B % "h" + _DOWN + r"?" + _TO + _B % "t" + r"(?=[，。；：、！？]|已來|以來|為|是|名)")),
    ("頭", re.compile(_Q % "h" + r"(?:已下|以下|下|等者|等)")),
]
END = "END"


def raw_to_norm(raw):
    """raw 每个下标 -> 它在 norm(raw) 中的下标（被删去的字符映到下一个保留字的位置）；逐字规范化与整体不一致时返回 None。"""
    m, k = [], 0
    for ch in raw:
        m.append(k)
        k += len(norm(ch))
    m.append(k)
    return m if k == len(norm(raw)) else None


def norm_to_raw(idx):
    """raw_to_norm 的逆：norm 下标 -> raw 下标。"""
    out = [0] * (idx[-1] + 1)
    for r in range(len(idx) - 1, -1, -1):
        out[idx[r]] = r
    return out


def occurrences(S, w):
    out, i = [], S.find(w)
    while i >= 0:
        out.append(i); i = S.find(w, i + 1)
    return out


def resolve(S, w, expect=None, slack=60, quoted=True, min_prefix=4):
    """在经里找 w。返回 (位置, 实际匹配长度) 或 None。整句找不到时，允许取它在经里出现的最长前缀（注家常只举头几个字）。"""
    w = norm(w)
    for cut in range(len(w), (min_prefix if quoted else 3) - 1, -1):
        occ = occurrences(S, w[:cut])
        if not occ:
            if cut == len(w) and len(w) < (min_prefix if quoted else 3):
                return None
            continue
        if expect is None:
            return (occ[0], cut) if len(occ) == 1 else None
        fwd = [j for j in occ if j >= expect - slack]
        return (fwd[0] if fwd else occ[-1], cut)
    if len(w) >= 2 and quoted:
        occ = occurrences(S, w)
        if occ:
            if expect is None:
                return (occ[0], len(w)) if len(occ) == 1 else None
            fwd = [j for j in occ if j >= expect - slack]
            return (fwd[0] if fwd else occ[-1], len(w))
    return None


def expecter(segs):
    """由確层引文锚点得到"疏文位置 → 当前讲到经文何处"。"""
    pts = sorted((s["c0"], s["s0"]) for s in segs if s["kind"] == "引" and s["tier"] == "確" and not s.get("weak"))
    cs = [p[0] for p in pts]

    def expect(c):
        k = bisect.bisect_right(cs, c) - 1
        return pts[k][1] if k >= 0 else None
    return expect


def find_links(raw, S, expect=None, idx=None):
    """raw: 疏文原文（含标点）；S: 经文（已 norm）；expect: 见 expecter。
    返回 [{rule,h,t,c0,c1,s0,s1}]（c 为 norm(raw) 内下标；s1=None 表示只有头）。"""
    idx = idx or raw_to_norm(raw)
    if idx is None:
        return []
    out, seen = [], []
    for name, rx in RULES:
        for m in rx.finditer(raw):
            if any(a < m.end() and m.start() < b for a, b in seen):
                continue                                          # 已被更具体的规则认走
            quoted = raw[m.start("h") - 1] in "「『"
            e = expect(idx[m.start()]) if expect else None
            h = resolve(S, m.group("h"), e, quoted=quoted)
            if not h:
                continue
            link = {"rule": name, "h": norm(m.group("h")), "t": "", "c0": idx[m.start()], "c1": idx[m.end()],
                    "s0": h[0], "s1": None}
            if name == "到底":
                link["t"], link["s1"] = END, len(S)
            elif name == "起止":
                tn = norm(m.group("t"))
                if norm(m.group("h") + "乃至" + m.group("t")) in S or norm(m.group("h") + "至" + m.group("t")) in S:
                    continue                                      # 经文原话
                t = resolve(S, tn, h[0] + h[1], slack=0, quoted=raw[m.start("t") - 1] in "「『")
                if not t or t[0] < h[0]:
                    if not quoted:
                        continue
                    link["rule"] = "頭"                            # 尾是概括语（"訖列眾"），不是经文：只认头
                else:
                    link["t"], link["s1"] = tn, t[0] + t[1]
            seen.append((m.start(), m.end()))
            out.append(link)
    return sorted(out, key=lambda x: x["c0"])


def apply_links(segs, links, bounds, span=1500):
    """把"起止／到底"链接落实到归属上：链接之后、下一处落在该区间之外的確层引文之前的释文，改记为「略」，经文区间取链接所说的整段。
    区间按句界取整。原归属与链接区间有交叠 → 保留原层级；否则降为候。返回改动的段数。"""
    starts = [b[0] for b in bounds]

    def snap(s0, s1):
        a = max(0, bisect.bisect_right(starts, s0) - 1)
        b = max(a, min(len(bounds) - 1, bisect.bisect_left(starts, s1) - 1))
        return bounds[a][0], bounds[b][1]

    n = 0
    order = sorted(range(len(segs)), key=lambda k: segs[k]["c0"])
    cs = [segs[k]["c0"] for k in order]
    for L in links:
        if L["s1"] is None:
            continue
        s0, s1 = snap(L["s0"], L["s1"])
        z = bisect.bisect_left(cs, L["c0"])
        while z < len(order):
            s = segs[order[z]]; z += 1
            if s["c0"] > L["c1"] + span:
                break
            if s["kind"] == "引" and s["tier"] == "確" and not s.get("weak") and s["c0"] >= L["c1"] \
                    and not (s0 <= s["s0"] < s1):
                break
            if s["kind"] != "釋":
                continue
            overlap = s["s0"] < s1 and s0 < s["s1"]
            s.update({"kind": "略", "s0": s0, "s1": s1, "tier": s["tier"] if overlap else "候", "rule": L["rule"]})
            s.pop("amb", None)
            n += 1
    return n


if __name__ == "__main__":
    S = norm("觀自在菩薩，行深般若波羅蜜多時，照見五蘊皆空，度一切苦厄。舍利子！色不異空，空不異色；色即是空，空即是色。"
             "受、想、行、識，亦復如是。舍利子！是諸法空相，不生不滅，不垢不淨，不增不減。是故空中無色，無受、想、行、識；"
             "無眼界，乃至無意識界；無無明，亦無無明盡；以無所得故。菩提薩埵，依般若波羅蜜多故，心無罣礙。故說般若波羅蜜多咒。")
    raw = ("自「觀自在菩薩」至「度一切苦厄」句，一卷之綱。從「舍利子！色不異空」至「亦復如是」是正宗。"
           "二、「行深般若」下明習應之慧；「無眼界，乃至無意識界」者，十八界也。自以無所得至心無罣礙，此一節開演。"
           "從「故說般若」訖經為流通。自性本空，至人無己。")
    ls = find_links(raw, S)
    got = [(l["rule"], l["h"], l["t"]) for l in ls]
    assert ("起止", "觀自在菩薩", "度一切苦厄") in got, got
    assert ("起止", "舍利子色不異空", "亦復如是") in got, got
    assert ("頭", "行深般若", "") in got, got
    assert ("起止", "以無所得", "心無罣礙") in got, got
    assert ("到底", "故說般若", END) in got and ls[-1]["s1"] == len(S), got
    assert not any(l["h"].startswith("無眼界") for l in ls), "经文原话里的乃至不是省略"
    assert not any(l["h"].startswith("性本空") for l in ls), "自性…至人…不是略牒"
    for l in ls:
        assert l["s1"] is None or l["s0"] < l["s1"]
    print("OK", got)
