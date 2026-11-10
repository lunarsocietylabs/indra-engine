# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lunar Society Technologies
"""
v4_locate.py — 注经引擎 v4 · 全文定位（确定性、零模型、每一步可解释、错误率可测）

v3 回答"疏文里哪几个字是引的经"。v4 回答"这一整段疏文在讲哪一句经"：把整部注疏比对到经文坐标上。

  1. 种子   疏文与经文之间逐字相同、左右都不能再延伸的片段。短经用二字起，长经用三、四字起（见 kmer_for）。
           不因"高频"跳过任何位置——跳过会让以常见词开头的整段引文丢失。
  2. 信息量 每个种子值多少，由**背景语言模型**决定：这几个字在一般佛典里连着出现有多稀罕（二字组条件概率，单位 nat）。
           "般若波羅蜜多"到处都是，几乎不值分；一句只在本经出现的话，值很多分。
  3. 成链   动态规划求得分最大的链。得分 = 信息量之和 − 位置自由度的代价：
             链的起点付 ln|经长|（它可以落在经里任何地方）；之后每一步付 ln(1+经文跨度)（它只能落在前一锚点之后的窗口里）；
             释文再长也只付很小的对数代价（一句经可以讲几千字）；允许在当前句附近小幅回引（注家讲一句时会反复提这句里的词）。
           于是链得分近似"这是真引文 相对 碰巧相同"的对数几率，不是手调的权重。
  4. 补认   链上相邻两个锚点之间，再在**那一小段经文窗口**里找二字以上的短牒文（"言空者""色者"）。窗口小，所以短也有据。
  5. 异文   同一斜线上只隔一两个字的两个锚点并成一处引文，记下差了几个字（异体、通假、脱衍），不当作两处。
  6. 门槛   同一部疏、同样的流程，去比对若干部同样长度的"假经"（无关文献的窗口）。每个锚点有两个量：
           "局部支持度"（它周围一段疏文里链的净得分）和"局部覆盖度"（它周围那段经文被就近牒到了多少）。
           真经与假经一比，就得到任一门槛下的假发现率（FDR）。確层 = FDR≤1%，候层 = FDR≤10%，其余不出。
           门槛由数据定，不由人定（见 thresholds_all）。
  7. 归属   只用过了门槛的锚点。两个锚点之间的释文：
             前后锚点在同一句，或后一处从下一句句首牒起 → 归前一句（先牒后释），层级取两端较低者；
             后一处从下一句中间牒起（句首可能漏认）→ 候层；
             中间跳过了若干句 → 归"前一句至后一句之前"这个区间，降为候层（除非略牒文法另有明文，见 v4_grammar）；
             后一锚点由"故云／故曰"引出（结引）→ 区间含后一句，候层。
           能带释文的锚点还须"在逐句讲经"：它周围那段经文按信息量计有一半以上被就近的引文牒到（见 _led）。
           别的文献只是引到本经几句通用的话，只出引文、不带释文。链上最后一处引文之后的文字没有下一处牒文收口，只到候层。
           离锚点太远的释文降层或不出（reach）。不在主链上的零星引文记为「參」（参见别处经文），不带释文。

接口只依赖字符串（已 norm 的字符流），无网络、无随机；同输入必得同输出。
"""
import bisect, collections, math

import numpy as np

TAIL_MARKS = ("故云", "故曰", "故言", "故說", "故名", "故經云", "是故云")
SURE, CAND = "確", "候"
HEAD_SLACK = 2                     # 牒文起点离句首不超过几个字，算"从句首牒起"


# ---------------------------------------------------------------- 背景语言模型
class Background:
    """二字组条件概率（回退到单字），由一批"一般佛典"文本统计。info(s) = −ln P(s)，单位 nat。"""

    def __init__(self, texts, mu=4.0, cap=12.0):
        self.uni, self.bi = collections.Counter(), collections.Counter()
        for t in texts:
            self.uni.update(t)
            self.bi.update(t[k:k + 2] for k in range(len(t) - 1))
        self.N = sum(self.uni.values()) or 1
        self.V = len(self.uni) + 1
        self.mu, self.cap, self._memo = mu, cap, {}

    def info(self, s):
        v = self._memo.get(s)
        if v is not None:
            return v
        uni, bi, N, V, mu, cap = self.uni, self.bi, self.N, self.V, self.mu, self.cap
        a = s[0]
        h = min(cap, -math.log((uni[a] + 1) / (N + V)))
        for b in s[1:]:
            pb = (uni[b] + 1) / (N + V)
            h += min(cap, -math.log((bi[a + b] + mu * pb) / (uni[a] + mu)))
            a = b
        if len(self._memo) < 2_000_000:
            self._memo[s] = h
        return h


