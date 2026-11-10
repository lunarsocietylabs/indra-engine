# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lunar Society Technologies
"""
v4_cite.py — 以经注经引证图：讲这一句经时，历代注家引了哪些别的经来证。

做法：取每部注疏归属到这一句的疏文（v4_locate 的確层段落），到"可引的经"里找逐字相同的长片段（≥min_len 字）。
同一处出处（同一部经、位置相差不远）被多少部**互不相同**的注疏引到，就是它的分量。排序只用可数的事实：
独立引用的家数，其次片段长度。不做任何"相关度"判断。

RunIndex 是这里自带的索引实现（按固定步长抽取 k 字片段建表；凡 ≥ k+step−1 字的相同片段必被找到）。
全藏规模时换成基因位点表（genome.py 的 loci），接口相同：find_runs(text) -> [(疏内偏移, 长度, [(经号, 位置)…])]。
"""
import collections


class RunIndex:
    def __init__(self, texts, k=8, step=2):
        """texts: {经号: 已 norm 的全文}。"""
        self.texts, self.k, self.step = texts, k, step
        self.tab = collections.defaultdict(list)
        for d in sorted(texts):
            t = texts[d]
            for p in range(0, len(t) - k + 1, step):
                self.tab[t[p:p + k]].append((d, p))
        self.min_len = k + step - 1

    def find_runs(self, piece, skip=None, max_occ=50, max_locs=3):
        """piece 里与库中某处逐字相同的极大片段。skip(text)->bool 为真的片段不要（如：目标经自己的话）。"""
        k, tab, out, seen = self.k, self.tab, {}, set()
        for i in range(len(piece) - k + 1):
            hits = tab.get(piece[i:i + k])
            if not hits or len(hits) > max_occ:
                continue
            for d, p in hits:
                if (d, p - i) in seen:
                    continue
                t = self.texts[d]
                a, b = i, p
                while a > 0 and b > 0 and piece[a - 1] == t[b - 1]:
                    a -= 1; b -= 1
                e, f = i + k, p + k
                while e < len(piece) and f < len(t) and piece[e] == t[f]:
                    e += 1; f += 1
                seen.add((d, p - i))
                if e - a >= self.min_len and not (skip and skip(piece[a:e])):
                    out.setdefault((a, e - a), []).append((d, b))
        # 在库里出现超过 max_locs 处的片段是套语（如来十号、"阿耨多羅三藐三菩提"之类），不算引证
        return [(a, ln, sorted(locs)) for (a, ln), locs in sorted(out.items()) if len(locs) <= max_locs]


def cite_graph(clause_segs, texts, find_runs, tol=60, top=20, texts_lib=None):
    """clause_segs: {注疏号: [seg…]}（seg 含 c0,c1；只传归属到这一句的段落）；texts: {注疏号: 疏文（norm）}。
    返回 [{doc, pos, len, n, by:[注疏号…]}]，按独立引用家数降序。同一出处 = 同一部经、位置相差 ≤tol。"""
    hits = []
    for cd in sorted(clause_segs):
        txt = texts[cd]
        for s in clause_segs[cd]:
            for off, ln, locs in find_runs(txt[s["c0"]:s["c1"]]):
                for d, pos in locs:
                    hits.append((d, pos, ln, cd))
    hits.sort()
    out, cur = [], None
    # 同一部经里同一段话（套语重出）只留一处：按 (经, 片段起点所在的 tol 窗口) 归组之外，再按出处文字去重（见下）
    for d, pos, ln, cd in hits:
        if cur and cur["doc"] == d and pos - cur["_last"] <= tol:
            cur["_last"] = pos; cur["len"] = max(cur["len"], ln); cur["_by"].add(cd)
        else:
            cur = {"doc": d, "pos": pos, "len": ln, "_last": pos, "_by": {cd}}
            out.append(cur)
    for c in out:
        c["by"] = sorted(c.pop("_by")); c["n"] = len(c["by"]); c.pop("_last")
    out.sort(key=lambda x: (-x["n"], -x["len"], x["doc"], x["pos"]))
    if texts_lib is not None:                                 # 同一部经里文字相同的出处并为一条
        seen, ded = set(), []
        for c in out:
            key = (c["doc"], texts_lib[c["doc"]][c["pos"]:c["pos"] + 12])
            if key in seen:
                continue
            seen.add(key); ded.append(c)
        out = ded
    return out[:top]


if __name__ == "__main__":
    lib = {"T09n0262": "甲" * 50 + "諸法從本來常自寂滅相佛子行道已來世得作佛" + "乙" * 50,
           "T10n0279": "丙" * 30 + "心如工畫師能畫諸世間五蘊悉從生無法而不造" + "丁" * 30}
    ix = RunIndex(lib)
    A = "故法華云諸法從本來常自寂滅相是也"
    B = "經言諸法從本來常自寂滅相佛子行道已又華嚴云心如工畫師能畫諸世間"
    r = ix.find_runs(A)
    assert r and r[0][2][0][0] == "T09n0262" and r[0][1] == 10, r
    g = cite_graph({"A": [{"c0": 0, "c1": len(A)}], "B": [{"c0": 0, "c1": len(B)}]}, {"A": A, "B": B}, ix.find_runs)
    assert g[0]["doc"] == "T09n0262" and g[0]["n"] == 2 and g[0]["by"] == ["A", "B"], g
    assert g[1]["doc"] == "T10n0279" and g[1]["n"] == 1, g
    assert not ix.find_runs(A, skip=lambda t: "寂滅相" in t)
    print("OK")
