# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lunar Society Technologies
"""
heldout.py — 留出检验：v3 参数冻结（不再按结果调参），换三部从未参与调参的经及其注疏。

  阿彌陀經 T12n0366 · 維摩詰所說經 T14n0475 · 金剛般若波羅蜜經 T08n0235
  对照与之前完全相同：中阿含 T01n0026、增一阿含 T02n0125（不可能引用大乘经）；
  交叉对照 = 其他两族注疏对这部经（含真实互引，只作下界）。
  召回考卷 = 注疏里用「」明引、且确在该经出现的片段。

用法：INDRA_SITE=... python3 -m indra.heldout --out /tmp/heldout
"""
import argparse, collections, json, os, re, time

import numpy as np

from .norm import norm
from .short_quote import extract_fragments
from .suffix_experiment import Corpus, suffix_array, kasai, sa_range, spans_for, ctx
from .lemma_walk import lemma_walk, family_matching_stats, propagate

SITE = os.environ.get("INDRA_SITE", "")
TARGETS = [
    ("amitabha", "T12n0366", "阿彌陀經", lambda t: "阿彌陀經" in t and "無量壽" not in t),
    ("vimalakirti", "T14n0475", "維摩經", lambda t: "維摩" in t),
    ("diamond", "T08n0235", "金剛經", lambda t: ("金剛般若" in t or "金剛經" in t) and "金剛頂" not in t and "三昧" not in t),
    ("heart", "T08n0251", "心經", lambda t: "般若" in t and "心經" in t and "金剛" not in t),
    ("lotus", "T09n0262", "法華經", lambda t: "法華" in t or "妙法蓮華" in t),
]
OTHER_SUTRAS = ["T08n0223", "T10n0279", "T12n0374", "T25n1509", "T12n0360"]
BG = ["T01n0001", "T02n0099"]
NULL = ["T01n0026", "T02n0125"]
CAP_CHARS = 1_500_000          # 每族注疏取前 150 万字（按经号排序，确定性）
FROZEN = {"确定层": dict(alpha=0.01, lemma=True, lemma_ext=True, lemma_k=5),
          "候选层": dict(alpha=0.03, lemma=True, lemma_ext=True, lemma_k=10)}


def matching_stats_mask(sa, lcp, mask):
    """matching statistics against an arbitrary set of text positions (mask[pos] == 1)."""
    n = len(sa)
    BIG = 1 << 30
    is_t = [mask[s] for s in sa]
    ms = [0] * n
    for direction in (1, -1):
        cur, have = 0, False
        rng = range(n) if direction == 1 else range(n - 1, -1, -1)
        for k in rng:
            if have:
                l = lcp[k] if direction == 1 else lcp[k + 1]
                if l < cur:
                    cur = l
            if is_t[k]:
                have, cur = True, BIG
            elif have:
                p = sa[k]
                if cur > ms[p]:
                    ms[p] = cur
    return ms


def active_targets():
    only = os.environ.get("INDRA_ONLY")
    return [t for t in TARGETS if not only or t[0] in only.split(",")]


def fetch():
    import requests, concurrent.futures
    if not SITE:
        raise SystemExit("set INDRA_SITE")
    man = requests.get(f"{SITE}/data/manifest.json", timeout=180).json()
    allm = man.get("docs", man) if isinstance(man, dict) else man
    meta = {d["doc_id"]: d for d in allm if d.get("doc_id")}
    sutra_ids = {t[1] for t in active_targets()} | set(OTHER_SUTRAS)
    plan = [(did, "sutra:" + did) for did in sorted(sutra_ids)]
    plan += [(d, "bg") for d in BG] + [(d, "null") for d in NULL]
    taken = set(sutra_ids)
    for fam, _, _, pick in active_targets():
        for did in sorted(meta):
            m = meta[did]
            t = m.get("title") or ""
            if did in taken or m.get("class") not in ("commentary", "treatise") or "梵本" in t:
                continue
            if pick(t):
                plan.append((did, "com:" + fam)); taken.add(did)

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
    out, used = [], collections.Counter()
    for d in sorted(docs, key=lambda d: (d["fam"], d["id"])):
        if d["fam"].startswith("com:"):
            n = sum(len(norm(t)) for _, t in d["paras"])
            if used[d["fam"]] + n > CAP_CHARS and used[d["fam"]] > 0:
                continue
            used[d["fam"]] += n
        out.append(d)
    for d in out:                       # 《心經》卷首有明代御制序：经文正文自「觀自在菩薩」起
        if d["id"] == "T08n0251":
            for i, (lb, t) in enumerate(d["paras"]):
                if "觀自在菩薩" in t:
                    j = t.index("觀自在菩薩")
                    d["paras"] = [(lb, t[j:])] + d["paras"][i + 1:]
                    break
    return out


