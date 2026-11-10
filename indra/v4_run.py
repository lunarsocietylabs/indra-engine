# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lunar Society Technologies
"""
v4_run.py — 在真实语料上跑 v4 全流程，并出考卷。全部确定性：无随机数，文献按经号排序，多进程只为提速、不影响结果。

每部目标经：
  第一遍  本族每部注疏（另加若干部**别族**注疏作对照）走 v4_locate.pipeline：对真经一次、对每部假经各一次。
  定门槛  把本族全部注疏的真、假支持度汇总，求 FDR≤1%（確）与 ≤10%（候）的门槛。门槛按经定，不按疏定。
  第二遍  按门槛归属 → 略牒文法落实 → 科判树 → 关系判别 → 再注（钞、记）层叠 → 引证图。
考卷（都不需要人看）：
  对照    别族注疏的文字里，被当成"在讲这部经"而归过来的释文比例——应为 0（它们偶尔引到这部经的那几个字不算），这是实测的误归属；
  明引    注疏里用「」标出、且确在本经出现的片段，被定位到的比例（召回）；
  判别    "是不是这部经的注疏"由形状判别，对本族／别族实测精确率、召回率；
  对比    与 v3（站上现有的逐句引用数）逐句比较覆盖。
用法：INDRA_SITE=https://… python3 -m indra.v4_run --out /tmp/v4 [--only heart,amitabha] [--workers 4]
输出：<out>/<fam>.json（逐句的归属、科判、引证，前端可直接用）与 summary.md。
"""
import argparse, bisect, collections, concurrent.futures, json, multiprocessing, os, re, time

from . import heldout, v4_locate as V, v4_grammar as G, v4_kepan as K, v4_layers as Ly, v4_cite as Ci, v4_relation as R
from .lemma_export import split_clauses
from .norm import norm

QUOTE = re.compile(r"[「『]([^「」『』]{2,80})[」』]")
NEG = 12                                 # 每部经取多少部别族注疏作对照
_G = {}


def prep_sutra(d):
    S, bounds, clauses = "", [], []
    for lb, t in d["paras"]:
        for craw, n in split_clauses(t):
            if n:
                bounds.append((len(S), len(S) + n)); S += norm(craw); clauses.append({"t": craw.strip(), "lb": lb})
    assert len(S) == bounds[-1][1]
    return S, bounds, clauses


def prep_com(d):
    raw = "".join(t for _, t in d["paras"])
    C = norm(raw)
    idx = G.raw_to_norm(raw)
    ps, k = [], 0
    for _, t in d["paras"]:
        ps.append(k); k += len(norm(t))
    return {"id": d["id"], "title": d["title"], "author": d["author"], "raw": raw, "C": C, "idx": idx,
            "paras": ps if k == len(C) else None}


def _first(cid):
    g = _G
    C = g["coms"][cid]["C"]
    chs, sup = V.pipeline(g["S"], C, g["bg"], g["decoys"], g["sinfo"])
    return cid, chs, sup, [V.pipeline(D, C, g["bg"], pre=p)[1] for D, p in zip(g["decoys"], g["dinfo"])]


def exam_quotes(com, segs, S):
    """「」明引召回：分母 = 注疏里用引号标出、规范化后 ≥4 字、且确在本经出现的片段。"""
    if com["idx"] is None:
        return None
    q = sorted((s["c0"], s["c1"], s["s0"], s["s1"], s["tier"]) for s in segs if s["kind"] in ("引", "參"))
    cs = [x[0] for x in q]
    res = collections.Counter()
    for m in QUOTE.finditer(com["raw"]):
        n = norm(m.group(1))
        if len(n) < 4 or n not in S:
            continue
        c0 = com["idx"][m.start(1)]; c1 = c0 + len(n)
        b = "長" if len(n) >= 8 else "短"
        res["n" + b] += 1
        got, sure, k = 0, 0, max(0, bisect.bisect_right(cs, c0) - 3)
        while k < len(q) and q[k][0] < c1:
            a = q[k]; k += 1
            ov = min(c1, a[1]) - max(c0, a[0])
            if ov > 0 and S[a[2]:a[3]] and (S[a[2]:a[3]] in n or n in S[max(0, a[2] - len(n)):a[3] + len(n)]):
                got += ov; sure += ov if a[4] == V.SURE else 0
        res["any" + b] += got * 2 >= len(n)
        res["sure" + b] += sure * 2 >= len(n)
    return res


