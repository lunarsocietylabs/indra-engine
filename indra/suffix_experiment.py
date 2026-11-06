# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lunar Society Technologies
"""
suffix_experiment.py — 因陀罗网引擎 · 实验一（纯确定性，零模型）

问题：不靠引号、不靠登记表，能否从全文本身找出"疏文在引/释哪部经的哪一句"，
     且误判率可以不靠人工、由数据自己测出来？

方法：
  1. 把若干"族"（心经族、法华族、阿含背景）拼成一条字符流，每部文献以独立的
     私用区字符结尾（保证匹配不跨文献），用前缀倍增建后缀数组（SA），Kasai 求 LCP。
  2. 匹配统计（matching statistics）：对疏文每个位置，求它与目标经文任意位置的最长
     公共前缀长度 ms[p]——一次 SA 扫描得到全部位置，不需要任何切词、引号或配对。
  3. 取"极大匹配段"为候选；规则只用 长度 / 经内出现次数 / 背景（阿含）中是否出现。
  4. 零模型噪声估计：用"错配"（心经疏 对 法华经、法华疏 对 心经）跑同一套规则，
     得到每万字的偶然命中密度，作为同一目标经文上的误判基线 → 估计精确率。
  5. 引号考卷：疏文里用「」明引、且确在经中出现的片段，作为现成标准答案测召回。
  6. 段落锚定 + 单调插值：有命中的段落是"锚"，两锚之间、经文位置单调的段落推定为
     在注这一区间 → 估计"多少疏文段落能挂到经文上"。
  7. 附：任意片段 SA 查询延迟；LCP 区间（重复片段因子）数与"两两连边"数之比。

用法：
  python3 -m indra.suffix_experiment --selftest
  python3 -m indra.suffix_experiment --out /tmp/indra     # runner 上，可访问站点
"""
import argparse, bisect, collections, json, os, random, re, sys, time, unicodedata
import numpy as np

from .norm import norm

SITE = os.environ.get("INDRA_SITE", "")   # base URL of a corpus server exposing /data/manifest.json and /data/search/<id>.json
SEP0 = 0xE000                      # 私用区：每部文献一个独有的结尾符


# ------------------------------------------------------------------ 后缀数组 / LCP
def suffix_array(text):
    codes = np.frombuffer(text.encode("utf-32-le"), dtype=np.uint32).astype(np.int64)
    n = len(codes)
    rank = codes.copy()
    k = 1
    while True:
        key2 = np.full(n, -1, np.int64)
        if k < n:
            key2[:n - k] = rank[k:]
        sa = np.lexsort((key2, rank))
        r1, r2 = rank[sa], key2[sa]
        diff = np.empty(n, bool)
        diff[0] = False
        diff[1:] = (r1[1:] != r1[:-1]) | (r2[1:] != r2[:-1])
        newr = np.cumsum(diff)
        rank = np.empty(n, np.int64)
        rank[sa] = newr
        if newr[-1] == n - 1 or k > n:
            return sa
        k *= 2


def kasai(text, sa):
    n = len(sa)
    rank = [0] * n
    for i, s in enumerate(sa):
        rank[s] = i
    lcp = [0] * (n + 1)              # lcp[r] = LCP(sa[r-1], sa[r])；lcp[n]=0 哨兵
    h = 0
    for i in range(n):
        r = rank[i]
        if r > 0:
            j = sa[r - 1]
            while i + h < n and j + h < n and text[i + h] == text[j + h]:
                h += 1
            lcp[r] = h
            if h > 0:
                h -= 1
        else:
            h = 0
    return lcp, rank