def one_sub_diagnostic(C, cf, tstr, minlen=6, maxlen=24):
    """注疏「」明引中，经文里找不到原样、但改一个字就能找到的片段：数量 + 最常见的替换字对。"""
    tot = exact = one = 0
    pairs, docs_of = collections.Counter(), collections.defaultdict(set)
    seen = set()
    for d in C.docs:
        if d["fam"] != cf:
            continue
        for raw in d["praw"]:
            for frag, src, _ in extract_fragments(raw):
                if src != "quote" or not (minlen <= len(frag) <= maxlen) or (d["id"], frag) in seen:
                    continue
                seen.add((d["id"], frag))
                tot += 1
                if frag in tstr:
                    exact += 1; continue
                h = len(frag) // 2
                found = None
                for half, off in ((frag[:h], 0), (frag[h:], h)):
                    j = tstr.find(half)
                    while j >= 0 and found is None:
                        st = j - off
                        cand = tstr[st:st + len(frag)] if st >= 0 else ""
                        if len(cand) == len(frag):
                            diff = [k for k in range(len(frag)) if cand[k] != frag[k]]
                            if len(diff) == 1:
                                found = (frag[diff[0]], cand[diff[0]])
                        j = tstr.find(half, j + 1)
                    if found:
                        break
                if found:
                    one += 1
                    pairs[found] += 1; docs_of[found].add(d["id"])
    top = [f"{a}→{b} ×{n}（{len(docs_of[(a, b)])}部）" for (a, b), n in pairs.most_common(25)]
    return {"quotes_6_24": tot, "exact": exact, "one_sub": one, "top_pairs": top}


