# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lunar Society Technologies
"""
v4_cite.py — 以经注经引证图：讲这一句经时，历代注家引了哪些经来证。
输入：每部注疏在这句经上的归属段（v4_locate 的结果）和它的文本；以及一个 find_runs(text) 函数
（由后缀数组或基因位点表支撑：返回 text 里每个 ≥L 字的极大重复 (offset, length, [(doc, pos)…])，已排除目标经与该注疏自身）。
聚合：同一处出处（doc 相同、pos 相差 ≤tol）被多少部**互不相同**的注疏引到。排序只用可数的事实：独立引用家数，其次出处长度。
"""
import collections


def cite_graph(clause_segs, texts, find_runs, tol=40, min_len=8):
    """clause_segs: {commentary_doc: [seg,…]}（seg 含 c0,c1）；texts: {commentary_doc: 疏文（norm）}。
    返回 [{doc, pos, len, by:[commentary_doc…], n}]，按 n 降序。"""
    buckets = collections.defaultdict(list)
    for cd, segs in clause_segs.items():
        txt = texts[cd]
        for s in segs:
            piece = txt[s["c0"]:s["c1"]]
            if len(piece) < min_len:
                continue
            for off, ln, locs in find_runs(piece, exclude=cd):
                if ln < min_len:
                    continue
                for d, pos in locs:
                    buckets[(d, pos // tol)].append((cd, d, pos, ln))
    out = []
    for key, hits in buckets.items():
        by = sorted({h[0] for h in hits})
        out.append({"doc": key[0], "pos": min(h[2] for h in hits), "len": max(h[3] for h in hits), "by": by, "n": len(by)})
    return sorted(out, key=lambda x: (-x["n"], -x["len"], x["doc"], x["pos"]))


if __name__ == "__main__":
    texts = {"A": "x" * 100, "B": "y" * 100, "C": "z" * 100}
    segs = {k: [{"c0": 0, "c1": 50, "s0": 0, "s1": 10}] for k in texts}
    def fr(piece, exclude):
        return [(0, 20, [("T09n0262", 1000)])] if exclude in "AB" else [(0, 20, [("T09n0262", 1010), ("T10n0279", 5)])]
    g = cite_graph(segs, texts, fr)
    assert g[0]["doc"] == "T09n0262" and g[0]["n"] == 3 and g[1]["n"] == 1, g
    print("OK")
