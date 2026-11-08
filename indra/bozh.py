# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lunar Society Technologies
"""
bozh.py — 汉藏"因子对齐"样本：用 MITRA 汉藏对照段（CC BY-SA 4.0）确定性地找出 汉文词 ↔ 藏文音节串 的对应。

方法（无模型、无人工）：
  1. 取对照段 score>=MIN_SCORE；汉文侧用丁福保词头表做最长优先识别，得到每段的汉文词集合；
     藏文侧按音节（་）取 1–4 音节串。
  2. 对每个汉文词 t：c(t,b)=同段共现数；Dice=2c/(df_t+df_b)；取 Dice 最高的 b，要求 c>=MIN_C、Dice>=MIN_DICE。
  3. 无人工验证：同一汉文词在不同经（金刚/弥陀/维摩…）里各自独立对齐，看最佳藏文是否一致。
只对齐词，不翻译；每个结论都能回到具体的汉文行号（zh_lb）和藏文原文。
"""
import collections, json, os, re, sys, time

import requests

SITE = os.environ.get("INDRA_SITE", "https://aicanon.omnisun.workers.dev")
MIN_SCORE = float(os.environ.get("BOZH_MIN_SCORE", "0.9"))
MIN_C = int(os.environ.get("BOZH_MIN_C", "3"))
MIN_DICE = float(os.environ.get("BOZH_MIN_DICE", "0.6"))
DOCS = os.environ.get("BOZH_DOCS", "T08n0235,T12n0366,T14n0475").split(",")
CAP_FRAC = float(os.environ.get("BOZH_CAP_FRAC", "0.02"))
WORKERS = int(os.environ.get("BOZH_WORKERS", "8"))
PUNCT = re.compile(r"[\s།༎༏༐༑༔,;/]+")


def fetch_rows(doc, files):
    rows = []
    for f in files:
        r = requests.get(f"{SITE}/data/tibetan/{f}", timeout=120)
        r.raise_for_status()
        j = r.json()
        rows += j if isinstance(j, list) else (j.get("rows") or [])
    return rows


# 藏文助词会粘在音节末尾（属格 འི 等），同一个词因此有多种写法；统计前先去掉，使其算同一个词
SUFFIXES = ("འི", "འང")


def _stem(syl):
    # 先去属格 འི / འང，再去末尾的 འ：དཔའི 与 དཔའ（词根带 འ）归一为同一形式，པའི→པ、པ→པ
    for suf in SUFFIXES:
        if syl.endswith(suf) and len(syl) > len(suf):
            syl = syl[: -len(suf)]
            break
    if syl.endswith("འ") and len(syl) > 1:
        syl = syl[:-1]
    return syl


def syllables(bo):
    return [_stem(s) for s in PUNCT.sub("་", bo).split("་") if s]


def bo_ngrams(bo, nmax=4):
    s = syllables(bo)
    out = set()
    for n in range(1, nmax + 1):
        for i in range(len(s) - n + 1):
            out.add("་".join(s[i:i + n]))
    return out


def zh_terms(text, heads, maxlen):
    t = re.sub(r"[\s，。、；：！？「」『』（）《》．·]", "", text)
    out, i = set(), 0
    while i < len(t):
        hit = 0
        for L in range(min(maxlen, len(t) - i), 1, -1):
            if t[i:i + L] in heads:
                hit = L
                break
        if hit:
            out.add(t[i:i + hit]); i += hit
        else:
            i += 1
    return out


def align(pairs, heads, maxlen, ctx=None):
    """pairs: [(zh_text, bo_text, lb)] -> ({term: (best_bo, c, dice, df_t, df_b, lbs[:3])}, df_t)
    两遍：先数全体 df，再只对 df>=MIN_C 且不过于常见的藏文串累计共现（过常见的多是助词，对词义对应无益）。"""
    N = len(pairs)
    cap = max(50, int(CAP_FRAC * N))
    T_of, B_of = [], []
    df_t, df_b = collections.Counter(), collections.Counter()
    for zh, bo, lb in pairs:
        T = zh_terms(zh, heads, maxlen); B = bo_ngrams(bo)
        T_of.append(T); B_of.append(B)
        df_t.update(T); df_b.update(B)
    keep_b = {b for b, n in df_b.items() if MIN_C <= n <= cap}
    co = collections.defaultdict(collections.Counter)
    where = collections.defaultdict(lambda: collections.defaultdict(list))
    for (zh, bo, lb), T, B in zip(pairs, T_of, B_of):
        Bk = [b for b in B if b in keep_b]
        for t in T:
            if df_t[t] < MIN_C:
                continue
            ct = co[t]
            for b in Bk:
                ct[b] += 1
    best = {}
    for t, cb in co.items():
        cand = []
        for b, c in cb.items():
            if c < MIN_C:
                continue
            d = 2 * c / (df_t[t] + df_b[b])
            cand.append((d, c, len(b), b))
        if not cand:
            continue
        top = max(x[0] for x in cand)
        tied = [x for x in cand if x[0] >= top - 1e-9]
        longest = max(tied, key=lambda x: (x[2], x[1]))
        if any(x[3] not in longest[3] for x in tied):
            continue
        d, c, _, bb = longest
        if d >= MIN_DICE:
            locs = [lb for (zh, bo, lb), T, B in zip(pairs, T_of, B_of) if t in T and bb in B][:3]
            best[t] = (bb, c, round(d, 3), df_t[t], df_b[bb], locs)
    if ctx is not None:
        ctx.update(co=co, df_b=df_b, T_of=T_of, B_of=B_of)
    return best, df_t