def matching_stats(sa, lcp, a, b):
    """对每个文本位置 p，求与目标区间 [a,b) 内任一后缀的最长公共前缀；返回 (ms, tpos)。"""
    n = len(sa)
    BIG = 1 << 30
    is_t = [a <= s < b for s in sa]
    msf = [0] * n; tpf = [-1] * n
    cur, have, last = 0, False, -1
    for k in range(n):
        if have:
            l = lcp[k]
            if l < cur:
                cur = l
        if is_t[k]:
            have, cur, last = True, BIG, sa[k]
        elif have:
            msf[k] = cur; tpf[k] = last
    msb = [0] * n; tpb = [-1] * n
    cur, have, last = 0, False, -1
    for k in range(n - 1, -1, -1):
        if have:
            l = lcp[k + 1]
            if l < cur:
                cur = l
        if is_t[k]:
            have, cur, last = True, BIG, sa[k]
        elif have:
            msb[k] = cur; tpb[k] = last
    ms = [0] * n; tp = [-1] * n
    for k in range(n):
        p = sa[k]
        if msf[k] >= msb[k]:
            ms[p], tp[p] = msf[k], tpf[k]
        else:
            ms[p], tp[p] = msb[k], tpb[k]
    return ms, tp


def sa_range(text, sa, q):
    """q 在全文中的出现区间 [lo,hi)（SA 二分）。"""
    m = len(q)
    lo, hi = 0, len(sa)
    while lo < hi:
        mid = (lo + hi) // 2
        if text[sa[mid]:sa[mid] + m] < q: lo = mid + 1
        else: hi = mid
    start, hi = lo, len(sa)
    while lo < hi:
        mid = (lo + hi) // 2
        if text[sa[mid]:sa[mid] + m] <= q: lo = mid + 1
        else: hi = mid
    return start, lo


def lcp_interval_stats(lcp, n, minlen):
    """枚举 LCP 区间（后缀树内部结点）：每个 = 一个重复片段因子。"""
    nodes = occ = pairs = 0
    st = [(0, 0)]
    for i in range(1, n + 1):
        l = lcp[i] if i < n else 0
        lb = i - 1
        while l < st[-1][0]:
            top_l, top_lb = st.pop()
            size = i - top_lb
            if top_l >= minlen:
                nodes += 1; occ += size; pairs += size * (size - 1) // 2
            lb = top_lb
        if l > st[-1][0]:
            st.append((l, lb))
    return nodes, occ, pairs


# ------------------------------------------------------------------ 语料
class Corpus:
    def __init__(self, docs):
        """docs: [{id, fam, title, author, paras:[(lb, raw)]}]"""
        parts, self.docs, pos = [], [], 0
        for i, d in enumerate(docs):
            pstarts, plbs, praw = [], [], []
            buf = []
            for lb, raw in d["paras"]:
                n = norm(raw)
                if not n:
                    continue
                pstarts.append(pos + sum(len(x) for x in buf)); plbs.append(lb); praw.append(raw)
                buf.append(n)
            body = "".join(buf)
            self.docs.append(dict(d, start=pos, end=pos + len(body), pstarts=pstarts, plbs=plbs, praw=praw))
            parts.append(body); parts.append(chr(SEP0 + i))
            pos += len(body) + 1
        self.text = "".join(parts)
        self.dstarts = [d["start"] for d in self.docs]

    def doc_of(self, p):
        return self.docs[bisect.bisect_right(self.dstarts, p) - 1]

    def para_of(self, d, p):
        return bisect.bisect_right(d["pstarts"], p) - 1


