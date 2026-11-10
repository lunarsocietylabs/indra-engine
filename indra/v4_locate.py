# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lunar Society Technologies
"""
v4_locate.py — 注经引擎 v4 · 全文定位（确定性、零模型、每一步可解释）

v3 回答"疏文里哪几个字是引的经"。v4 回答"这一整段疏文在讲哪一句经"：把整部注疏比对到经文坐标上，
没有引一个字的义理段落也有归属。思路借自基因组比对（minimap2 的 种子—成链），再加上读段比对里的"假经对照"估计错误率。

  1. 种子   疏文与经文之间全部逐字相同、左右都不能再延伸的片段（≥2 字）。
  2. 权重   每个种子的信息量 = (长度-1) × log(1 + 经长 / 该种子首个二字组在经中的出现次数)；"如是我聞"类高频片段几乎不值分。
  3. 成链   在全部种子里求一条总得分最大、疏文顺序 = 经文顺序的链（动态规划，间隙有罚分）。
           注家常把一部经走两遍（悬谈、正释）：取最优链后，把用过的疏文部分剔除，再取下一条链，直到得分低于门槛。
  4. 归属   链上相邻两个锚点之间的疏文（没有引经字的部分）归属于对应的那段经文：疏文接在某句牒文之后，就是在讲那一句。
  5. 门槛   用同样长度的"假经"（与该疏无关的别部文献的窗口）做同样的比对，得到链得分的零分布；
           据此估计任一得分门槛下的假发现率（FDR），确定层与候选层用 FDR 定义，而不是手调的阈值。

接口只依赖字符串（已 norm 的字符流），无网络、无随机；同输入必得同输出。
"""
import bisect, collections, math

# ---------------------------------------------------------------- 种子
def seeds(S, C, kmin=2, max_occ=300):
    """S（经）与 C（疏）之间所有极大公共子串 (ci, sj, L)；L>=kmin。二字组在经中出现 > max_occ 次的位置跳过。"""
    idx = collections.defaultdict(list)
    for j in range(len(S) - kmin + 1):
        idx[S[j:j + kmin]].append(j)
    out = []
    n, m = len(C), len(S)
    for i in range(n - kmin + 1):
        js = idx.get(C[i:i + kmin])
        if not js or len(js) > max_occ:
            continue
        for j in js:
            if i > 0 and j > 0 and C[i - 1] == S[j - 1]:
                continue                                   # 左边还能延伸：不是极大
            L = kmin
            while i + L < n and j + L < m and C[i + L] == S[j + L]:
                L += 1
            out.append((i, j, L, len(js)))
    return out


def weigh(sd, lenS):
    """(i, j, L, occ2) -> (i, j, L, w)"""
    return [(i, j, L, (L - 1) * math.log(1.0 + lenS / occ)) for i, j, L, occ in sd]

# ---------------------------------------------------------------- 成链
def best_chain(sd, gap_c=900, gap_s=600, look=300, a=0.02, b=0.6):
    """sd: [(i,j,L,w)]。返回 (得分, [种子下标…])。i 递增、j 不后退；间隙罚分 a*疏文间隙 + b*log(1+经文间隙)。"""
    order = sorted(range(len(sd)), key=lambda k: (sd[k][0], sd[k][1]))
    f = {}; prev = {}
    seq = []
    for k in order:
        i, j, L, w = sd[k]
        best, bp = w, -1
        for p in reversed(seq[-look:]):
            ip, jp, Lp, _ = sd[p]
            if ip + Lp > i or j < jp + Lp - 2:
                continue
            gc, gs = i - (ip + Lp), max(0, j - (jp + Lp))
            if gc > gap_c or gs > gap_s:
                continue
            v = f[p] + w - a * gc - b * math.log1p(gs)
            if v > best:
                best, bp = v, p
        f[k], prev[k] = best, bp
        seq.append(k)
    if not f:
        return 0.0, []
    end = max(f, key=lambda k: (f[k], -k))
    path = []
    while end != -1:
        path.append(end); end = prev[end]
    return f[path[0]], path[::-1]


def chains(S, C, min_score=8.0, max_chains=12, **kw):
    """反复取最优链；用过的疏文区间剔除后再取。返回 [{score, seeds:[(i,j,L,w)]}…]，按得分降序。"""
    sd = weigh(seeds(S, C), len(S))
    out = []
    while sd and len(out) < max_chains:
        score, path = best_chain(sd, **kw)
        if score < min_score or not path:
            break
        used = [sd[k] for k in path]
        out.append({"score": score, "seeds": used})
        cov = [(s[0], s[0] + s[2]) for s in used]
        cov.sort()
        def hit(s):
            i0, i1 = s[0], s[0] + s[2]
            p = bisect.bisect_right(cov, (i0, 1 << 60)) - 1
            for q in (p, p + 1):
                if 0 <= q < len(cov) and cov[q][0] < i1 and i0 < cov[q][1]:
                    return True
            return False
        sd = [s for s in sd if not hit(s)]
    return out