# ---------------------------------------------------------------- 种子
def kmer_for(lenS):
    return 2 if lenS <= 3000 else 3 if lenS <= 30000 else 4


def seeds(S, C, k=None):
    """S（经）与 C（疏）之间全部长度 ≥k 的极大公共子串 [(i, j, L)]。"""
    k = k or kmer_for(len(S))
    idx = collections.defaultdict(list)
    for j in range(len(S) - k + 1):
        idx[S[j:j + k]].append(j)
    out, n, m = [], len(C), len(S)
    get = idx.get
    for i in range(n - k + 1):
        js = get(C[i:i + k])
        if not js:
            continue
        left = C[i - 1] if i else None
        for j in js:
            if j and left == S[j - 1]:
                continue                                   # 左边还能延伸：不是极大
            L = k
            while i + L < n and j + L < m and C[i + L] == S[j + L]:
                L += 1
            out.append((i, j, L))
    return out


# ---------------------------------------------------------------- 成链
class _Fenwick:
    """前缀最大值（值, 下标）。"""

    def __init__(self, n):
        self.n = n + 1
        self.v = [-1e18] * (self.n + 1)
        self.a = [-1] * (self.n + 1)

    def put(self, pos, val, arg):
        p = pos + 1
        while p <= self.n:
            if val > self.v[p]:
                self.v[p], self.a[p] = val, arg
            p += p & -p

    def best(self, pos):
        p, bv, ba = min(pos, self.n - 1) + 1, -1e18, -1
        while p > 0:
            if self.v[p] > bv:
                bv, ba = self.v[p], self.a[p]
            p -= p & -p
        return bv, ba


def _pen(gs, gc, Lp, back, beta, cgap):
    """从前一锚点（经止点之后 gs 字、疏文隔 gc 字）走到这一锚点的代价；不允许则返回 None。"""
    if gs >= 0:
        p = math.log1p(gs)
    elif -gs <= back + Lp:
        p = beta + math.log1p(-gs)
    else:
        return None
    return p + cgap * math.log1p(gc)


def chain(sd, W, lenS, lenC, look=160, back=24, beta=1.0, cgap=1.0, tau=2.0, min_score=4.0, max_chains=64):
    """sd: [(i,j,L)]，W: 各种子信息量。一次动态规划，再从高分终点回溯出互不重叠（疏文上）的若干条链。
    返回 [{score, anchors:[[i,j,L,w,net]…]}]，按得分降序；net = 该锚点信息量 − 走到它的代价。"""
    n = len(sd)
    if not n:
        return []
    order = sorted(range(n), key=lambda q: (sd[q][0], sd[q][1], sd[q][2]))
    I = np.array([sd[q][0] for q in order], dtype=np.int64)
    J = np.array([sd[q][1] for q in order], dtype=np.int64)
    L = np.array([sd[q][2] for q in order], dtype=np.int64)
    Wt = np.array([W[q] for q in order], dtype=np.float64)
    IE, JE = I + L, J + L
    F = np.zeros(n); P = np.full(n, -1, dtype=np.int64)
    START = math.log(1 + lenS) + tau
    FAR = math.log(1 + lenS) + cgap * math.log1p(lenC) + tau        # 远距离衔接：按最坏情况付费（只会低估得分）
    fen = _Fenwick(lenS + 2)
    by_end = np.argsort(IE, kind="stable")
    ptr = 0
    for q in range(n):
        i, j = int(I[q]), int(J[q])
        while ptr < n and IE[by_end[ptr]] <= i:
            e = int(by_end[ptr]); ptr += 1
            if e < q:
                fen.put(int(JE[e]), float(F[e]), e)
        best, bp = -START, -1
        lo = max(0, q - look)
        if q > lo:
            ie, je = IE[lo:q], JE[lo:q]
            gs = j - je
            ok = ie <= i
            fwd = gs >= 0
            ok &= fwd | (-gs <= back + L[lo:q])
            pen = np.where(fwd, np.log1p(np.abs(gs)), beta + np.log1p(np.abs(gs))) + cgap * np.log1p(np.maximum(i - ie, 0))
            v = np.where(ok, F[lo:q] - pen - tau, -1e18)
            a = int(np.argmax(v))
            if v[a] > best:
                best, bp = float(v[a]), lo + a
        fv, fa = fen.best(j)
        if fa >= 0 and fv - FAR > best:
            best, bp = fv - FAR, fa
        F[q] = Wt[q] + best
        P[q] = bp
    ends = sorted(range(n), key=lambda q: (-F[q], q))
    used = np.zeros(n, dtype=bool)
    usedC = bytearray(lenC + 1)
    out = []
    for e in ends:
        if len(out) >= max_chains or F[e] < min_score:
            break
        if used[e]:
            continue
        path, q = [], e
        while q != -1 and not used[q]:
            path.append(q); q = int(P[q])
        path.reverse()
        for q in path:
            used[q] = True
        keep = [q for q in path if not any(usedC[int(I[q]):int(IE[q])])]
        if not keep:
            continue
        anchors, score, prev = [], 0.0, None
        for q in keep:
            if prev is None:
                pen = START
            else:
                pen = _pen(int(J[q] - JE[prev]), int(I[q] - IE[prev]), int(L[prev]), back, beta, cgap)
                pen = FAR if pen is None else min(pen + tau, FAR)
            net = float(Wt[q]) - pen
            anchors.append([int(I[q]), int(J[q]), int(L[q]), float(Wt[q]), net])
            score += net; prev = q
        if score < min_score:
            continue
        for a in anchors:
            usedC[a[0]:a[0] + a[2]] = b"\x01" * a[2]
        out.append({"score": score, "anchors": anchors})
    out.sort(key=lambda c: (-c["score"], c["anchors"][0][0]))
    return out