def main(out):
    from concurrent.futures import ThreadPoolExecutor
    import hashlib
    t0 = time.time()
    heads = set(requests.get(f"{SITE}/data/lexicon-preview/heads.json", timeout=120).json())
    maxlen = max(len(h) for h in heads)
    man = requests.get(f"{SITE}/data/tibetan/manifest.json", timeout=120).json()["files"]
    docs = list(man) if DOCS == ["ALL"] else [d for d in DOCS if d in man]
    R = {"min_score": MIN_SCORE, "min_c": MIN_C, "min_dice": MIN_DICE, "cap_frac": CAP_FRAC, "docs_used": len(docs)}

    def get(d):
        rows = fetch_rows(d, man[d])
        return d, [("".join(r["zh_text"]), " ".join(r["bo_unicode"]), r["zh_lb"]) for r in rows if r.get("score", 0) >= MIN_SCORE]
    with ThreadPoolExecutor(WORKERS) as ex:
        got = list(ex.map(get, docs))
    print("fetched", len(got), "docs in", round(time.time() - t0, 1), "s", flush=True)
    allp = [x for d, ps in got for x in ps]
    R["pairs"] = len(allp)
    ctx = {}
    best, df_t = align(allp, heads, maxlen, ctx)
    elig = [t for t, n in df_t.items() if n >= MIN_C]
    R["all"] = {"terms_seen": len(df_t), "terms_eligible": len(elig), "aligned": len(best), "aligned_rate": round(len(best) / max(1, len(elig)), 3)}
    print("ALL", json.dumps(R["all"], ensure_ascii=False), flush=True)
    # 给没配上的高频词留候选（供"提议-核验"一步使用）：每词 top-K 藏文串（按 Dice）+ 3 个短例句
    NT = int(os.environ.get("BOZH_CAND_TERMS", "1500")); K = 8
    un = sorted((t for t in elig if t not in best), key=lambda t: -df_t[t])[:NT]
    need = set(un); ex = {t: [] for t in un}
    for (zh, bo, lb), T in zip(allp, ctx["T_of"]):
        for t in T & need:
            if len(ex[t]) < 40:
                ex[t].append((len(zh) + len(bo), zh, bo, lb))
    cands = {}
    for t in un:
        cb = ctx["co"].get(t, {})
        lst = sorted(((2 * c / (df_t[t] + ctx["df_b"][b]), c, b) for b, c in cb.items() if c >= MIN_C), key=lambda x: (-x[0], -len(x[2])))[:K]
        shots = sorted(ex[t])[:3]
        cands[t] = {"df": df_t[t], "cands": [{"bo": b, "c": c, "dice": round(d, 3)} for d, c, b in lst],
                    "examples": [{"zh": z, "bo": b_, "lb": l} for _, z, b_, l in shots]}
    R["unaligned_with_candidates"] = len(cands)
    ctx.clear(); import gc; gc.collect()  # 释放大表，再做对半验证（避免内存叠加）
    # 无人工验证：按文献哈希分成两半，各自独立对齐，看同一汉文词的最佳藏文是否一致
    half = lambda d: int(hashlib.md5(d.encode()).hexdigest(), 16) % 2
    A = [x for d, ps in got if half(d) == 0 for x in ps]; B = [x for d, ps in got if half(d) == 1 for x in ps]
    bA, _ = align(A, heads, maxlen); bB, _ = align(B, heads, maxlen)
    both = [t for t in bA if t in bB]
    same = lambda a, b: a == b or a in b or b in a
    agree = [t for t in both if same(bA[t][0], bB[t][0])]
    R["split_half"] = {"pairs_A": len(A), "pairs_B": len(B), "aligned_A": len(bA), "aligned_B": len(bB),
                       "terms_in_both": len(both), "consistent": len(agree), "rate": round(len(agree) / max(1, len(both)), 3)}
    R["examples_consistent"] = [{"zh": t, "bo": bA[t][0], "dice": bA[t][2], "df": bA[t][3]} for t in sorted(agree, key=lambda t: -bA[t][3])[:40]]
    R["examples_inconsistent"] = [{"zh": t, "A": bA[t][0], "B": bB[t][0]} for t in both if t not in agree][:20]
    os.makedirs(out, exist_ok=True)
    json.dump(R, open(os.path.join(out, "bozh.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump({t: list(v) for t, v in best.items()}, open(os.path.join(out, "pairs.json"), "w", encoding="utf-8"), ensure_ascii=False)
    json.dump(cands, open(os.path.join(out, "cands.json"), "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps({k: R[k] for k in ("docs_used", "pairs", "all", "split_half")}, ensure_ascii=False, indent=1))
    print("EXAMPLES", json.dumps(R["examples_consistent"][:30], ensure_ascii=False))
    print("INCONSISTENT", json.dumps(R["examples_inconsistent"][:12], ensure_ascii=False))
    print("seconds", round(time.time() - t0, 1))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/bozh")
