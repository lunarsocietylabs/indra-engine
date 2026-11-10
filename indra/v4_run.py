# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lunar Society Technologies
"""
v4_run.py — 在真实语料上跑 v4：对每部目标经的每部注疏，定位 + 假经 FDR + 略牒文法 + 科判 + 经疏关系，写 JSON 与汇总表。
用法：INDRA_SITE=https://... python3 -m indra.v4_run --out /tmp/v4 [--only heart,amitabha]
输出：<out>/<fam>.json（逐注疏：命中率、锚点数、各条链得分、通过 FDR 的链、略牒链接、科判树、关系分类）与 summary.md。
全部确定性：没有随机数，注疏按经号排序。
"""
import argparse, json, os, re
from . import heldout, v4_locate as V, v4_grammar as G, v4_kepan as K, v4_relation as R
from .norm import norm

PUNCT_END = "。；？！"


def clause_bounds(raw_paras):
    """按句读切经文；返回 (S 归一化全文, [(s0,s1)…])。"""
    S, bounds, cur = "", [], 0
    for _, t in raw_paras:
        buf = ""
        for ch in t:
            buf += ch
            if ch in PUNCT_END:
                n = norm(buf)
                if n:
                    bounds.append((cur, cur + len(n))); cur += len(n); S += n
                buf = ""
        n = norm(buf)
        if n:
            bounds.append((cur, cur + len(n))); cur += len(n); S += n
    return S, bounds


def run(out, only=None):
    docs = heldout.fetch()
    byid = {d["id"]: d for d in docs}
    nulls = [norm("".join(t for _, t in byid[i]["paras"])) for i in heldout.NULL if i in byid]
    os.makedirs(out, exist_ok=True)
    rows = []
    for fam, tid, title, _ in heldout.active_targets():
        if only and fam not in only or tid not in byid:
            continue
        S, bounds = clause_bounds(byid[tid]["paras"])
        res = []
        for d in sorted((x for x in docs if x["fam"] == "com:" + fam), key=lambda x: x["id"]):
            raw = "".join(t for _, t in d["paras"])
            C = norm(raw)
            r = V.locate(S, C, bounds)
            decoy = [[c["score"] for c in V.chains(S, N[:len(C)] or N)] for N in nulls]
            th = V.fdr_thresholds([c["score"] for c in r["chains"]], decoy)
            links = G.find_links(raw, S)
            sh = R.shape(S, C)
            res.append({"id": d["id"], "title": d["title"], "author": d["author"], "len": len(C),
                        "frac": round(r["frac"], 4), "stats": r["stats"], "chains": [round(c["score"], 1) for c in r["chains"]],
                        "fdr": {str(k): v for k, v in th[0].items()} if isinstance(th, tuple) else th,
                        "links": links, "kepan": K.parse(raw), "relation": R.classify(sh), "shape": sh,
                        "clauses": r["clauses"]})
        json.dump({"fam": fam, "id": tid, "title": title, "n_clauses": len(bounds), "commentaries": res},
                  open(os.path.join(out, fam + ".json"), "w", encoding="utf8"), ensure_ascii=False)
        cov = sum(1 for r in res if r["clauses"] for _ in [0])
        rows.append((title, len(bounds), len(res), sum(r["frac"] for r in res) / max(1, len(res)),
                     sum(1 for r in res if r["relation"] == "注疏")))
    with open(os.path.join(out, "summary.md"), "w", encoding="utf8") as f:
        f.write("| 经 | 句数 | 注疏数 | 平均定位率 | 判为注疏 |\n|---|---|---|---|---|\n")
        for t, nc, n, fr, k in rows:
            f.write(f"| {t} | {nc} | {n} | {fr:.3f} | {k} |\n")
    print(open(os.path.join(out, "summary.md"), encoding="utf8").read())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    run(a.out, set(filter(None, a.only.split(","))))