# ---------------------------------------------------------------- 补认短牒文
GLOSS_AFTER, GLOSS_BEFORE = "者", "言云曰謂名"


def _local(win, C, g0, g1, bg, bonus=4.0):
    """疏文 C[g0:g1] 对一小段经文窗口 win 的全部 ≥2 字极大相同片段，及其净得分。
    净得分 = 信息量（＋"X者／言X"句式加分：这种句式把牒文的位置钉住了）− ln(窗口长) − ½ln(间隙长)。"""
    cost = math.log(1 + len(win)) + 0.5 * math.log(1 + g1 - g0)
    idx = collections.defaultdict(list)
    for j in range(len(win) - 1):
        idx[win[j:j + 2]].append(j)
    out = []
    for i in range(g0, g1 - 1):
        js = idx.get(C[i:i + 2])
        if not js:
            continue
        for j in js:
            if i > g0 and j and C[i - 1] == win[j - 1]:
                continue
            Ln = 2
            while i + Ln < g1 and j + Ln < len(win) and C[i + Ln] == win[j + Ln]:
                Ln += 1
            w = bg.info(C[i:i + Ln])
            mark = (C[i + Ln:i + Ln + 1] == GLOSS_AFTER) or (i > 0 and C[i - 1] in GLOSS_BEFORE)
            out.append((w + (bonus if mark else 0.0) - cost, i, j, Ln, w))
    return out


def refine(S, C, chs, bg, decoys=(), back=24, win_cap=400, targets=(0.01, 0.10)):
    """在每条链相邻锚点之间的疏文里，只对着那一小段经文窗口找 ≥2 字的短牒文（"言空者""色者"）。
    同一段疏文同时去比各部假经里同样大小的窗口，得到短牒文净得分的零分布；只留 FDR≤10% 的，≤1% 的另记。
    没有假经则不补认。就地加入锚点（weak=1 候 / 2 確），返回加入数。"""
    if not decoys:
        return 0
    jobs, true_nets, decoy_nets = [], [], [[] for _ in decoys]
    for ci, ch in enumerate(chs):
        A = sorted(_pad(a) for a in ch["anchors"])
        for x, y in zip(A, A[1:]):
            g0, g1 = x[0] + x[2], y[0]
            if g1 - g0 < 2:
                continue
            g1 = min(g1, g0 + 4000)
            w0 = max(0, min(x[1], y[1]) - back)
            w1 = min(len(S), max(x[1] + x[7], y[1] + y[7]) + back)
            if w1 - w0 > win_cap:
                w1 = min(len(S), max(x[1] + x[7] + back, w0 + win_cap))
            cand = _local(S[w0:w1], C, g0, g1, bg)
            for c in cand:
                true_nets.append(c[0])
            jobs.append((ci, w0, cand))
            for d, D in enumerate(decoys):
                if len(D) >= w1 - w0:
                    off = min(w0, len(D) - (w1 - w0))
                    decoy_nets[d].extend(c[0] for c in _local(D[off:off + w1 - w0], C, g0, g1, bg))
    th = thresholds(true_nets, decoy_nets, targets)
    lo, hi = th.get(targets[1]), th.get(targets[0])
    if lo is None:
        return 0
    added = 0
    new = collections.defaultdict(list)
    for ci, w0, cand in jobs:
        taken = {}
        for net, i, j, Ln, w in sorted(cand, key=lambda c: (-c[0], c[1], c[2])):
            if net < lo:
                break
            if any((i + z) in taken for z in range(Ln)):
                continue
            for z in range(Ln):
                taken[i + z] = 1
            new[ci].append([i, w0 + j, Ln, w, net, 2 if (hi is not None and net >= hi) else 1, 0, Ln])
            added += 1
    for ci, lst in new.items():
        chs[ci]["anchors"] = sorted([_pad(a) for a in chs[ci]["anchors"]] + lst)
    return added