def run_family(fam, tid, title, docs, byid, bg, out, workers):
    t0 = time.time()
    S, bounds, clauses = prep_sutra(byid[tid])
    pool_txt = [norm("".join(t for _, t in byid[i]["paras"])) for i in heldout.NULL + heldout.BG + heldout.OTHER_SUTRAS if i in byid]
    decoys = V.decoy_windows(pool_txt, len(S), n=8 if len(S) <= 20000 else 4, avoid=S)
    own = sorted((d for d in docs if d["fam"] == "com:" + fam), key=lambda d: d["id"])
    others = sorted((d for d in docs if d["fam"].startswith("com:") and d["fam"] != "com:" + fam),
                    key=lambda d: (sum(len(t) for _, t in d["paras"]) > 400000, d["id"]))[:NEG]
    coms = {d["id"]: prep_com(d) for d in own + others}
    sinfo = V.char_info(S, bg)
    _G.clear(); _G.update({"S": S, "bg": bg, "decoys": decoys, "coms": coms, "sinfo": sinfo,
                           "dinfo": [V.char_info(D, bg) for D in decoys]})
    first = {}
    if workers > 1:
        with concurrent.futures.ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("fork")) as ex:
            for cid, chs, sup, dsup in ex.map(_first, sorted(coms)):
                first[cid] = {"chains": chs, "sup_true": sup, "sup_decoy": dsup}
    else:
        for cid in sorted(coms):
            _, chs, sup, dsup = _first(cid)
            first[cid] = {"chains": chs, "sup_true": sup, "sup_decoy": dsup}
    own_ids = [d["id"] for d in own]
    th = V.thresholds_all([x for c in own_ids for x in first[c]["sup_true"]],
                      [[x for c in own_ids for x in first[c]["sup_decoy"][z]] for z in range(len(decoys))])
    res, tally = {}, collections.Counter()
    for cid in sorted(coms):
        c = coms[cid]
        r = V.finish(first[cid], S, c["C"], bounds, th, c["paras"], sinfo)
        r["links"], r["kepan"] = [], []
        if c["idx"] is not None and cid in own_ids:
            r["links"] = G.find_links(c["raw"], S, G.expecter(r["segs"]), c["idx"])
            r["n_elide"] = G.apply_links(r["segs"], r["links"], bounds)
            r["kepan"] = K.parse(c["raw"], S, r["segs"], c["idx"])
            r["clauses"] = V.by_clause(r["segs"], bounds)
        r["relation"] = R.classify(r["stats"])
        res[cid] = r
    # ---- 再注层叠：书名提候选，比对确认，映射相乘
    layers = []
    cands = [(a, b) for a, b in Ly.candidates([coms[c] for c in own_ids]) if len(coms[a]["C"]) <= 200000]
    for a, b in cands[:12]:
        A, B = coms[a], coms[b]
        ab = [x for x in V.pseudo_bounds(len(A["C"]), 40)]
        dz = V.decoy_windows(pool_txt, len(A["C"]), n=4, avoid=None)
        if not dz:
            continue
        rb = V.locate(A["C"], B["C"], ab, dz, bg, sinfo=None)
        okay = Ly.confirm(rb["stats"])
        gain = 0
        if okay:
            extra = Ly.compose(rb["segs"], res[a]["segs"])
            have = bytearray(len(B["C"]) + 1)
            for s in res[b]["segs"]:
                have[s["c0"]:s["c1"]] = b"\x01" * (s["c1"] - s["c0"])
            add = [s for s in extra if not any(have[s["c0"]:s["c1"]])]
            gain = sum(s["c1"] - s["c0"] for s in add)
            res[b]["segs"] = sorted(res[b]["segs"] + add, key=lambda s: (s["c0"], s["c1"]))
            res[b]["clauses"] = V.by_clause(res[b]["segs"], bounds)
            res[b]["base"] = a
        layers.append({"base": a, "sub": b, "ok": okay, "hit": rb["stats"]["clauses_hit"], "of": rb["stats"]["clauses"],
                       "order": rb["stats"]["order"], "gain": gain})
    # ---- 逐句汇总 + 引证图
    lib = {d["id"]: norm("".join(t for _, t in d["paras"])) for d in docs if d["fam"].startswith("sutra:") and d["id"] != tid}
    ix = Ci.RunIndex(lib)
    memo = {}

    def runs(piece):
        if piece not in memo:
            memo[piece] = ix.find_runs(piece, skip=lambda t: t in S)
        return memo[piece]
    n2r = {c: (G.norm_to_raw(coms[c]["idx"]) if coms[c]["idx"] else None) for c in own_ids}
    out_cl = []
    for k, cl in enumerate(clauses):
        items, sure_docs, cand_docs, quote_docs, per = [], set(), set(), set(), {}
        for di, cid in enumerate(own_ids):
            r, c = res[cid], coms[cid]
            got = 0
            for si in r["clauses"][k]:
                s = r["segs"][si]
                if s["kind"] == "參":
                    continue
                (sure_docs if s["tier"] == V.SURE else cand_docs).add(cid)
                if s["kind"] == "引" and s["tier"] == V.SURE:
                    quote_docs.add(cid)
                if s["tier"] == V.SURE and s["kind"] in ("釋", "略") and not s.get("amb") and s["c1"] - s["c0"] >= 12:
                    per.setdefault(cid, []).append(s)
                if got < 3 and len(items) < 60 and s["c1"] - s["c0"] >= (2 if s["kind"] == "引" else 8):
                    it = {"d": di, "k": s["kind"], "r": s["tier"], "c": s["c0"], "n": s["c1"] - s["c0"]}
                    m = n2r[cid]
                    if m:
                        it["x"] = c["raw"][m[s["c0"]]:m[min(s["c1"], s["c0"] + 80, len(m) - 1)]]
                    if s.get("amb"):
                        it["amb"] = 1
                    items.append(it); got += 1
        cites = Ci.cite_graph(per, {c: coms[c]["C"] for c in per}, runs, top=8) if per else []
        for ct in cites:
            ct["x"] = lib[ct["doc"]][ct["pos"]:ct["pos"] + min(ct["len"], 40)]
        out_cl.append({"t": cl["t"], "lb": cl["lb"], "n": len(sure_docs), "nq": len(quote_docs),
                       "nc": len(cand_docs - sure_docs), "items": items, "cites": cites})
    # ---- 考卷
    ex = collections.Counter()
    for cid in own_ids:
        e = exam_quotes(coms[cid], res[cid]["segs"], S)
        if e:
            ex.update(e)
    loo = [V.loo_exam(first[c]["chains"], th, bounds) for c in own_ids]
    conf = R.confusion([res[c]["relation"] for c in own_ids], [res[d["id"]]["relation"] for d in others])
    ctrl = [res[d["id"]]["stats"]["gloss_frac"] for d in others]
    kep = [K.count(res[c]["kepan"]) for c in own_ids]
    v3 = v3_compare(tid, clauses, out_cl)
    doc_rows = [{"id": c, "title": coms[c]["title"], "author": coms[c]["author"], "len": len(coms[c]["C"]),
                 "relation": res[c]["relation"], "stats": res[c]["stats"], "base": res[c].get("base"),
                 "links": len(res[c]["links"]), "kepan": res[c]["kepan"]} for c in own_ids]
    summ = {"fam": fam, "id": tid, "title": title, "chars": len(S), "clauses": len(clauses), "docs": len(own_ids),
            "th": {str(k): v for k, v in th.items()}, "decoys": len(decoys),
            "clauses_sure": sum(1 for c in out_cl if c["n"]), "clauses_quote": sum(1 for c in out_cl if c["nq"]),
            "frac_sure": _mean([res[c]["stats"]["frac_sure"] for c in own_ids]),
            "frac_cand": _mean([res[c]["stats"]["frac_cand"] for c in own_ids]),
            "control_mean": _mean(ctrl), "control_max": max(ctrl) if ctrl else None, "control_n": len(ctrl),
            "exam": dict(ex), "relation": conf, "loo": [sum(a for a, _ in loo), sum(b for _, b in loo)],
            "why": dict(sum((collections.Counter(res[c]["stats"]["why"]) for c in own_ids), collections.Counter())),
            "chars": len(S), "own_chars": sum(len(coms[c]["C"]) for c in own_ids),
            "weak": sum(1 for c in own_ids for s in res[c]["segs"] if s.get("weak")),
            "var": sum(1 for c in own_ids for s in res[c]["segs"] if s.get("var")),
            "elide": sum(res[c].get("n_elide", 0) for c in own_ids), "links": sum(len(res[c]["links"]) for c in own_ids),
            "kepan_nodes": sum(a for a, _ in kep), "kepan_attached": sum(b for _, b in kep),
            "layers": layers, "cites": sum(1 for c in out_cl if c["cites"]), "v3": v3, "seconds": round(time.time() - t0)}
    json.dump({"summary": summ, "docs": doc_rows, "clauses": out_cl},
              open(os.path.join(out, fam + ".json"), "w", encoding="utf8"), ensure_ascii=False)
    sample(summ, out_cl, doc_rows, others, res)
    return summ


