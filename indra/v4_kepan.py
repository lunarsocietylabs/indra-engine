# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lunar Society Technologies
"""
v4_kepan.py — 科判树（自带校验，直接挂到经文上）。

真实注疏里的科判长这样（法藏《心經略疏》、《彌陀經疏》等）：
  略中有四：一、明造修之者；二、「行深般若」下明習應之慧；三、「照見五蘊」下明契證之境；四、「度一切苦厄」下明理圓剋果。
  大分三分：初至大眾俱為序分；二、爾時佛告下，至是為甚難，為正宗分；三、佛說此經下……
  分二：初列名、二總結。        有三：初總、次別、後結。
要点：条目里往往自带"「某某」下"——它直接说出这一科从经文哪里开始。所以科判不必靠猜就能挂到经文上：
  第 k 科的经文区间 = [第 k 科的头, 第 k+1 科的头)。
自检（通不过的整棵不出）：
  1. 说"分 N"，就必须恰好找到 N 个次第相连的条目（初/一、二…；初、次、後；第一、第二…）；
  2. 各科的头在经文里必须确实存在，而且一科比一科靠后。
条目不带经文头的，是两种情况之一：后文再"牒科"展开（初列名者……二總結者……）→ 按后文出现的位置挂；
或者只是法数（"住有八""緣起有四義"）→ 标为「數」，不挂经文。
输入是带标点的原文；c0/c1 是 norm 后的下标（与 v4_locate 的坐标一致）。
"""
import bisect, re

from .norm import norm
from . import v4_grammar as G

NUM = "一二三四五六七八九十"
HEAD = re.compile(r"(?:分為|分爲|分成|大分|總有|略有|文有|中有|分|有|為|爲|開|科)(?:為|爲)?"
                  r"(?P<n>[二三四五六七八九十兩])(?P<u>[段分門科章節種類對義意重番位句事法])?[：:；，。]\s*"
                  r"(?=初|一|先|第一)")
ITEM = re.compile(r"(?:第(?P<o>[一二三四五六七八九十])|(?P<d>[一二三四五六七八九十])者?|(?P<w>初|次|後|先|末))[、，：]?")
DELIM = "：:；，。、 　"
_QH = re.compile(r"(?:從|自)?[「『](?P<h>[^「」『』]{2,40})[」』](?:已下|以下|下|等)?"
                 r"(?:[，、]?(?:乃至|至|訖)[「『](?P<t>[^「」『』]{2,40})[」』])?")
_BH = re.compile(r"(?:從|自)?(?P<h>[^，。；：、「」『』]{2,14}?)(?:已下|以下|下)(?:[，、]?(?:乃至|至|訖)(?P<t>[^，。；：、「」『』]{2,14}?)(?=[，。；、為名是]))?")
_TO = re.compile(r"至(?P<t>[^，。；：、「」『』]{2,14}?)(?=[，。；、為名是])")


def _n(ch):
    return 2 if ch == "兩" else NUM.index(ch) + 1


def _items(raw, start, n, reach=400):
    """从 start 起找 N 个次第相连的条目标记。返回 [(标记起, 正文起, 正文止)]，找不齐返回 None。"""
    marks, want, pos, last = [], 1, start, start
    while want <= n:
        m = ITEM.search(raw, pos)
        if not m or m.start() - last > reach:
            return None
        pos = m.end()
        if m.start() > start and raw[m.start() - 1] not in DELIM:
            pos = m.start() + 1
            continue                                              # 不在句读之后：是正文里的字（"一切""後世"）
        if m.group("w"):
            val = {"初": 1, "先": 1}.get(m.group("w"))
            if val is None:
                val = want if (m.group("w") == "次" and 1 < want < n) or (m.group("w") in "後末" and want == n) \
                    or (m.group("w") == "次" and want == n == 2) else -1
        else:
            val = NUM.index(m.group("o") or m.group("d")) + 1
        if val == 1 and want == 2 and m.start() - marks[0][1] <= 1:
            marks[0] = (m.start(), m.end())                       # "初，一、明……"：两个标记指同一条
            last = m.start()
            continue
        if val != want:
            if val == 1 and want > 1:
                return None                                       # 又从头数起：不是同一个列举
            continue
        marks.append((m.start(), m.end())); want += 1; last = m.start()
    out = []
    for k, (a, b) in enumerate(marks):
        if k + 1 < n:
            e = marks[k + 1][0]
        else:
            stop = [raw.find(ch, b) for ch in "。；"]
            stop = [x for x in stop if x >= 0]
            e = min(min(stop) if stop else len(raw), b + 160)
        if e - b < 1:
            return None
        out.append((a, b, e))
    return out