def _pad(a):
    a = list(a)
    if len(a) < 6:
        a += [0] * (6 - len(a))
    if len(a) < 7:
        a.append(0)
    if len(a) < 8:
        a.append(a[2])
    return a                                                 # [i, j, L, w, net, weak, var, Ls]


def merge_near(chs, tol=2):
    """同一链上疏文、经文都只隔 ≤tol 字的相邻锚点并为一处引文；记异文字数。返回合并次数。"""
    n = 0
    for ch in chs:
        out = []
        for a in sorted(_pad(a) for a in ch["anchors"]):
            if out:
                p = out[-1]
                gc, gs = a[0] - (p[0] + p[2]), a[1] - (p[1] + p[7])
                if 0 <= gc <= tol and 0 <= gs <= tol and (gc or gs) and not (p[5] or a[5]):
                    out[-1] = [p[0], p[1], a[0] + a[2] - p[0], p[3] + a[3], p[4] + a[4], 0, p[6] + max(gc, gs),
                               a[1] + a[7] - p[1]]
                    n += 1
                    continue
            out.append(a)
        ch["anchors"] = out
    return n


RADIUS = 300


def support(chs, pre=None, lenS=0, R=None):
    """每个（非补认）锚点的局部支持度：同一条链上、疏文距离 ≤R 的非补认锚点净得分之和。
    补认的短牒文取它所在位置的同一数值，但不计入返回的列表（零分布只比主锚点）。
    另给每个主锚点"局部覆盖度" cov（见 _cover；pre 是经文逐字信息量前缀和）。返回 [(sup, cov)]。
    同时把锚点统一成字典：{i, j, L, Ls, w, net, weak, var, sup, cov}。"""
    allv = []
    R = R or RADIUS
    for ch in chs:
        A = []
        for a in ch["anchors"]:
            if not isinstance(a, dict):
                a = _pad(a)
                a = {"i": a[0], "j": a[1], "L": a[2], "Ls": a[7], "w": a[3], "net": a[4], "weak": a[5], "var": a[6]}
            A.append(a)
        A.sort(key=lambda a: (a["i"], a["j"]))
        M = [a for a in A if not a["weak"]]
        pos = [a["i"] for a in M]
        acc = [0.0]
        for a in M:
            acc.append(acc[-1] + a["net"])
        cov = _cover([(a["i"], a["j"], a["Ls"]) for a in M], pre, lenS) if pre is not None else [0.0] * len(M)
        for a, c in zip(M, cov):
            a["cov"] = round(c, 3)
        for a in A:
            lo = bisect.bisect_left(pos, a["i"] - R)
            hi = bisect.bisect_right(pos, a["i"] + R)
            a["sup"] = acc[hi] - acc[lo]
            if not a["weak"]:
                allv.append((a["sup"], a["cov"]))
            else:
                a["cov"] = 0.0
        ch["anchors"] = A
    return allv


ROOM = 200


def seed_floor(lenS, room=None, margin=2.0):
    """主锚点的最低信息量：在经里任选位置（ln|经|）、在疏文两百字内任选位置（ln 200），仍比碰巧高出 margin。"""
    return math.log(1 + lenS) + math.log(room or ROOM) + margin


def join_diagonal(sd, W, tol=2):
    """同一斜线上（疏、经错位相同）只隔 ≤tol 字的种子并成一个：中间那一两个字是异文（异体、通假、讹字）。
    并后的信息量是各段之和（异文那几个字不算）。返回 ([(i,j,L)], [w], [异文字数])。"""
    order = sorted(range(len(sd)), key=lambda q: (sd[q][0] - sd[q][1], sd[q][0]))
    out, Wo, Vo = [], [], []
    for q in order:
        i, j, L = sd[q]
        if out:
            pi, pj, pL = out[-1]
            gap = i - (pi + pL)
            if pi - pj == i - j and 0 < gap <= tol:
                out[-1] = (pi, pj, i + L - pi); Wo[-1] += W[q]; Vo[-1] += gap
                continue
        out.append((i, j, L)); Wo.append(W[q]); Vo.append(0)
    return out, Wo, Vo


def pipeline(S, C, bg, decoys=(), pre=None, **kw):
    """种子 → 信息量 → 并异文 → 成链 → 补认 → 支持度。给了 decoys 才做补认（假经自己走流程时不给）。"""
    sd = seeds(S, C)
    W = [bg.info(C[i:i + L]) for i, j, L in sd]
    sd, W, var = join_diagonal(sd, W)
    floor = seed_floor(len(S))
    keep = [q for q in range(len(sd)) if W[q] >= floor]
    vmap = {sd[q]: var[q] for q in keep if var[q]}
    chs = chain([sd[q] for q in keep], [W[q] for q in keep], len(S), len(C), **kw)
    for ch in chs:
        ch["anchors"] = [_pad(a) for a in ch["anchors"]]
        for a in ch["anchors"]:
            a[6] = vmap.get((a[0], a[1], a[2]), 0)
    merge_near(chs)
    refine(S, C, chs, bg, decoys)
    sup = support(chs, pre if pre is not None else char_info(S, bg), len(S))
    return chs, sup