def fetch_docs():
    import requests, concurrent.futures
    if not SITE:
        raise SystemExit('set INDRA_SITE to a corpus server base URL')
    man = requests.get(f"{SITE}/data/manifest.json", timeout=180).json()
    allm = man.get("docs", man) if isinstance(man, dict) else man
    meta = {d["doc_id"]: d for d in allm if d.get("doc_id")}
    def is_heart_com(d):
        t = d.get("title") or ""
        return d.get("class") in ("commentary", "treatise") and "梵本" not in t and (("般若" in t and "心經" in t) or "毒語心經" in t)
    plan = [("T08n0251", "heart_sutra"), ("T09n0262", "lotus_sutra")]
    plan += [(d, "heart_com") for d in sorted(meta) if is_heart_com(meta[d])]
    plan += [(d, "lotus_com") for d in ("T34n1718", "T34n1719", "T34n1721", "T34n1723")]
    plan += [(d, "bg") for d in ("T01n0001", "T02n0099")]            # 专属性背景（长阿含、杂阿含）
    plan += [(d, "null") for d in ("T01n0026", "T02n0125")]          # 零模型对照（中阿含、增一阿含）：不可能引用大乘经
    plan += [(d, "comp") for d in ("T08n0223", "T08n0235", "T10n0279", "T12n0374", "T14n0475", "T25n1509")]  # 别部经论
    def one(item):
        did, fam = item
        r = requests.get(f"{SITE}/data/search/{did}.json", timeout=300)
        if r.status_code != 200 or r.headers.get("content-type", "").startswith("text/html"):
            return None
        paras = [(p.get("lb0") or "", p.get("t") or "") for p in r.json().get("paragraphs", []) if p.get("t")]
        m = meta.get(did, {})
        return {"id": did, "fam": fam, "title": re.sub(r"^.*No\. *[0-9A-Za-z]+ *", "", m.get("title") or ""),
                "author": m.get("author") or "", "paras": paras}
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        docs = [d for d in ex.map(one, plan) if d]
    order = {"heart_sutra": 0, "lotus_sutra": 1, "comp": 2, "heart_com": 3, "lotus_com": 4, "bg": 5, "null": 6}
    docs.sort(key=lambda d: order[d["fam"]])                        # comp 连续存放，便于一次匹配统计
    # 《心经》T08n0251 卷首有明代御制序：经文正文自「觀自在菩薩」起（实验内硬切，见报告说明）
    for d in docs:
        if d["id"] == "T08n0251":
            for i, (lb, t) in enumerate(d["paras"]):
                if "觀自在菩薩" in t:
                    j = t.index("觀自在菩薩")
                    d["paras"] = [(lb, t[j:])] + d["paras"][i + 1:]
                    break
    return docs


# ------------------------------------------------------------------ 实验
RULES = {
    "L≥8": lambda m, occ, bg: m >= 8,
    "L≥6": lambda m, occ, bg: m >= 6,
    "L≥4": lambda m, occ, bg: m >= 4,
    "L≥5+专属": lambda m, occ, bg: m >= 8 or (m >= 5 and bg == 0 and occ <= 3),
    "L≥4+专属": lambda m, occ, bg: m >= 8 or (m >= 4 and bg == 0 and occ <= 3),
}


def spans_for(C, ms, tp, fam, target):
    tstr = C.text[target["start"]:target["end"]]
    out = []
    for d in C.docs:
        if d["fam"] != fam:
            continue
        for p in range(d["start"], d["end"]):
            m = min(ms[p], d["end"] - p)
            if m < 4:
                continue
            if p > d["start"] and min(ms[p - 1], d["end"] - p + 1) >= m + 1:
                continue                                   # 被前一位置的匹配包含，不是极大段
            s = C.text[p:p + m]
            occ, j = [], tstr.find(s)
            while j >= 0 and len(occ) < 50:
                occ.append(j); j = tstr.find(s, j + 1)
            if not occ:
                continue
            out.append({"doc": d, "p": p, "m": m, "s": s, "occ": occ})
    return out