def _mean(v):
    return round(sum(v) / len(v), 4) if v else None


def v3_compare(tid, clauses, out_cl):
    """站上现有 v3 逐句引用数（lemma-preview）与 v4 逐句比较；取不到就返回 None。"""
    try:
        import requests
        r = requests.get(f"{heldout.SITE}/data/lemma-preview/{tid}.json", timeout=60)
        if r.status_code != 200:
            return None
        v3 = {norm(c["t"]): c["n"] for p in r.json()["paras"] for c in p["clauses"]}
    except Exception:
        return None
    both = [(v3[norm(c["t"])], o) for c, o in zip(clauses, out_cl) if norm(c["t"]) in v3]
    if not both:
        return None
    return {"matched": len(both), "v3_hit": sum(1 for a, _ in both if a), "v4_quote": sum(1 for _, o in both if o["nq"]),
            "v4_any": sum(1 for _, o in both if o["n"]), "v3_only": sum(1 for a, o in both if a and not o["n"]),
            "v4_only": sum(1 for a, o in both if o["n"] and not a),
            "v3_docs": sum(a for a, _ in both), "v4_docs_quote": sum(o["nq"] for _, o in both), "v4_docs_any": sum(o["n"] for _, o in both)}


def sample(summ, out_cl, doc_rows, others, res):
    """打印少量实例，供日志里目检（不进报表）。"""
    print(f"\n=== {summ['title']} 门槛 {summ['th']} 用时 {summ['seconds']}s")
    for k in sorted({0, len(out_cl) // 3, 2 * len(out_cl) // 3, len(out_cl) - 1}):
        c = out_cl[k]
        print(f"[{k}] {c['t']}  確{c['n']} 引{c['nq']} 候{c['nc']}")
        for it in c["items"][:4]:
            print(f"     {doc_rows[it['d']]['id']} {it['k']}{it['r']}{'?' if it.get('amb') else ''} {it.get('x', '')[:60]}")
        for ct in c["cites"][:2]:
            print(f"     ⇢ {ct['doc']} ×{ct['n']} {ct['x']}")
    for d in doc_rows:
        st = d["stats"]
        print(f"  {d['id']} {d['title'][:14]} {d['relation']} 字{d['len']} 確{st['frac_sure']} 候{st['frac_cand']} "
              f"句{st['clauses_hit']}/{st['clauses']} 序{st['order']} 錨{st['anchors_sure']}/{st['anchors_cand']} 科{K.count(d['kepan'])} {st['why']}")
    shown = 0
    for d in doc_rows:
        for nd in d["kepan"]:
            if nd["type"] == "科" and shown < 4:
                shown += 1
                print(f"  科 {d['id']} 分{nd['n']}{nd['u']}: " + " ｜ ".join(f"{it['k']}{it['head'] or ''}〔{it['label'][:10]}〕{it['s']}" for it in nd["items"]))
    for d in others:
        st = res[d["id"]]["stats"]
        print(f"  对照 {d['id']} {d['title'][:14]} {res[d['id']]['relation']} 字{len(_G['coms'][d['id']]['C'])} 引確{st['quote_sure']} 釋確{st['gloss_sure']} "
              f"句{st['clauses_hit']}/{st['clauses']} 序{st['order']} 錨{st['anchors_sure']}")


def report(rows, out):
    L = ["| 经 | 字数 | 句 | 注疏 | 有確层注的句 | 有確层引文的句 | 本族確 | 本族候 | 对照被带走的释文（均/最大，n） | 明引召回 长（確/含候） | 明引召回 短 | 判别 精确/召回 |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in rows:
        e = s["exam"]
        def rc(b):
            n = e.get("n" + b, 0)
            return f"{e.get('sure' + b, 0) / n:.3f}/{e.get('any' + b, 0) / n:.3f}（{n}）" if n else "—"
        rel = s["relation"]
        pr = f"{rel['precision']:.2f}/{rel['recall']:.2f}" if rel["precision"] is not None and rel["recall"] is not None else "—"
        L.append(f"| {s['title']} | {s['chars']} | {s['clauses']} | {s['docs']} | {s['clauses_sure']} | {s['clauses_quote']} | "
                 f"{s['frac_sure']} | {s['frac_cand']} | {s['control_mean']}/{s['control_max']}（{s['control_n']}） | {rc('長')} | {rc('短')} | {pr} |")
    L += ["", "| 经 | 門檻 確/候 | 假经数 | 漏牒误归（错/考） | 补认短牒 | 异文 | 略牒链接/落实段 | 科判 节点/挂经 | 再注 确认/候选 | 有引证的句 | v3→v4 有注的句（v3 / v4引 / v4全 / 仅v3 / 仅v4） | 秒 |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in rows:
        v = s["v3"]
        vv = f"{v['v3_hit']} / {v['v4_quote']} / {v['v4_any']} / {v['v3_only']} / {v['v4_only']}（共 {v['matched']} 句）" if v else "—"
        th = s["th"]
        f = lambda x: "無" if x is None else f"{x:.1f}"
        L.append(f"| {s['title']} | {f(th.get('0.01'))}/{f(th.get('0.1'))}，覆盖度 {th.get('cov')} | {s['decoys']} | {s['loo'][1]}/{s['loo'][0]} | {s['weak']} | {s['var']} | "
                 f"{s['links']}/{s['elide']} | {s['kepan_nodes']}/{s['kepan_attached']} | "
                 f"{sum(1 for l in s['layers'] if l['ok'])}/{len(s['layers'])} | {s['cites']} | {vv} | {s['seconds']} |")
    L += ["", "候层释文的原因（字数）：" + "；".join(f"{s['title']} {s['why']}（本族共 {s['own_chars']} 字）" for s in rows)]
    L += ["", "判别明细：" + "；".join(f"{s['title']} 本族 {s['relation']['own']} 对照 {s['relation']['other']}" for s in rows)]
    txt = "\n".join(L) + "\n"
    open(os.path.join(out, "summary.md"), "w", encoding="utf8").write(txt)
    print(txt)


def run(out, only=None, workers=4):
    docs = heldout.fetch()
    byid = {d["id"]: d for d in docs}
    bg = V.Background([norm("".join(t for _, t in d["paras"])) for d in docs if not d["fam"].startswith("com:")])
    os.makedirs(out, exist_ok=True)
    rows = []
    for fam, tid, title, _ in heldout.active_targets():
        if (only and fam not in only) or tid not in byid:
            continue
        rows.append(run_family(fam, tid, title, docs, byid, bg, out, workers))
    report(rows, out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--room", type=int, default=V.ROOM, help="主锚点信息量下限里的疏文自由度（越小越宽）")
    ap.add_argument("--radius", type=int, default=V.RADIUS, help="局部支持度的疏文半径")
    a = ap.parse_args()
    V.ROOM, V.RADIUS = a.room, a.radius
    run(a.out, set(filter(None, a.only.split(","))), a.workers)