# ---------------------------------------------------------------- 假经门槛
def thresholds(true_sup, decoy_sups, targets=(0.01, 0.10)):
    """true_sup: 真经上各锚点支持度；decoy_sups: 每部假经上的支持度列表。
    门槛 t 处估计 FDR = (假经上 ≥t 的锚点总数 + 1) / (假经部数 × 真经上 ≥t 的锚点数)；取"单调化"后的 q 值。
    返回 {target: 满足 q≤target 的最低门槛；没有则 None}。没有假经时返回 None（调用方不得出確层）。"""
    D = len(decoy_sups)
    if not D or not true_sup:
        return {t: None for t in targets}
    tv = sorted(true_sup, reverse=True)
    dv = sorted((x for d in decoy_sups for x in d), reverse=True)
    res, r, k = {t: None for t in targets}, 0, 0
    for t in sorted(set(tv) | set(dv) | {0.0}, reverse=True):   # 门槛由高到低；越低越宽
        while r < len(tv) and tv[r] >= t:
            r += 1
        while k < len(dv) and dv[k] >= t:
            k += 1
        if not r:
            continue
        fdr = (k + 1) / (D * r)
        for tg in targets:
            if fdr <= tg:
                res[tg] = t                                  # 记下满足目标的最宽门槛
    return res


COV_GRID = (0.9, 0.8, 0.7, 0.6, 0.5)


def thresholds_all(true_pairs, decoy_pairs, targets=(0.01, 0.10)):
    """两个判据合用，错误率仍由假经来量。
      判据一  支持度 ≥ t1（FDR≤1%）→ 確；≥ t10（FDR≤10%）→ 候。
      判据二  支持度只到候层、但局部覆盖度 ≥ c 的锚点也升为確——c 取使"两条合起来"的 FDR 仍 ≤1% 的最宽值（没有则不启用）。
    短牒文单看自己分量不够，但它前后的经文都被就近牒到（在逐句讲经），而假经上的零星相同做不到这一点。
    返回 {0.01: t1, 0.10: t10, "cov": c 或 None}。"""
    th = thresholds([p[0] for p in true_pairs], [[p[0] for p in d] for d in decoy_pairs], targets)
    th["cov"] = None
    t1, t10, D = th.get(targets[0]), th.get(targets[1]), len(decoy_pairs)
    if t10 is None or not D:
        return th
    hi = t1 if t1 is not None else float("inf")
    for c in COV_GRID:
        nt = sum(1 for s, v in true_pairs if s >= hi or (s >= t10 and s > 0 and v >= c))
        nd = sum(1 for d in decoy_pairs for s, v in d if s >= hi or (s >= t10 and s > 0 and v >= c))
        if nt and (nd + 1) / (D * nt) <= targets[0]:
            th["cov"] = c
        else:
            break
    return th


def tier_of(sup, th, cov=0.0):
    a, b, c = th.get(0.01), th.get(0.10), th.get("cov")
    if sup <= 0:
        return None
    if a is not None and sup >= a:
        return SURE
    if b is not None and sup >= b:
        return SURE if (c is not None and cov >= c) else CAND
    return None


# ---------------------------------------------------------------- 归属
def pseudo_bounds(lenS, step=8):
    return [(x, min(lenS, x + step)) for x in range(0, lenS, step)]


def _low(t1, t2):
    return SURE if t1 == SURE and t2 == SURE else CAND


def char_info(S, bg):
    """经文逐字信息量的前缀和（背景模型下的条件信息量）。通用套语（"阿耨多羅三藐三菩提"）几乎不占分量。"""
    out = np.zeros(len(S) + 1)
    for j in range(len(S)):
        out[j + 1] = out[j] + (bg.info(S[j - 1:j + 1]) - bg.info(S[j - 1]) if j else bg.info(S[0]))
    return out