def run(outdir, docs=None):
    os.makedirs(outdir, exist_ok=True)
    t0 = time.time()
    docs = docs or fetch_docs()
    C = Corpus(docs)
    fam_n = collections.Counter(d["fam"] for d in C.docs)
    fam_chars = collections.Counter()
    for d in C.docs:
        fam_chars[d["fam"]] += d["end"] - d["start"]
    N = len(C.text)
    t1 = time.time(); sa = suffix_array(C.text).tolist(); t_sa = time.time() - t1
    t1 = time.time(); lcp, rank = kasai(C.text, sa); t_lcp = time.time() - t1
    bg = "\x00".join(C.text[d["start"]:d["end"]] for d in C.docs if d["fam"] == "bg")
    T = {d["fam"]: d for d in C.docs if d["fam"] in ("heart_sutra", "lotus_sutra")}
    R = {"corpus": {"chars": N, "docs": dict(fam_n), "fam_chars": dict(fam_chars), "t_sa_s": round(t_sa, 1), "t_lcp_s": round(t_lcp, 1)}}

    comp = [d for d in C.docs if d["fam"] == "comp"]
    R["corpus"]["comp"] = [f"{d['id']} {d['title']}" for d in comp]
    msc = None
    if comp:
        msc, _ = matching_stats(sa, lcp, comp[0]["start"], comp[-1]["end"])

    def attribution(x):
        """span 在别部经中是否有**更长**的包含匹配 → 'other'；等长包含 → 'shared'；否则 'own'。"""
        if msc is None:
            return "own"
        d = x["doc"]; p, m = x["p"], x["m"]
        pi = C.para_of(d, p)
        pst = d["pstarts"][pi]
        pen = d["pstarts"][pi + 1] if pi + 1 < len(d["pstarts"]) else d["end"]
        best = "own"
        for q in range(p, max(pst, p - 40) - 1, -1):
            L = min(msc[q], pen - q)
            if q + L >= p + m:
                if L > m:
                    return "other"
                best = "shared"
        return best

    pair_spans = {}
    for tname, target in T.items():
        ms, tp = matching_stats(sa, lcp, target["start"], target["end"])
        for fam in ("heart_com", "lotus_com", "null"):
            sp = spans_for(C, ms, tp, fam, target)
            for x in sp:
                x["bg"] = bg.count(x["s"]) if x["m"] < 8 else 0
                x["attr"] = attribution(x)
            pair_spans[(fam, tname)] = sp

    def add_chain(spans, target):
        W = max(200, int(0.03 * (target["end"] - target["start"])))
        by = collections.defaultdict(list)
        for x in spans:
            by[x["doc"]["id"]].append(x)
        for xs in by.values():
            xs.sort(key=lambda x: x["p"])
            strong = [i for i, x in enumerate(xs) if x["m"] >= 8 and len(x["occ"]) == 1 and x.get("attr") != "other"]
            sp = [xs[i]["p"] for i in strong]
            for i, x in enumerate(xs):
                k = bisect.bisect_left(sp, x["p"])
                nb = [xs[strong[j]]["occ"][0] for j in range(max(0, k - 4), min(len(strong), k + 5)) if strong[j] != i]
                if len(nb) < 2:
                    x["chain"], x["pos"] = None, x["occ"][0]; continue
                med = sorted(nb)[len(nb) // 2]
                pos = min(x["occ"], key=lambda o: abs(o - med))
                x["pos"] = pos
                x["chain"] = abs(pos - med) <= W
    for (fam, tname), sp in pair_spans.items():
        add_chain(sp, T[tname])
    RULES["L≥4+专属+链"] = lambda m, occ, bg: m >= 8 or (m >= 4 and bg == 0 and occ <= 3)

    correct = {"heart_sutra": "heart_com", "lotus_sutra": "lotus_com"}
    null = {"heart_sutra": "lotus_com", "lotus_sutra": "heart_com"}
    from .short_quote import extract_fragments

    table, gold_report = [], {}
    for tname, target in T.items():
        tstr = C.text[target["start"]:target["end"]]
        cf, nf = correct[tname], null[tname]
        # 引号考卷
        gold, gold_cover = [], {}
        for d in C.docs:
            if d["fam"] != cf:
                continue
            for i, raw in enumerate(d["praw"]):
                pn = norm(raw)
                for f, src, quoted in extract_fragments(raw):
                    if src != "quote" or f not in tstr:
                        continue
                    j = pn.find(f)
                    if j >= 0:
                        gold.append((d["id"], d["pstarts"][i] + j, len(f)))
        gold_mask = {d["id"]: bytearray(d["end"] - d["start"]) for d in C.docs if d["fam"] == cf}
        for did, g, L in gold:
            st = C.doc_of(g)["start"]
            gold_mask[did][g - st:g - st + L] = b"\x01" * L
        for attr_mode in ("不过滤", "去别部经", "去别部经+顺序链"):
            keep = {"不过滤": lambda x: True,
                    "去别部经": lambda x: x["attr"] != "other",
                    "去别部经+顺序链": lambda x: x["attr"] != "other" and x.get("chain") is True}[attr_mode]
            for rname, rule in RULES.items():
                ok = lambda x: rule(x["m"], len(x["occ"]), x["bg"]) and keep(x)
                acc_c = [x for x in pair_spans[(cf, tname)] if ok(x)]
                acc_n = [x for x in pair_spans[(nf, tname)] if ok(x)]
                acc_a = [x for x in pair_spans[("null", tname)] if ok(x)]
                dens_c = len(acc_c) / max(1, fam_chars[cf]) * 1e4
                dens_n = len(acc_n) / max(1, fam_chars[nf]) * 1e4
                dens_a = len(acc_a) / max(1, fam_chars["null"]) * 1e4
                cover = {did: bytearray(len(v)) for did, v in gold_mask.items()}
                extra = 0
                for x in acc_c:
                    st = x["doc"]["start"]; a = x["p"] - st
                    cover[x["doc"]["id"]][a:a + x["m"]] = b"\x01" * x["m"]
                    if b"\x01" not in gold_mask[x["doc"]["id"]][a:a + x["m"]]:
                        extra += 1
                hit = 0
                for did, g, L in gold:
                    st = C.doc_of(g)["start"]
                    if b"\x00" not in cover[did][g - st:g - st + L]:
                        hit += 1
                pa = max(0.0, 1 - dens_a / dens_c) if dens_c else 0.0
                pn = max(0.0, 1 - dens_n / dens_c) if dens_c else 0.0
                table.append({"target": tname, "attr": attr_mode, "rule": rname, "accepted": len(acc_c),
                              "per10k": round(dens_c, 2), "agama_per10k": round(dens_a, 2), "est_precision": round(pa, 3),
                              "cross_per10k": round(dens_n, 2), "cross_precision_lb": round(pn, 3),
                              "gold": len(gold), "gold_recall": round(hit / max(1, len(gold)), 3), "unquoted_new": extra})
        attr_cnt = collections.Counter(x["attr"] for x in pair_spans[(cf, tname)] if RULES["L≥4+专属"](x["m"], len(x["occ"]), x["bg"]))
        R.setdefault("attribution", {})[tname] = dict(attr_cnt)
        R.setdefault("other_samples", {})[tname] = [
            {"doc": x["doc"]["id"], "len": x["m"], "text": x["s"], "ctx": ctx(x["doc"]["praw"][C.para_of(x["doc"], x["p"])], x["s"])}
            for x in pair_spans[(cf, tname)] if x["attr"] == "other" and RULES["L≥4+专属"](x["m"], len(x["occ"]), x["bg"])][:8]
        # ---- miss analysis: 为什么这些明引片段没被找回（规则 L≥4+专属，去别部经+顺序链）
        spans_all = pair_spans[(cf, tname)]
        acc = [x for x in spans_all if RULES["L≥4+专属"](x["m"], len(x["occ"]), x["bg"]) and x["attr"] != "other" and x.get("chain") is True]
        mask = {did: bytearray(len(v)) for did, v in gold_mask.items()}
        for x in acc:
            st = x["doc"]["start"]; a_ = x["p"] - st
            mask[x["doc"]["id"]][a_:a_ + x["m"]] = b"\x01" * x["m"]
        by_doc_sp = collections.defaultdict(list)
        for x in spans_all:
            by_doc_sp[x["doc"]["id"]].append(x)
        for v in by_doc_sp.values():
            v.sort(key=lambda x: x["p"])
        reasons, bylen, msamples = collections.Counter(), collections.defaultdict(collections.Counter), collections.defaultdict(list)
        for did, g, L_ in gold:
            st = C.doc_of(g)["start"]
            if b"\x00" not in mask[did][g - st:g - st + L_]:
                continue
            xs = by_doc_sp.get(did, [])
            ps = [x["p"] for x in xs]
            k = bisect.bisect_right(ps, g) - 1
            cov = None
            for j in range(max(0, k - 4), min(len(xs), k + 5)):
                if xs[j]["p"] <= g and xs[j]["p"] + xs[j]["m"] >= g + L_:
                    cov = xs[j]; break
            if cov is None:
                why = "no_cover"
            elif cov["attr"] == "other":
                why = "other_sutra"
            elif cov["m"] < 8 and cov["bg"] > 0:
                why = "agama_common"
            elif cov["m"] < 8 and len(cov["occ"]) > 3:
                why = "occ>3"
            elif cov.get("chain") is None:
                why = "chain_no_anchor"
            elif cov.get("chain") is False:
                why = "chain_far"
            else:
                why = "other"
            reasons[why] += 1
            bk = "4" if L_ == 4 else "5" if L_ == 5 else "6" if L_ == 6 else "7" if L_ == 7 else "8-11" if L_ < 12 else "12+"
            bylen[bk][why] += 1
            if len(msamples[why]) < 6:
                d_ = C.doc_of(g)
                pi_ = C.para_of(d_, g)
                msamples[why].append({"doc": did, "lb": d_["plbs"][pi_], "len": L_, "occ": len(cov["occ"]) if cov else 0,
                                      "text": C.text[g:g + L_], "ctx": ctx(d_["praw"][pi_], C.text[g:g + L_])})
        R.setdefault("miss", {})[tname] = {"total_missed": sum(reasons.values()), "reasons": dict(reasons),
                                           "by_len": {k_: dict(v_) for k_, v_ in sorted(bylen.items())}, "samples": dict(msamples)}
        gold_report[tname] = len(gold)
    R["rules"] = table

    # 锚定 + 消歧 + 插值（规则 L≥4+专属，正确配对）
    rule = RULES["L≥4+专属"]
    cov_rows, samples, multi_stats = [], {}, collections.Counter()
    for tname, target in T.items():
        sp = [x for x in pair_spans[(correct[tname], tname)] if rule(x["m"], len(x["occ"]), x["bg"]) and x["attr"] != "other" and x.get("chain") is True]
        by_doc = collections.defaultdict(list)
        for x in sp:
            by_doc[x["doc"]["id"]].append(x)
        for d in C.docs:
            if d["fam"] != correct[tname]:
                continue
            xs = sorted(by_doc.get(d["id"], []), key=lambda x: x["p"])
            last, anchor = -1, {}
            for x in xs:
                if True:
                    pos = x["pos"]
                    if len(x["occ"]) > 1:
                        multi_stats["resolved_by_chain"] += 1
                elif len(x["occ"]) == 1:
                    pos = x["occ"][0]
                else:
                    fwd = [o for o in x["occ"] if o >= last]
                    pos = min(fwd) if fwd else min(x["occ"], key=lambda o: abs(o - last))
                    multi_stats["resolved_by_order" if fwd else "resolved_by_nearest"] += 1
                last = pos
                pi = C.para_of(d, x["p"])
                anchor.setdefault(pi, pos)
            npara = len(d["pstarts"])
            keys = sorted(anchor)
            interp = 0
            for a_, b_ in zip(keys, keys[1:]):
                if b_ - a_ > 1 and b_ - a_ <= 20 and anchor[a_] <= anchor[b_]:
                    interp += b_ - a_ - 1
            cov_rows.append({"target": tname, "doc": d["id"], "title": d["title"], "author": d["author"],
                             "paras": npara, "anchored": len(anchor), "interpolated": interp,
                             "coverage": round((len(anchor) + interp) / max(1, npara), 3)})
            # 无引号新增命中的样例（每部最多 3 条，带上下文）
            ex = []
            for x in xs:
                if len(ex) >= 3:
                    break
                pi = C.para_of(d, x["p"])
                raw = d["praw"][pi]
                if "「" + x["s"][:2] in norm_keep(raw):
                    continue
                ex.append({"lb": d["plbs"][pi], "len": x["m"], "text": x["s"], "ctx": ctx(raw, x["s"])})
            if ex:
                samples[d["id"]] = ex
    R["coverage"] = cov_rows
    R["disambiguation"] = dict(multi_stats)
    R["samples_unquoted"] = samples

    # 零模型误判样例（错配：法华疏 → 心经）
    fp = [x for x in pair_spans[("lotus_com", "heart_sutra")] if rule(x["m"], len(x["occ"]), x["bg"])]
    R["null_samples"] = [{"doc": x["doc"]["id"], "len": x["m"], "text": x["s"]} for x in fp[:15]]

    # 任意片段查询延迟（SA 二分，纯 Python）
    rnd = random.Random(7)
    coms = [d for d in C.docs if d["fam"] in ("heart_com", "lotus_com") and d["end"] - d["start"] > 20]
    lat, occs = [], []
    for _ in range(2000):
        d = rnd.choice(coms); L = rnd.randint(4, 8)
        p = rnd.randint(d["start"], d["end"] - L)
        q = C.text[p:p + L]
        t1 = time.perf_counter(); lo, hi = sa_range(C.text, sa, q); lat.append((time.perf_counter() - t1) * 1e3)
        occs.append(hi - lo)
    lat.sort()
    R["latency_ms"] = {"p50": round(lat[1000], 4), "p99": round(lat[1980], 4), "mean_occ": round(sum(occs) / len(occs), 1)}

    nodes, occ, pairs = lcp_interval_stats(lcp, N, 8)
    R["factors_minlen8"] = {"nodes": nodes, "factor_edges": occ, "pairwise_edges": pairs,
                            "ratio": round(pairs / max(1, occ), 1)}
    R["elapsed_s"] = round(time.time() - t0, 1)
    json.dump(R, open(os.path.join(outdir, "results.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    md = report(R)
    open(os.path.join(outdir, "report.md"), "w", encoding="utf-8").write(md)
    print(md)
    return R


def norm_keep(raw):
    return re.sub(r"\s", "", unicodedata.normalize("NFKC", raw))


def ctx(raw, frag, w=30):
    nt, idx = "", []
    for i, ch in enumerate(raw):
        for c in norm(ch):
            nt += c; idx.append(i)
    j = nt.find(frag)
    if j < 0:
        return raw[:2 * w]
    a, b = idx[j], idx[j + len(frag) - 1] + 1
    return raw[max(0, a - w):a] + "【" + raw[a:b] + "】" + raw[b:b + w]


def report(R):
    c = R["corpus"]
    L = ["# 因陀罗网引擎 · 实验一：后缀数组 + 匹配统计（零模型）", "",
         f"- 语料 {c['chars']:,} 字；文献 {c['docs']}；建 SA {c['t_sa_s']}s，LCP {c['t_lcp_s']}s；总耗时 {R['elapsed_s']}s", "",
         f"- 别部经论（竞争集）：{c.get('comp')}", "",
         "## 规则对比", "",
         "估计精确率 = 1 − 阿含对照命中密度 / 正配命中密度（阿含不可能引用大乘经，命中即偶然或套语）。",
         "交叉下界 = 用另一族疏文作对照（含真实互引，所以偏低，只作下界）。", "",
         "| 目标经 | 别部经过滤 | 规则 | 命中段 | 每万字 | 阿含每万字 | 估计精确率 | 交叉每万字 | 交叉下界 | 考卷题数 | 召回 | 无引号新增 |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in R["rules"]:
        L.append(f"| {r['target']} | {r['attr']} | {r['rule']} | {r['accepted']} | {r['per10k']} | {r['agama_per10k']} | {r['est_precision']} | {r['cross_per10k']} | {r['cross_precision_lb']} | {r['gold']} | {r['gold_recall']} | {r['unquoted_new']} |")
    L += ["", f"归属（规则 L≥4+专属）：{R.get('attribution')}", ""]
    for t, xs in R.get("other_samples", {}).items():
        L.append(f"### 判为引自别部经的样例（{t}）")
        L += [f"- {x['doc']}（{x['len']}字）…{x['ctx']}…" for x in xs]
        L.append("")
    L += [f"消歧（经内多处出现的片段）：{R['disambiguation']}", "",
          "## 段落覆盖（规则 L≥4+专属，去别部经+顺序链；锚定 + 单调插值）", "",
          "| 目标 | 文献 | 作者 | 段落 | 锚定 | 插值 | 覆盖率 |", "|---|---|---|---|---|---|---|"]
    for r in sorted(R["coverage"], key=lambda r: (r["target"], -r["coverage"])):
        L.append(f"| {r['target']} | {r['doc']} {r['title']} | {r['author']} | {r['paras']} | {r['anchored']} | {r['interpolated']} | {r['coverage']} |")
    L += ["", "## 召回缺口分析（明引片段为何没找回；规则 L≥4+专属，去别部经+顺序链）", ""]
    for t, m in R.get("miss", {}).items():
        L.append(f"### {t}：漏 {m['total_missed']} 条；原因 {m['reasons']}")
        L.append(f"按引文字数分布：{m['by_len']}")
        for why, xs in m["samples"].items():
            L.append(f"- 样例 [{why}]")
            L += [f"  - {x['doc']}:{x['lb']}（{x['len']}字，经内出现{x['occ']}次）…{x['ctx']}…" for x in xs]
        L.append("")
    L += ["", "## 无引号命中样例（引号方法找不到的）", ""]
    for did, ex in R["samples_unquoted"].items():
        for e in ex:
            L.append(f"- {did}:{e['lb']}（{e['len']}字）…{e['ctx']}…")
    L += ["", "## 错配误判样例（法华疏 → 心经）", ""]
    L += [f"- {x['doc']}（{x['len']}字）{x['text']}" for x in R["null_samples"]]
    lt, f = R["latency_ms"], R["factors_minlen8"]
    L += ["", "## 任意片段查询（SA 二分，纯 Python 单线程）",
          f"- 2000 次随机 4–8 字查询：p50 {lt['p50']} ms，p99 {lt['p99']} ms；平均每次命中 {lt['mean_occ']} 处", "",
          "## 因子化（LCP 区间，≥8 字，近似）",
          f"- 重复片段因子 {f['nodes']:,} 个；按因子连边 {f['factor_edges']:,} 条；若两两连边需 {f['pairwise_edges']:,} 条（{f['ratio']}×）", ""]
    return "\n".join(L)


# ------------------------------------------------------------------ 自测
def selftest():
    rnd = random.Random(1)
    alpha = "甲乙丙丁戊己庚辛壬癸子丑"
    def rs(n): return "".join(rnd.choice(alpha) for _ in range(n))
    txt = rs(400) + chr(SEP0) + rs(300) + chr(SEP0 + 1)
    sa = suffix_array(txt).tolist()
    assert sa == sorted(range(len(txt)), key=lambda i: txt[i:]), "SA 错"
    lcp, _ = kasai(txt, sa)
    for r in range(1, len(sa)):
        a, b = txt[sa[r - 1]:], txt[sa[r]:]
        k = 0
        while k < min(len(a), len(b)) and a[k] == b[k]: k += 1
        assert lcp[r] == k, "LCP 错"
    a, b = 0, 400
    ms, tp = matching_stats(sa, lcp, a, b)
    tgt = txt[a:b]
    for p in range(401, len(txt) - 1):
        best = 0
        for q in range(a, b):
            k = 0
            while p + k < len(txt) and q + k < b and txt[p + k] == txt[q + k]: k += 1
            best = max(best, k)
        # 目标区间以私用区字符结尾、疏文侧亦然：最长匹配不会越过结尾符
        assert ms[p] == best, (p, ms[p], best)
    for q in ["甲乙", "子丑子", tgt[10:16]]:
        lo, hi = sa_range(txt, sa, q)
        assert hi - lo == sum(1 for i in range(len(txt)) if txt.startswith(q, i))
    print("suffix_experiment selftest: OK（SA、LCP、匹配统计、区间查询均与暴力解一致）")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--out", default="/tmp/indra")
    a = ap.parse_args()
    selftest() if a.selftest else run(a.out)
