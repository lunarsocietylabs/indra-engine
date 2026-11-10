# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lunar Society Technologies
"""
v4_layers.py — 层层相注：经 ← 疏 ← 钞 ← 记。

钞是对疏的注。把钞用同一套 v4_locate 比对到**疏**的坐标上（疏当"经"用），疏已在经的坐标上；两次映射一乘，钞就落在经上。
哪部是哪部的钞，不凭书名定：书名只用来提候选（《略疏連珠記》含《略疏》），是不是，由比对结果说了算（见 confirm）。

compose(b_to_a, a_to_s)：
  b_to_a 每段：钞的区间 (c0,c1) → 疏的区间 (s0,s1)；a_to_s 每段：疏的区间 (c0,c1) → 经的区间 (s0,s1)。
  结果每段仍带钞的区间；经文区间取疏的那个区间所压到的各段经文区间的并；层级取两层中较低的；kind 记「複」。
  疏的那个区间若压到跨度很大的经文（> max_span 字），说明对不准，丢弃。
"""
import bisect, re

SURE, CAND = "確", "候"


def compose(b_to_a, a_to_s, max_span=600):
    segs = sorted((s for s in a_to_s if s["kind"] != "參"), key=lambda s: s["c0"])
    starts = [s["c0"] for s in segs]
    out = []
    for g in b_to_a:
        if g["kind"] == "參":
            continue
        lo, hi = g["s0"], g["s1"]
        q = max(0, bisect.bisect_right(starts, lo) - 1)
        s0 = s1 = None
        tier = g["tier"]
        while q < len(segs) and segs[q]["c0"] < hi:
            h = segs[q]
            if h["c1"] > lo:
                s0 = h["s0"] if s0 is None else min(s0, h["s0"])
                s1 = h["s1"] if s1 is None else max(s1, h["s1"])
                if h["tier"] != SURE:
                    tier = CAND
            q += 1
        if s0 is None or s1 - s0 > max_span:
            continue
        out.append({"c0": g["c0"], "c1": g["c1"], "s0": s0, "s1": s1, "kind": "複", "tier": tier,
                    "via": [lo, hi], "depth": g.get("depth", 1) + 1})
    return out


def _core(title):
    t = re.sub(r"[（(].*?[)）]", "", title or "")
    return re.sub(r"(卷[上中下一二三四五六七八九十]+|并序|序)$", "", t).strip()


def candidates(docs):
    """按书名提"谁可能是谁的再注"：B 的书名以 A 的书名开头且更长（《…略疏》→《…略疏連珠記》）。
    docs: [{id,title}]。返回 [(base_id, sub_id)]；只是候选，须经 confirm。"""
    core = [(d["id"], _core(d["title"])) for d in docs]
    out = []
    for a, ta in core:
        if len(ta) < 4:
            continue
        for b, tb in core:
            if a != b and len(tb) > len(ta) and tb.startswith(ta):
                out.append((a, b))
    return sorted(set(out))


def confirm(stats, min_cover=0.3, min_order=0.5):
    """用比对结果确认"B 是 A 的再注"：A（当经用）的句子有相当一部分被 B 按次序牒到。stats 来自 v4_locate。"""
    hit = stats["clauses_hit"] / max(1, stats["clauses"])
    return hit >= min_cover and stats["order"] >= min_order


if __name__ == "__main__":
    a_to_s = [{"c0": 0, "c1": 20, "s0": 100, "s1": 110, "kind": "引", "tier": "確"},
              {"c0": 20, "c1": 60, "s0": 100, "s1": 130, "kind": "釋", "tier": "候"},
              {"c0": 60, "c1": 90, "s0": 5000, "s1": 5010, "kind": "參", "tier": "確"}]
    b_to_a = [{"c0": 5, "c1": 30, "s0": 10, "s1": 40, "kind": "引", "tier": "確"},
              {"c0": 30, "c1": 50, "s0": 0, "s1": 15, "kind": "釋", "tier": "確"},
              {"c0": 50, "c1": 70, "s0": 62, "s1": 80, "kind": "引", "tier": "確"}]
    r = compose(b_to_a, a_to_s)
    assert r[0] == {"c0": 5, "c1": 30, "s0": 100, "s1": 130, "kind": "複", "tier": "候", "via": [10, 40], "depth": 2}, r
    assert r[1]["tier"] == "確" and (r[1]["s0"], r[1]["s1"]) == (100, 110) and len(r) == 2, r
    c = candidates([{"id": "A", "title": "般若心經略疏"}, {"id": "B", "title": "般若心經略疏連珠記卷上"}, {"id": "C", "title": "心經注"}])
    assert c == [("A", "B")], c
    print("OK")