def _cover(A, pre, lenS, near=3000, half=40, far=8000):
    """A: 一条链上的锚点 [(疏文位置 i, 经文位置 j, 经文长 Ls)]，按 i 排序。返回每个锚点的"局部覆盖度"：
    以它为中心、前后各 half 字的那段经文，按信息量计，有多大比例被它前后疏文内（near 字，或至少前后各两处，至多 far 字）的锚点牒到。
    逐句讲经的地方接近 1；别的文献只是引到本经几句通用的话，则很低。"""
    out = []
    pos = [a[0] for a in A]
    for k, (i, j, Ls) in enumerate(A):
        left = max(near, i - pos[max(0, k - 2)])
        right = max(near, pos[min(len(A) - 1, k + 2)] - i)
        lo = bisect.bisect_left(pos, i - min(left, far))
        hi_i = i + min(right, far)
        w0, w1 = max(0, j - half), min(lenS, j + Ls + half)
        tot = pre[w1] - pre[w0]
        if tot <= 0:
            out.append(0.0); continue
        iv = []
        z = lo
        while z < len(A) and A[z][0] <= hi_i:
            a0, a1 = max(w0, A[z][1]), min(w1, A[z][1] + A[z][2])
            if a1 > a0:
                iv.append((a0, a1))
            z += 1
        iv.sort()
        got, end = 0.0, w0
        for a0, a1 in iv:
            a0 = max(a0, end)
            if a1 > a0:
                got += pre[a1] - pre[a0]; end = a1
        out.append(float(got / tot))
    return out


def _led(A, pre, lenS, near, need=0.5):
    """確层锚点里"在逐句讲经"的那些（局部覆盖度 ≥ need，只按確层锚点算）。只有它们能带释文。"""
    return {k for k, c in enumerate(_cover(A, pre, lenS, near)) if c >= need}


def attribute(chs, C, bounds, th, para_starts=None, sinfo=None, reach=2000, reach_max=8000, tail_reach=300, near=3000):
    """返回 (segs, 字数统计)。seg = {c0,c1,s0,s1,kind,tier,chain[,var,weak,sup]}；kind ∈ 引／釋／參。"""
    lenC = len(C)
    starts = [b[0] for b in bounds]

    def cl(j):
        return max(0, min(len(bounds) - 1, bisect.bisect_right(starts, j) - 1))

    para_starts = para_starts or []
    quote = bytearray(lenC + 1)                               # 已作为引文的字
    gap_owner = np.zeros(lenC + 1, dtype=np.int32)            # 释文归哪条链（链号+1）
    segs, tiered = [], []
    for ci, ch in enumerate(chs):
        T = []
        for a in ch["anchors"]:
            t = tier_of(a["sup"], th, a["cov"])
            if t and a["weak"] == 1:
                t = CAND
            if t and not any(quote[a["i"]:a["i"] + a["L"]]):
                quote[a["i"]:a["i"] + a["L"]] = b"\x01" * a["L"]
                T.append((a, t))
        tiered.append(T)
    for ci, T in enumerate(tiered):
        def claim(g0, g1, s0, s1, tier, why=""):
            run = None
            for x in range(g0, g1 + 1):
                free = x < g1 and not quote[x] and not gap_owner[x]
                if free and run is None:
                    run = x
                if not free and run is not None:
                    tt, yy = (tier, why) if run - g0 < reach else (CAND, why or "遠")
                    gap_owner[run:x] = ci + 1
                    sg = {"c0": run, "c1": x, "s0": s0, "s1": s1, "kind": "釋", "tier": tt, "chain": ci}
                    if tt == CAND:
                        sg["why"] = yy                         # 为什么只到候层：跳（中间跳过了句）/ 中（下一处牒文从句中起，句首可能漏认）/ 結（结引）/ 弱（旁边的锚点只到候层）/ 遠 / 回 / 尾
                    if yy == "跳" or yy == "結":
                        sg["amb"] = 1
                    segs.append(sg)
                    run = None
        sure = [(a["i"], a["j"], a["Ls"]) for a, t in T if t == SURE]
        lenS = bounds[-1][1]
        pre = sinfo if sinfo is not None else np.arange(lenS + 1, dtype=float)
        led_k = _led(sure, pre, lenS, near) if sure else set()
        led = {sure[k][:2] for k in led_k}
        cur, amb_hi = None, -1                                 # 当前所讲的经句（只由"在逐句讲经"的確层锚点确立）；其后别的锚点指到的最远句
        for q, (a, t) in enumerate(T):
            ka, ke = cl(a["j"]), cl(a["j"] + a["Ls"] - 1)
            if t == SURE and (a["i"], a["j"]) not in led:
                t = CAND
            if t == SURE and not (cur is not None and a["weak"] and ka < cur[0]):
                cur, amb_hi = (ka, ke), -1                     # 短牒文往回指：不改变"当前句"
            elif cur is not None and ke > cur[1]:
                amb_hi = max(amb_hi, ke)
            if cur is None:
                continue                                       # 还没有这样的锚点：释文不归属
            g0 = a["i"] + a["L"]
            if q + 1 < len(T):
                b, tb = T[q + 1]
                if tb == SURE and (b["i"], b["j"]) not in led:
                    tb = CAND
                g1 = min(b["i"], g0 + reach_max)
                if g1 <= g0:
                    continue
                kb = cl(b["j"])
                tail = any(C[max(0, b["i"] - 4):b["i"]].endswith(m) for m in TAIL_MARKS)
                if amb_hi > cur[1]:
                    claim(g0, g1, bounds[cur[0]][0], bounds[amb_hi][1], CAND, "弱")
                elif b["weak"] and kb < cur[0]:
                    claim(g0, g1, bounds[cur[0]][0], bounds[cur[1]][1], SURE)
                elif cur[0] <= kb <= cur[1] and not tail:
                    claim(g0, g1, bounds[cur[0]][0], bounds[cur[1]][1], _low(SURE, tb), "" if tb == SURE else "弱")
                elif kb == cur[1] + 1 and not tail:
                    head = b["j"] - bounds[kb][0] <= HEAD_SLACK  # 下一处牒文从下一句句首起：先牒后释，确定
                    claim(g0, g1, bounds[cur[0]][0], bounds[cur[1]][1],
                          _low(SURE, tb) if head else CAND, ("" if tb == SURE else "弱") if head else "中")
                elif kb > cur[1]:
                    hi = kb if tail else kb - 1
                    claim(g0, g1, bounds[cur[0]][0], bounds[hi][1], CAND, "結" if tail else "跳")
                else:
                    claim(g0, g1, bounds[cur[0]][0], bounds[cur[1]][1], CAND, "回")
            else:
                end = min(lenC, g0 + tail_reach)
                if para_starts:
                    p = bisect.bisect_right(para_starts, g0)
                    end = min(lenC, para_starts[p] if p < len(para_starts) else lenC, g0 + reach)
                hi = max(cur[1], amb_hi)
                claim(g0, end, bounds[cur[0]][0], bounds[hi][1], CAND, "尾")   # 链的最后一处引文之后：没有下一处牒文收口，只到候层
    for ci, T in enumerate(tiered):
        for a, t in T:
            i, L = a["i"], a["L"]
            near = int(gap_owner[i - 1]) if i else 0
            near = near or (int(gap_owner[i + L]) if i + L < lenC else 0)
            kind = "參" if (near and near != ci + 1) else "引"
            sg = {"c0": i, "c1": i + L, "s0": a["j"], "s1": a["j"] + a["Ls"], "kind": kind, "tier": t, "chain": ci,
                  "sup": round(a["sup"], 1)}
            if a["var"]:
                sg["var"] = a["var"]
            if a["weak"]:
                sg["weak"] = 1
            segs.append(sg)
    segs.sort(key=lambda s: (s["c0"], s["c1"]))
    cnt = collections.Counter()
    for s in segs:
        cnt[s["tier"]] += s["c1"] - s["c0"]
        cnt[s["kind"] + s["tier"]] += s["c1"] - s["c0"]
        if s.get("why"):
            cnt["why" + s["why"]] += s["c1"] - s["c0"]
    return segs, cnt