def run(outdir, docs=None, full=None, export_dir=None):
    os.makedirs(outdir, exist_ok=True)
    t0 = time.time()
    docs = docs or fetch()
    C = Corpus(docs)
    N = len(C.text)
    sa = suffix_array(C.text)
    sa_np = sa.astype(np.int64)
    sa = sa.tolist()
    lcp, _ = kasai(C.text, sa)
    fam_chars = collections.Counter()
    for d in C.docs:
        fam_chars[d["fam"]] += d["end"] - d["start"]
    R = {"corpus": {"chars": N, "fams": {k: [sum(1 for d in C.docs if d["fam"] == k), v] for k, v in fam_chars.items()},
                    "t_index_s": round(time.time() - t0, 1)}, "rows": [], "samples": {}, "null_samples": {}}
    by_id = {d["id"]: d for d in C.docs}
    sutra_docs = [d for d in C.docs if d["fam"].startswith("sutra:")]
    null_docs = [d for d in C.docs if d["fam"] == "null"]
    fam_ms = {}
    for f in ["com:" + t[0] for t in active_targets()] + ["null"]:
        fam_ms[f] = family_matching_stats(C, sa, lcp, f)
    for fam, tid, tname, _ in active_targets():
        if tid not in by_id or not any(d["fam"] == "com:" + fam for d in C.docs):
            R["rows"].append({"target": tname, "error": "missing sutra or commentaries"}); continue
        t1 = time.time()
        target = by_id[tid]
        target["fam"] = "target"
        N_T = target["end"] - target["start"]
        mask = bytearray(N)
        mask[target["start"]:target["end"]] = b"\x01" * N_T
        ms = matching_stats_mask(sa, lcp, mask)
        comp_mask = bytearray(N)
        bg_mask = np.zeros(N, dtype=np.bool_)
        for d in sutra_docs:
            if d["id"] != tid:
                comp_mask[d["start"]:d["end"]] = b"\x01" * (d["end"] - d["start"])
                bg_mask[d["start"]:d["end"]] = True
        for d in C.docs:
            if d["fam"] == "bg":
                bg_mask[d["start"]:d["end"]] = True
        nbg = int(bg_mask.sum())
        msc = matching_stats_mask(sa, lcp, comp_mask)
        fams = ["com:" + f for f, *_ in active_targets()] + ["null"]
        spans = {f: spans_for(C, ms, None, f, target) for f in fams}

        def attribution(x):
            d = x["doc"]; p, m = x["p"], x["m"]
            pi = C.para_of(d, p)
            pst = d["pstarts"][pi]
            pen = d["pstarts"][pi + 1] if pi + 1 < len(d["pstarts"]) else d["end"]
            for q in range(p, max(pst, p - 40) - 1, -1):
                L = min(msc[q], pen - q)
                if q + L >= p + m and L > m and L >= 8:
                    return "other"
            return "own"
        bgc = {}
        for f in fams:
            for x in spans[f]:
                x["attr"] = attribution(x)
                if x["s"] not in bgc:
                    lo, hi = sa_range(C.text, sa, x["s"])
                    bgc[x["s"]] = int(bg_mask[sa_np[lo:hi]].sum()) if hi > lo else 0
        tstr = C.text[target["start"]:target["end"]]
        cf = "com:" + fam
        R.setdefault("variants", {})[tname] = one_sub_diagnostic(C, cf, tstr)
        gold = []
        for d in C.docs:
            if d["fam"] != cf:
                continue
            for i, raw in enumerate(d["praw"]):
                pn = norm(raw)
                for frag, src, _ in extract_fragments(raw):
                    if src == "quote" and frag in tstr:
                        j = pn.find(frag)
                        if j >= 0:
                            gold.append((d, d["pstarts"][i] + j, len(frag)))
        com_ids = [d["id"] for d in C.docs if d["fam"] == cf]
        null_ids = [d["id"] for d in null_docs]
        bgvars = [("样本背景(基线)", bgc, nbg)]
        if full is not None:
            strs = {x["s"] for f in fams for x in spans[f]}
            for vname, msk in (("全藏·排除目标经/其注疏/阿含对照", full.mask([tid] + com_ids + null_ids)),
                               ("全藏·仅经藏正文", full.mask([tid] + null_ids, classes=("canon",)))):
                bgv = {s_: full.count(s_, msk) for s_ in strs}
                bgvars.append((vname, bgv, full.chars(msk)))
        tiers = []
        for bgname, bgc_, nbg_ in bgvars:
            items = list(FROZEN.items())
            if bgname == "全藏·仅经藏正文":
                items = [("确定层", FROZEN["确定层"])] + [(f"确定层 α={a}", dict(FROZEN["确定层"], alpha=a)) for a in (0.002, 0.004, 0.006)]
            for tier, kw in items:
                if bgname != bgvars[0][0] and not tier.startswith("确定层"):
                    continue
                acc = {f: lemma_walk(spans[f], N_T, bgc_, C.text, nbg_, **kw)[0] for f in fams}
                tiers.append((tier + ("" if bgname == bgvars[0][0] else " · " + bgname), acc))
                if tier == "确定层" and bgname == bgvars[0][0]:
                    tiers.append(("确定层+复注传递", {f: propagate(C, f, spans[f], acc[f], fam_ms[f]) for f in fams}))
        for tier, acc in tiers:
            acc_c = [x for x in spans[cf] if id(x) in acc[cf]]
            dc = len(acc_c) / max(1, fam_chars[cf]) * 1e4
            row = {"target": tname, "tier": tier, "commentaries": sum(1 for d in C.docs if d["fam"] == cf),
                   "com_chars": fam_chars[cf], "hits": len(acc_c), "per10k": round(dc, 2)}
            for nd in null_docs:
                k = sum(1 for x in spans["null"] if id(x) in acc["null"] and x["doc"]["id"] == nd["id"])
                da = k / max(1, nd["end"] - nd["start"]) * 1e4
                row["prec_" + nd["id"]] = round(max(0.0, 1 - da / dc), 3) if dc else 0.0
            other = [f for f in fams if f.startswith("com:") and f != cf]
            kc = sum(1 for f in other for x in spans[f] if id(x) in acc[f])
            dn = kc / max(1, sum(fam_chars[f] for f in other)) * 1e4
            row["cross_lb"] = round(max(0.0, 1 - dn / dc), 3) if dc else 0.0
            cover = collections.defaultdict(set)
            for x in acc_c:
                cover[x["doc"]["id"]].update(range(x["p"], x["p"] + x["m"]))
            hit = sum(1 for d, g, L in gold if all(q in cover[d["id"]] for q in range(g, g + L)))
            row["gold"] = len(gold)
            row["recall"] = round(hit / max(1, len(gold)), 3)
            R["rows"].append(row)
            if export_dir and tier == "确定层":
                from .lemma_export import export
                pairs = [(x, acc[cf][id(x)]) for x in acc_c]
                R.setdefault("exports", []).append(export(C, target, pairs, export_dir, tid, tname))
            if tier == "确定层":
                gm = collections.defaultdict(set)
                for d, g, L in gold:
                    gm[d["id"]].update(range(g, g + L))
                ex = [x for x in acc_c if x["m"] < 8 and not any(q in gm[x["doc"]["id"]] for q in range(x["p"], x["p"] + x["m"]))]
                step = max(1, len(ex) // 8)
                R["samples"][tname] = [f"{x['doc']['id']} {x['doc']['title'][:12]}（{x['m']}字）…{ctx(x['doc']['praw'][C.para_of(x['doc'], x['p'])], x['s'], 24)}…"
                                       for x in ex[::step][:8]]
                R["null_samples"][tname] = [f"{x['doc']['id']}（{x['m']}字，背景{bgc[x['s']]}次）{x['s']}"
                                            for x in spans["null"] if id(x) in acc["null"]][:6]
        target["fam"] = "sutra:" + tid
        R.setdefault("t_target_s", {})[tname] = round(time.time() - t1, 1)
        del ms, msc, spans
    R["elapsed_s"] = round(time.time() - t0, 1)
    json.dump(R, open(os.path.join(outdir, "heldout.json"), "w"), ensure_ascii=False, indent=1)
    rep = report(R)
    open(os.path.join(outdir, "report.md"), "w").write(rep)
    print(rep)
    return R


def report(R):
    rows = [r for r in R["rows"] if "error" not in r]
    nk = sorted({k for r in rows for k in r if k.startswith("prec_")})
    from .norm import VARIANTS
    fc = R.get("fullcanon")
    L = ["# 留出检验：v3 参数冻结，换未参与调参的经", "",
         f"- 异体字归一：{'开（Unihan kZVariant+kSemanticVariant，' + str(len(VARIANTS)) + ' 字映射）' if VARIANTS else '关'}",
         f"- 语料 {R['corpus']['chars']:,} 字；各族（文献数，字数）{R['corpus']['fams']}；总耗时 {R['elapsed_s']}s", "",
         *( [f"- 全藏索引：{fc}", ""] if fc else [] ),
         "| 经 | 层 | 注疏数 | 注疏字数 | 命中 | 每万字 | " + " | ".join("估计精确率·" + k[5:] for k in nk) + " | 交叉下界 | 考卷 | 召回 |",
         "|---|---|---|---|---|---|" + "---|" * len(nk) + "---|---|---|"]
    for r in rows:
        L.append(f"| {r['target']} | {r['tier']} | {r['commentaries']} | {r['com_chars']:,} | {r['hits']} | {r['per10k']} | "
                 + " | ".join(str(r.get(k, "")) for k in nk) + f" | {r['cross_lb']} | {r['gold']} | {r['recall']} |")
    L += [f"- {r['target']}：{r['error']}" for r in R["rows"] if "error" in r]
    for t, v in R.get("variants", {}).items():
        L += ["", f"### {t}：「」明引（6–24 字）{v['quotes_6_24']} 条；经中原样可见 {v['exact']}；改一字可见 {v['one_sub']}",
              "注疏字→经文字（出现次数，涉及几部注疏）：" + "；".join(v["top_pairs"])]
    for t, xs in R["samples"].items():
        L += ["", f"### {t}：确定层里、注疏没加引号的短片段（抽样）"] + [f"- {e}" for e in xs]
    for t, xs in R["null_samples"].items():
        L += ["", f"### {t}：阿含对照里仍被接受的（噪声样例）"] + [f"- {e}" for e in xs]
    return "\n".join(L)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/heldout")
    ap.add_argument("--export", default=None, help="write clause-level lemma JSON for each active target here")
    ap.add_argument("--full", action="store_true", help="also build the whole-canon index and use it as background")
    a = ap.parse_args()
    full = None
    if a.full:
        from .fullcanon import FullCanon
        full = FullCanon()
        st = full.stats()
        print("FULLCANON", json.dumps(st, ensure_ascii=False), flush=True)
    R = run(a.out, full=full, export_dir=a.export)
    if full is not None:
        R["fullcanon"] = st
        json.dump(R, open(os.path.join(a.out, "heldout.json"), "w"), ensure_ascii=False, indent=1)
