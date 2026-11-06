# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lunar Society Technologies
"""
lemma_walk.py — v3：随文释义的"行走模型"（确定性、零模型、每一步可解释）

v2 的问题：短片段（4–7 字）只要落在近邻锚点中位数 ±W（法华约 ±840 字）以内就接受。
一个在经中出现 10 次的 4 字串，偶然落进这个窗口的概率约 10×1680/70000 ≈ 24%——
固定窗口 + 固定出现上限，召回与精确率只能此消彼长。

v3 的三个改动（借自基因组比对：minimap2 的共线链 + BLAST 的期望偶然数）：
  1. 共线链确认锚点：≥8 字、经内 ≤3 处的片段，只有和同一疏文中前后另一锚点
     "疏文顺序 = 经文顺序"（疏文间距 ≤Gc、经文间距 ≤Gt）时才算确认锚。
  2. 夹逼区间代替固定窗口：短片段必须落在它前一个与后一个确认锚之间的经文区间
     （疏家逐句释经时，这个区间往往只有几十字）。
  3. 期望偶然数代替出现上限：P = 出现次数 × 区间长度 / 经长，即"若这是巧合，
     有多大机会恰好落进区间"；P ≤ α 才接受。区间越紧，越常见的短语也能确认；
     区间越松，越要求罕见——同一条规则自动在精确与召回之间取最优。
  另：背景频度改用大乘经论 + 阿含（不再只用阿含），用来识别套语与经典专有词。
"""
import bisect, collections

import numpy as np


def background_counts(C, sa, sa_np, strings, sa_range, fams=("comp", "bg")):
    """每个字串在背景（别部大乘经论 + 阿含背景）中的出现次数；SA 二分 + numpy 计数。"""
    mask = np.zeros(len(C.text), dtype=np.bool_)
    nbg = 0
    for d in C.docs:
        if d["fam"] in fams:
            mask[d["start"]:d["end"]] = True
            nbg += d["end"] - d["start"]
    out = {}
    for s in strings:
        lo, hi = sa_range(C.text, sa, s)
        out[s] = int(mask[sa_np[lo:hi]].sum()) if hi > lo else 0
    return out, nbg


LEMMA_PRE2 = ("經云", "文云", "經曰", "偈云", "下云", "故云", "所謂", "次從", "初從", "言從")
LEMMA_PRE1 = ("從", "至", "訖", "次", "言", "云", "曰")
LEMMA_POST = ("者", "下", "去", "已下", "以下", "等")


def lemma_mark(text, x, ext=True):
    """疏家"牒经/科判"句式（标点已去，只看字）：X者、經云X；扩展：從X下、至X、X去、X已下……"""
    p, m = x["p"], x["m"]
    if text[p + m:p + m + 1] == "者" or text[max(0, p - 2):p] in LEMMA_PRE2:
        return True
    if not ext:
        return False
    return text[p - 1:p] in LEMMA_PRE1 or text[p + m:p + m + 1] in LEMMA_POST or text[p + m:p + m + 2] in LEMMA_POST


def _confirm(seeds, Gc, Gt, h=40):
    """共线支持：seed (p,t,m,i)；若存在另一片段的 seed 与之疏文/经文同向且间距受限，二者互相确认。"""
    seeds.sort()
    ok = set()
    for a in range(len(seeds)):
        pa, ta, ma, ia = seeds[a]
        for b in range(a - 1, max(-1, a - h) - 1, -1):
            pb, tb, mb, ib = seeds[b]
            if pa - pb > Gc:
                break
            if ib == ia:
                continue
            if 0 < ta - tb <= Gt:
                ok.add(a); ok.add(b)
    return [seeds[k] for k in sorted(ok)]


def lemma_walk(spans, N_T, bgc, text, nbg, alpha=0.03, lemma=False, lemma_k=5, lemma_ext=False, grow=True, seed_E=1.0,
               Gc=3000, Gt_frac=0.05, delta=60, one_side=600):
    """期望偶然数 E = f(s) × B：
         f(s) = (背景出现次数 + 经内出现次数) / (背景字数 + 经长) —— 这个字串在佛典语言中的一般频度；
         B    = 允许它落入的经文区间长度（有夹逼区间用区间，否则用全经）。
       E 就是"如果疏家只是自己在说话、并非在引这部经，这个字串恰好出现在这段经文里的期望次数"。
       E ≤ α 接受。区间紧 → 常见短语也能确认；没有区间 → 只接受本身就罕见的字串。
       返回 (accepted: dict id(x)->经文位置, why: dict id(x)->拒绝原因)。"""
    Gt = max(500, int(Gt_frac * N_T))
    Z = nbg + N_T
    f = lambda x: (bgc.get(x["s"], 0) + len(x["occ"])) / Z
    by = collections.defaultdict(list)
    for x in spans:
        by[x["doc"]["id"]].append(x)
    acc, why = {}, {}
    for xs in by.values():
        xs.sort(key=lambda x: x["p"])
        seeds = [(x["p"], t, x["m"], i) for i, x in enumerate(xs)
                 if x["attr"] != "other" and x["m"] >= 8 and len(x["occ"]) <= 3 and f(x) * N_T <= seed_E for t in x["occ"]]
        best = {}
        for p, t, m, i in _confirm(seeds, Gc, Gt):
            best.setdefault(i, (p, t, m, i))
        anchors = sorted(best.values())
        for i, a in best.items():
            acc[id(xs[i])] = a[1]

        def bracket_pass(anchors):
            aps = [a[0] for a in anchors]
            newly = []
            for i, x in enumerate(xs):
                if id(x) in acc:
                    continue
                if x["attr"] == "other":
                    why[id(x)] = "other_sutra"; continue
                k = bisect.bisect_left(aps, x["p"])
                prv = anchors[k - 1] if k > 0 and x["p"] - aps[k - 1] <= Gc else None
                nxt = anchors[k] if k < len(anchors) and aps[k] - x["p"] <= Gc else None
                if prv and nxt and prv[1] <= nxt[1] + nxt[2] and nxt[1] - prv[1] <= Gt:
                    iv = [(prv[1] - delta, nxt[1] + nxt[2] + delta)]
                else:
                    iv = []
                    if prv:
                        iv.append((prv[1] - delta, prv[1] + prv[2] + one_side))
                    if nxt:
                        iv.append((nxt[1] - one_side, nxt[1] + nxt[2] + delta))
                inb = [t for t in x["occ"] if any(a <= t and t + x["m"] <= b for a, b in iv)]
                thr = alpha * (lemma_k if lemma and lemma_mark(text, x, lemma_ext) else 1)
                if inb:
                    B = sum(b - a for a, b in iv)
                    if f(x) * B <= thr:
                        acc[id(x)] = inb[0]; why.pop(id(x), None)
                        if x["m"] >= 6 and len(inb) == 1:
                            newly.append((x["p"], inb[0], x["m"], i))
                        continue
                if len(x["occ"]) <= 2 and f(x) * N_T <= thr:      # 无区间：字串本身须足够罕见
                    acc[id(x)] = x["occ"][0]; why.pop(id(x), None); continue
                why[id(x)] = ("no_anchor" if not iv else "outside_bracket" if not inb else "too_common")
            return newly
        newly = bracket_pass(anchors)
        if grow and newly:
            bracket_pass(sorted(anchors + newly))
    return acc, why