def loo_exam(chs, segs, th, bounds):
    """漏牒考（确定性的敏感度检验）：假装某一处確层引文 a 没被认出——它前一处 p 的释文就会一直延伸到后一处 b。
    比较 a 之后那段释文的实际归属，与"没有 a 时"它会得到的归属（即 p 之后那段的归属）：两者都是確层而所指的句子不相交，记一次错。
    （没有 a 时只会到候层的情形不计。）返回 (考的锚点数, 归错数)：量的是"万一漏认一处牒文，確层释文被归错的比例"。"""
    starts = [b[0] for b in bounds]

    def cl(j):
        return max(0, min(len(bounds) - 1, bisect.bisect_right(starts, j) - 1))
    gl = {s["c0"]: s for s in segs if s["kind"] in ("釋", "略")}
    n = wrong = 0
    for ch in chs:
        T = [a for a in ch["anchors"] if not a["weak"] and tier_of(a["sup"], th, a.get("cov", 0)) == SURE]
        for q in range(1, len(T) - 1):
            p, a, b = T[q - 1], T[q], T[q + 1]
            sp, sa = gl.get(p["i"] + p["L"]), gl.get(a["i"] + a["L"])
            if not sp or not sa or sp["tier"] != SURE or sa["tier"] != SURE:
                continue
            c0, c1 = cl(sp["s0"]), cl(sp["s1"] - 1)
            kb = cl(b["j"])
            if not (c0 <= kb <= c1 or (kb == c1 + 1 and b["j"] - bounds[kb][0] <= HEAD_SLACK)):
                continue                                      # 没有 a 时这一段只会到候层
            n += 1
            d0, d1 = cl(sa["s0"]), cl(sa["s1"] - 1)
            wrong += d1 < c0 or d0 > c1
    return n, wrong