def parse(raw, S=None, segs=None, idx=None):
    """返回节点列表（已嵌套）。节点 = {c0,c1,n,u,type,items:[{k,label,c0,c1,s,head,children}]}。
    type：「科」= 条目挂上了经文且通过自检；「數」= 只是法数，不挂。S、segs 不给则只做结构解析（全部记「數」）。"""
    idx = idx or G.raw_to_norm(raw)
    if idx is None:
        return []
    expect = G.expecter(segs) if segs else None
    Cn = norm(raw) if segs else ""
    nodes = []
    for m in HEAD.finditer(raw):
        n = _n(m.group("n"))
        its = _items(raw, m.end(), n)
        if not its:
            continue
        node = {"c0": idx[m.start()], "c1": idx[its[-1][2]], "n": n, "u": m.group("u") or "", "type": "數", "items": []}
        e = expect(idx[m.start()]) if expect else None
        for k, (a, b, z) in enumerate(its):
            txt = raw[b:z]
            it = {"k": k + 1, "label": txt.strip(" 　，、。；")[:40], "c0": idx[a], "c1": idx[z], "s": None, "head": "",
                  "children": []}
            if S:
                q = _QH.match(txt) or _BH.match(txt)
                if q:
                    quoted = txt[q.start("h") - 1] in "「『" if q.start("h") > 0 else False
                    h = G.resolve(S, q.group("h"), e, quoted=quoted)
                    if h:
                        it["head"], it["_s0"] = norm(q.group("h"))[:h[1]], h[0]
                        e = h[0]
                        if q.group("t"):
                            t = G.resolve(S, q.group("t"), h[0] + h[1], slack=0, quoted=quoted)
                            if t and t[0] >= h[0]:
                                it["_s1"] = t[0] + t[1]
                                e = it["_s1"]
                    it["label"] = txt[q.end():].strip(" 　，、。；")[:40] or it["label"]
                else:
                    q = _TO.match(txt)
                    if q:
                        t = G.resolve(S, q.group("t"), e, quoted=False)
                        if t:
                            it["_s1"] = t[0] + t[1]
                            if k == 0:
                                it["_s0"] = e if e is not None else 0
                            e = it["_s1"]
            node["items"].append(it)
        if S:
            _ranges(node, S, expect(idx[m.start()]) if expect else None)
            if node["type"] == "數" and segs:
                _resume(node, Cn, segs)
        for it in node["items"]:
            it.pop("_s0", None); it.pop("_s1", None)
        nodes.append(node)
    return nest(nodes)


