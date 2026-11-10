# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lunar Society Technologies
"""
v4_layers.py — 层层相注：经 ← 疏 ← 钞 ← 记。
钞（对疏的注）用同一套 locate() 比对到疏的坐标上；疏已在经的坐标上；两次映射相乘，钞也就落在经上。
compose(b_to_a, a_to_s)：b_to_a 的每一段给出 b 的区间 (c0,c1) 对应 a 的区间 (s0,s1)；a_to_s 给出 a 的区间对应经的区间。
结果的每段仍带 b 的区间，经区间取 a 区间所覆盖的各段经区间的并集（最小起点、最大终点），kind 记为「複」，depth 加一。
"""
import bisect


def compose(b_to_a, a_to_s):
    segs = sorted(a_to_s, key=lambda s: s["c0"])
    starts = [s["c0"] for s in segs]
    out = []
    for g in b_to_a:
        lo, hi = g["s0"], g["s1"]
        q = max(0, bisect.bisect_right(starts, lo) - 1)
        s0 = s1 = None
        while q < len(segs) and segs[q]["c0"] < hi:
            h = segs[q]
            if h["c1"] > lo:
                s0 = h["s0"] if s0 is None else min(s0, h["s0"])
                s1 = h["s1"] if s1 is None else max(s1, h["s1"])
            q += 1
        if s0 is not None:
            out.append({"c0": g["c0"], "c1": g["c1"], "s0": s0, "s1": s1, "kind": "複", "via": (lo, hi), "depth": g.get("depth", 1) + 1})
    return out


if __name__ == "__main__":
    a_to_s = [{"c0": 0, "c1": 20, "s0": 100, "s1": 110, "kind": "引"}, {"c0": 20, "c1": 60, "s0": 100, "s1": 130, "kind": "釋"}]
    b_to_a = [{"c0": 5, "c1": 30, "s0": 10, "s1": 40, "kind": "引"}]
    r = compose(b_to_a, a_to_s)
    assert r == [{"c0": 5, "c1": 30, "s0": 100, "s1": 130, "kind": "複", "via": (10, 40), "depth": 2}], r
    print("OK")
