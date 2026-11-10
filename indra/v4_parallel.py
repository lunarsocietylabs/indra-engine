# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lunar Society Technologies
"""
v4_parallel.py — 同本异译对齐（如《心經》T0250 与 T0251、《阿彌陀經》与《稱讚淨土佛攝受經》）。

两个译本共有的字句（咒、名相、套语、沿用前译之处）仍用 v4_locate 找：把 A 当经、B 当疏，取確层引文锚点；
异译通篇同序，所以锚点落在一条链上。句对 = 两边各自的句子里有確层锚点相连的；连不上的句子不硬配（留空）。
用途：注 B 的疏，可经句对转到 A 的坐标上（再交给 v4_layers.compose），于是一个译本的注疏也能挂到另一个译本上——标为候层。
"""
import bisect, collections

from . import v4_locate as V


def align(A, B, boundsA, boundsB, decoys, bg):
    """返回 {pairs:[(a句, b句, 相同字数)], cover_A, cover_B, stats}。decoys 必须给（没有对照不出结果）。
    異譯之间相同的片段往往很长、处数很少；確层要求 假经数 × 锚点数 ≥ 100，所以短经请给足假经（16 部以上）。"""
    r = V.locate(A, B, boundsA, decoys, bg)
    sb = [b[0] for b in boundsB]
    share = collections.Counter()
    sa = [b[0] for b in boundsA]
    for s in r["segs"]:
        if s["kind"] != "引" or s["tier"] != V.SURE:
            continue
        for o in range(min(s["c1"] - s["c0"], s["s1"] - s["s0"])):     # 一处相同的长片段可以跨好几句：逐字记到各自的句对上
            ka = max(0, bisect.bisect_right(sa, s["s0"] + o) - 1)
            kb = max(0, bisect.bisect_right(sb, s["c0"] + o) - 1)
            share[(ka, kb)] += 1
    pairs = sorted((a, b, n) for (a, b), n in share.items()
                   if n * 2 >= min(boundsA[a][1] - boundsA[a][0], boundsB[b][1] - boundsB[b][0]))   # 至少对上较短一句的一半
    return {"pairs": pairs, "cover_A": len({p[0] for p in pairs}) / max(1, len(boundsA)),
            "cover_B": len({p[1] for p in pairs}) / max(1, len(boundsB)), "stats": r["stats"]}


def as_segs(pairs, boundsA, boundsB):
    """句对 → 可供 compose 使用的映射段（B 的句子区间 → A 的句子区间），一律候层。"""
    out = []
    for a, b, n in pairs:
        out.append({"c0": boundsB[b][0], "c1": boundsB[b][1], "s0": boundsA[a][0], "s1": boundsA[a][1],
                    "kind": "引", "tier": V.CAND})
    return out