def by_clause(segs, bounds):
    """经文每个小句上归属的段落下标列表。"""
    starts = [b[0] for b in bounds]
    res = [[] for _ in bounds]
    for k, s in enumerate(segs):
        q = max(0, bisect.bisect_right(starts, s["s0"]) - 1)
        while q < len(bounds) and bounds[q][0] < s["s1"]:
            if bounds[q][1] > s["s0"]:
                res[q].append(k)
            q += 1
    return res


def stats(chs, segs, cnt, bounds, lenS, lenC):
    """整体形状（供关系判别与报表；只给指标，不下结论）。"""
    covS = bytearray(lenS + 1)
    n_sure = n_cand = 0
    for s in segs:
        if s["kind"] in ("引", "參"):
            if s["tier"] == SURE:
                covS[s["s0"]:s["s1"]] = b"\x01" * (s["s1"] - s["s0"]); n_sure += 1
            else:
                n_cand += 1
    main = [s for s in segs if s["kind"] == "引" and s["tier"] == SURE]
    top = collections.Counter(s["chain"] for s in main).most_common(1)
    cl_hit = sum(1 for b in bounds if any(covS[b[0]:b[1]]))
    return {"chains": len(chs), "top": round(chs[0]["score"], 1) if chs else 0.0,
            "anchors_sure": n_sure, "anchors_cand": n_cand,
            "cover_S": round(sum(covS) / max(1, lenS), 4), "clauses_hit": cl_hit, "clauses": len(bounds),
            "order": round(top[0][1] / len(main), 4) if main else 0.0,
            "scale": round(lenC / max(1, lenS), 2),
            "frac_sure": round(cnt[SURE] / max(1, lenC), 4), "frac_cand": round(cnt[CAND] / max(1, lenC), 4),
            "quote_sure": cnt["引" + SURE] + cnt["參" + SURE], "gloss_sure": cnt["釋" + SURE],
            "gloss_frac": round(cnt["釋" + SURE] / max(1, lenC), 4),
            "why": {k[3:]: v for k, v in sorted(cnt.items()) if k.startswith("why")}}


# ---------------------------------------------------------------- 总入口
def grams(texts, k=12):
    """若干文本的全部 k 字片段（取哈希，省内存）。"""
    out = set()
    for t in texts:
        for x in range(len(t) - k + 1):
            out.add(hash(t[x:x + k]))
    return out


def decoy_windows(texts, size, n=8, avoid=None, cited=None, common=12, max_cited=1, slots_per_text=40, report=None):
    """从无关文献里按固定步长取 n 个长度为 size 的窗口作假经。两种窗口不要：
      与真经有 ≥common 字相同片段的（那是真平行文）；
      与本族注疏有 > max_cited 处 ≥common 字相同片段的（注家引过这段，它就不是"无关"的）。
    cited 是本族注疏的 grams()。各文献轮流取，确定性。report 给一个 dict 就记下取舍数。"""
    ga = grams([avoid], common) if avoid and len(avoid) >= common else set()
    pool, drop_s, drop_c = [], 0, 0
    for t in texts:
        if len(t) < size:
            continue
        slots = max(1, min(slots_per_text, len(t) // size))
        step = (len(t) - size) // slots if slots > 1 else 0
        for z in range(slots):
            w = t[z * step:z * step + size]
            hs = [hash(w[k:k + common]) for k in range(0, len(w) - common + 1)]
            if ga and any(h in ga for h in hs):
                drop_s += 1; continue
            if cited is not None and len({h for h in hs if h in cited}) > max_cited:
                drop_c += 1; continue
            pool.append((z, w))
    pool.sort(key=lambda p: p[0])
    if report is not None:
        report.update({"kept": min(n, len(pool)), "pool": len(pool), "drop_parallel": drop_s, "drop_cited": drop_c})
    return [w for _, w in pool[:n]]


def locate(S, C, bounds=None, decoys=(), bg=None, th=None, para_starts=None, sinfo=None):
    """th 给定则用它（整族汇总后的门槛）；否则用本疏自己的真假分布求门槛。没有假经又没有 th：一律不出（空结果）。"""
    bg = bg or Background([S, C])
    bounds = bounds or pseudo_bounds(len(S))
    sinfo = sinfo if sinfo is not None else char_info(S, bg)
    chs, sup = pipeline(S, C, bg, decoys, sinfo)
    dsup = [pipeline(D, C, bg)[1] for D in decoys]
    return finish({"chains": chs, "sup_true": sup, "sup_decoy": dsup}, S, C, bounds, th, para_starts, sinfo)


def finish(res, S, C, bounds, th=None, para_starts=None, sinfo=None):
    th = th or thresholds_all(res["sup_true"], res["sup_decoy"])
    segs, cnt = attribute(res["chains"], C, bounds, th, para_starts, sinfo)
    res.update({"th": th, "segs": segs, "clauses": by_clause(segs, bounds),
                "stats": stats(res["chains"], segs, cnt, bounds, len(S), len(C))})
    return res
