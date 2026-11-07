# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lunar Society Technologies
"""
bozh.py — 汉藏"因子对齐"样本：用 MITRA 汉藏对照段（CC BY-SA 4.0）确定性地找出 汉文词 ↔ 藏文音节串 的对应。

方法（无模型、无人工）：
  1. 取对照段 score>=MIN_SCORE；汉文侧用丁福保词头表做最长优先识别，得到每段的汉文词集合；
     藏文侧按音节（་）取 1–3 音节串。
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
PUNCT = re.compile(r"[\s།༎༏༐༑༔,;/]+")


def fetch_rows(doc, files):
    rows = []
    for f in files:
        r = requests.get(f"{SITE}/data/tibetan/{f}", timeout=120)
        r.raise_for_status()
        j = r.json()
        rows += j if isinstance(j, list) else (j.get("rows") or [])
    return rows


def syllables(bo):
    return [s for s in PUNCT.sub("་", bo).split("་") if s]


def bo_ngrams(bo, nmax=3):
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


def align(pairs, heads, maxlen):
    """pairs: [(zh_text, bo_text, lb)] -> {term: (best_bo, c, dice, df_t, df_b, lbs[:3])}"""
    df_t, df_b = collections.Counter(), collections.Counter()
    co = collections.defaultdict(collections.Counter)
    where = collections.defaultdict(lambda: collections.defaultdict(list))
    for zh, bo, lb in pairs:
        T = zh_terms(zh, heads, maxlen)
        B = bo_ngrams(bo)
        for t in T:
            df_t[t] += 1
        for b in B:
            df_b[b] += 1
        for t in T:
            for b in B:
                co[t][b] += 1
                if len(where[t][b]) < 3:
                    where[t][b].append(lb)
    best = {}
    for t, cb in co.items():
        if df_t[t] < MIN_C:
            continue
        cand = []
        for b, c in cb.items():
            if c < MIN_C:
                continue
            d = 2 * c / (df_t[t] + df_b[b])
            cand.append((d, c, -len(b), b))
        if not cand:
            continue
        d, c, _, b = max(cand)
        if d >= MIN_DICE:
            best[t] = (b, c, round(d, 3), df_t[t], df_b[b], where[t][b])
    return best, df_t


def main(out):
    t0 = time.time()
    heads = set(requests.get(f"{SITE}/data/lexicon-preview/heads.json", timeout=120).json())
    maxlen = max(len(h) for h in heads)
    man = requests.get(f"{SITE}/data/tibetan/manifest.json", timeout=120).json()["files"]
    R = {"min_score": MIN_SCORE, "min_c": MIN_C, "min_dice": MIN_DICE, "docs": {}}
    per = {}
    for d in DOCS:
        if d not in man:
            R["docs"][d] = "not in alignment manifest"; continue
        rows = fetch_rows(d, man[d])
        pairs = [("".join(r["zh_text"]), " ".join(r["bo_unicode"]), r["zh_lb"]) for r in rows if r.get("score", 0) >= MIN_SCORE]
        best, df_t = align(pairs, heads, maxlen)
        eligible = [t for t, n in df_t.items() if n >= MIN_C]
        per[d] = best
        R["docs"][d] = {"rows": len(rows), "kept": len(pairs), "terms_seen": len(df_t), "terms_eligible": len(eligible),
                        "aligned": len(best), "aligned_rate": round(len(best) / max(1, len(eligible)), 3)}
        print(d, json.dumps(R["docs"][d], ensure_ascii=False), flush=True)
    # 跨经一致性：同一汉文词在两部以上经里各自独立对齐，最佳藏文是否一致（相同或互为子串）
    shared = collections.defaultdict(dict)
    for d, best in per.items():
        for t, v in best.items():
            shared[t][d] = v[0]
    multi = {t: v for t, v in shared.items() if len(v) >= 2}

    def same(a, b):
        return a == b or a in b or b in a
    agree = [t for t, v in multi.items() if all(same(x, y) for x in v.values() for y in v.values())]
    R["cross_doc"] = {"terms_in_2plus_docs": len(multi), "consistent": len(agree),
                      "rate": round(len(agree) / max(1, len(multi)), 3)}
    ex = []
    for t in sorted(agree, key=lambda t: -len(multi[t]))[:40]:
        ex.append({"zh": t, "bo": multi[t]})
    bad = [{"zh": t, "bo": multi[t]} for t in multi if t not in agree][:20]
    R["examples_consistent"], R["examples_inconsistent"] = ex, bad
    os.makedirs(out, exist_ok=True)
    json.dump(R, open(os.path.join(out, "bozh.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump({d: {t: v[:2] + v[2:5] for t, v in b.items()} for d, b in per.items()},
              open(os.path.join(out, "pairs.json"), "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps({k: R[k] for k in ("docs", "cross_doc")}, ensure_ascii=False, indent=1))
    print("EXAMPLES", json.dumps(ex[:25], ensure_ascii=False))
    print("INCONSISTENT", json.dumps(bad[:10], ensure_ascii=False))
    print("seconds", round(time.time() - t0, 1))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/bozh")