def _ranges(node, S, e):
    """由各科的头定区间并自检：头必须一科比一科靠后；至少有一半的科带头（两科的，至少一科）。"""
    its = node["items"]
    heads = [it.get("_s0") for it in its]
    have = [h for h in heads if h is not None]
    if len(have) < max(1, len(its) // 2) or any(a >= b for a, b in zip(have, have[1:])):
        return
    if heads[0] is None:
        if e is None or e > have[0]:
            if len(have) < len(its) - 1:
                return
            heads[0] = None
        else:
            heads[0] = e
    for k, it in enumerate(its):
        s0 = heads[k]
        if s0 is None:
            continue
        nxt = next((h for h in heads[k + 1:] if h is not None), None)
        s1 = it.get("_s1") or nxt
        if s1 is None or s1 <= s0:
            s1 = it.get("_s1")                                    # 最后一科：没有明文止点就留空，由上一级的区间收口
        it["s"] = [s0, s1]
    node["type"] = "科"


def _resume(node, Cn, segs, far=60000):
    """条目不带经文头：到后文找"牒科"（标记＋科名头几个字），按它出现的位置取疏文跨度，再由跨度内的引文锚点得经文区间。"""
    its, pos, spans = node["items"], node["c1"], []
    for it in its:
        lab = norm(it["label"])[:4]
        if len(lab) < 2:
            return
        k = it["k"]
        marks = [NUM[k - 1], "第" + NUM[k - 1]] + (["初", "先"] if k == 1 else ["次"] if k < len(its) else ["次", "後", "末"])
        hit = -1
        for mk in marks:
            p = Cn.find(mk + lab, pos, pos + far)
            if p >= 0 and (hit < 0 or p < hit):
                hit = p
        if hit < 0:
            return
        spans.append(hit); pos = hit + 1
    spans.append(None)
    quotes = sorted((s["c0"], s["s0"], s["s1"]) for s in segs if s["kind"] == "引" and not s.get("weak"))
    cs = [q[0] for q in quotes]
    ok = 0
    for k, it in enumerate(its):
        a = spans[k]
        b = spans[k + 1] if spans[k + 1] is not None else min(len(Cn), a + 8000)
        inside = quotes[bisect.bisect_left(cs, a):bisect.bisect_left(cs, b)]
        it["c_span"] = [a, b]
        if inside:
            it["s"] = [min(q[1] for q in inside), max(q[2] for q in inside)]
            ok += 1
    if ok >= max(1, len(its) // 2):
        node["type"] = "科"; node["resumed"] = 1
    else:
        for it in its:
            it["s"] = None


def nest(nodes):
    """把后出的节点挂到包含它的最近一科之下：按疏文跨度（牒科展开的），或按经文区间（带头的）。"""
    nodes = sorted(nodes, key=lambda x: x["c0"])
    roots = []
    for q, b in enumerate(nodes):
        bs = [it["s"] for it in b["items"] if it["s"]]
        b0 = min(s[0] for s in bs) if bs else None
        home = None
        for a in reversed(nodes[:q]):
            for it in a["items"]:
                span = it.get("c_span")
                if span and span[0] <= b["c0"] < span[1]:
                    home = it; break
                if a["c0"] <= b["c0"] < a["c1"] and it["c0"] <= b["c0"] < it["c1"]:
                    home = it; break
                if it["s"] and b0 is not None and b["type"] == "科" and a["type"] == "科" and not a.get("resumed") \
                        and it["s"][0] <= b0 and (it["s"][1] is None or b0 < it["s"][1]) and b["c0"] - a["c1"] < 20000:
                    home = it; break
            if home:
                break
        (home["children"] if home else roots).append(b)
    return roots


def count(tree):
    """(节点数, 其中挂上经文的「科」数)。"""
    n = k = 0
    for nd in tree:
        n += 1; k += nd["type"] == "科"
        for it in nd["items"]:
            a, b = count(it["children"]); n += a; k += b
    return n, k


if __name__ == "__main__":
    S = norm("如是我聞一時佛在舍衛國祇樹給孤獨園與大比丘僧千二百五十人俱及釋提桓因等無量諸天大眾俱"
             "爾時佛告長老舍利弗從是西方過十萬億佛土有世界名曰極樂是為甚難"
             "佛說此經已舍利弗及諸比丘一切世間天人阿修羅等聞佛所說歡喜信受作禮而去")
    a = parse("次釋經文，大分三分：初至大眾俱為序分；二、爾時佛告下，至是為甚難，為正宗分；三、佛說此經下，為流通分。", S)
    assert len(a) == 1 and a[0]["type"] == "科" and a[0]["n"] == 3, a
    r = [it["s"] for it in a[0]["items"]]
    assert r[1][0] == S.index("爾時佛告") and r[1][1] == S.index("是為甚難") + 4 and r[2][0] == S.index("佛說此經"), r
    H = norm("觀自在菩薩行深般若波羅蜜多時照見五蘊皆空度一切苦厄舍利子色不異空空不異色")
    b = parse("略中有四：初，一、明造修之者；二、「行深般若」下明習應之慧；三、「照見五蘊」下明契證之境；四、「度一切苦厄」下明理圓剋果。", H)
    assert b and b[0]["type"] == "科" and [it["head"] for it in b[0]["items"]] == ["", "行深般若", "照見五蘊", "度一切苦厄"], b
    assert b[0]["items"][1]["s"] == [H.index("行深般若"), H.index("照見五蘊")]
    c = parse("有三：初總、次別、後結。", H)
    assert c and c[0]["type"] == "數" and c[0]["n"] == 3
    assert parse("此經分三：初序分，二正宗分。", H) == [], "说分三却只有两项：必须不出"
    d = parse("文分為二：初、「照見五蘊」下明境；二、「行深般若」下明慧。", H)
    assert d and d[0]["type"] == "數", "头的次序与经文相反：不得挂经文"
    e = parse("住有八，今略有二：一境界住、二依止住。一切眾生皆有三世、內外。", H)
    assert e and e[0]["n"] == 2 and e[0]["type"] == "數", e
    print("OK")