# ---------------------------------------------------------------- 归属
def attribute(chs, lenC, min_score=0.0):
    """把疏文每个字归属到经文区间。返回 (段落列表, 归属字数)。
    段落 = {c0,c1,s0,s1,kind,chain}；kind='引'（疏文里的逐字引文，对应经文就是那几个字）
    或 '釋'（两个锚点之间、没有引经字的疏文，归属于前一锚点起到后一锚点止的经文）。"""
    owner = bytearray(lenC)
    segs = []
    for ci, ch in sorted(enumerate(chs), key=lambda t: -t[1]["score"]):
        if ch["score"] < min_score:
            continue
        ss = sorted(ch["seeds"])
        for q, (i, j, L, _) in enumerate(ss):
            if not any(owner[i:i + L]):
                owner[i:i + L] = b"\x01" * L
                segs.append({"c0": i, "c1": i + L, "s0": j, "s1": j + L, "kind": "引", "chain": ci})
            if q + 1 < len(ss):
                i2, j2 = ss[q + 1][0], ss[q + 1][1]
                g0, g1 = i + L, i2
                if g1 > g0:
                    s1 = j2 if j2 > j else j + L
                    run = None
                    for x in range(g0, g1 + 1):                     # 只认领尚未归属的连续片段
                        free = x < g1 and not owner[x]
                        if free and run is None:
                            run = x
                        if (not free) and run is not None:
                            owner[run:x] = b"\x01" * (x - run)
                            segs.append({"c0": run, "c1": x, "s0": j, "s1": s1, "kind": "釋", "chain": ci})
                            run = None
    segs.sort(key=lambda s: (s["c0"], s["c1"]))
    return segs, sum(owner)


def by_clause(segs, bounds):
    """bounds: 经文每个小句在 S 中的 (s0, s1)。返回每个小句上归属的段落下标列表。"""
    starts = [b[0] for b in bounds]
    res = [[] for _ in bounds]
    for k, s in enumerate(segs):
        q = max(0, bisect.bisect_right(starts, s["s0"]) - 1)
        while q < len(bounds) and bounds[q][0] < s["s1"]:
            if bounds[q][1] > s["s0"]:
                res[q].append(k)
            q += 1
    return res

# ---------------------------------------------------------------- 假经对照
def fdr_thresholds(true_scores, decoy_score_lists, targets=(0.01, 0.1)):
    """true_scores: 真经上各链得分；decoy_score_lists: 每个假经上各链得分的列表。
    在得分 t 处：估计假发现数 = 各假经上 ≥t 的链数的均值；真发现数 = 真经上 ≥t 的链数；FDR = 前者/后者。
    返回 {target: 最低的满足 FDR<=target 的得分 t（没有则 None）} 与完整曲线。"""
    cand = sorted(set(true_scores))
    curve = []
    for t in cand:
        tp = sum(1 for s in true_scores if s >= t)
        fp = sum(sum(1 for s in d if s >= t) for d in decoy_score_lists) / max(1, len(decoy_score_lists))
        curve.append((t, tp, fp, min(1.0, fp / tp) if tp else 1.0))
    out = {}
    for tg in targets:
        ok = [t for t, tp, fp, r in curve if r <= tg]
        out[tg] = min(ok) if ok else None
    return out, curve


def stats(chs, lenS, lenC):
    """疏文相对经文的整体形状（供关系判别用；这里只给指标，不下结论）。"""
    if not chs:
        return {"chains": 0, "cover_S": 0.0, "scale": lenC / max(1, lenS)}
    cov = bytearray(lenS)
    for ch in chs:
        for i, j, L, _ in ch["seeds"]:
            cov[j:j + L] = b"\x01" * L
    return {"chains": len(chs), "cover_S": sum(cov) / max(1, lenS), "scale": lenC / max(1, lenS),
            "top": chs[0]["score"], "anchors": sum(len(c["seeds"]) for c in chs)}


def locate(S, C, bounds=None, min_score=8.0):
    chs = chains(S, C, min_score=min_score)
    segs, n = attribute(chs, len(C))
    return {"chains": chs, "segs": segs, "located": n, "frac": n / max(1, len(C)),
            "clauses": by_clause(segs, bounds) if bounds else None, "stats": stats(chs, len(S), len(C))}
