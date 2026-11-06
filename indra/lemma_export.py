# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lunar Society Technologies
"""
lemma_export.py — 把确定性匹配结果导出成"句句相连"数据：经文每一小句 → 各部注疏里逐字引到它的那一段（含上下文）。

输出（每部经一个 JSON，纯展示用，全部是原书逐字文字）：
  {"id","title","paras":[{"lb","clauses":[{"t":小句原文,"n":引到它的注疏部数,"items":[{"d","t","a","lb","m","s"}]}]}]}
  items 每部注疏取最长的一处；s = 注疏原文（带标点）片段，命中的经文用【】标出。
"""
import bisect, json, os, re, unicodedata

from .norm import norm, PUNCT_RE

CLAUSE_END = "，。；：？！、,.;:?!\n"
DYN = ["姚秦", "後秦", "晉", "東晉", "劉宋", "梁", "陳", "隋", "唐", "五代", "宋", "遼", "金", "元", "明", "清", "民國", "近代", "現代", "日本", "朝鮮", "新羅", "高麗", "高句麗", "天竺", "印度"]


def dyn_rank(author):
    a = author or ""
    for i, d in enumerate(DYN):
        if a.startswith(d):
            return i
    m = re.match(r"^[一-鿿]{1,3}", a)
    return 50 if m else 99


def split_clauses(raw):
    """(clause_raw, norm_len) 列表；若逐句规范化长度之和与整段不等，退回整段一句。"""
    # 简单切分：遇到句读符号收句
    out, cur = [], ""
    for ch in raw:
        cur += ch
        if ch in CLAUSE_END:
            out.append(cur); cur = ""
    if cur:
        out.append(cur)
    res = [(c, len(norm(c))) for c in out]
    res = [r for r in res if r[1] > 0] or [(raw, len(norm(raw)))]
    if sum(n for _, n in res) != len(norm(raw)):
        return [(raw, len(norm(raw)))]
    return res


def raw_index_map(raw):
    """规范化字符序号 -> raw 下标（逐字 NFKC 近似；与 norm(raw) 长度不等时返回 None）。"""
    idx = []
    for i, ch in enumerate(raw):
        n = norm(ch)
        idx.extend([i] * len(n))
    return idx if len(idx) == len(norm(raw)) else None


def export(C, target, spans_acc, outdir, tid, title, ctx=40, max_items=40):
    """spans_acc: [(x, t_rel)]，x 为 spans_for 的 span，t_rel 为命中在目标经（规范化）中的起点。"""
    os.makedirs(outdir, exist_ok=True)
    paras, flat = [], []                       # flat: (norm_start, norm_end, para_idx, clause_idx)
    base = 0
    for pi, raw in enumerate(target["praw"]):
        n = len(norm(raw))
        if n == 0:
            continue
        clauses = []
        off = base
        for craw, cn in split_clauses(raw):
            clauses.append({"t": craw.strip(), "n": 0, "docs": {}})
            flat.append((off, off + cn, len(paras), len(clauses) - 1))
            off += cn
        paras.append({"lb": target["plbs"][pi], "clauses": clauses})
        base += n
    starts = [f[0] for f in flat]
    maps = {}
    for x, t in spans_acc:
        a, b = t, t + x["m"]
        k = bisect.bisect_right(starts, a) - 1
        d = x["doc"]
        pi_c = C.para_of(d, x["p"])
        key = (d["id"], pi_c)
        if key not in maps:
            maps[key] = raw_index_map(d["praw"][pi_c])
        mp = maps[key]
        rawtxt = d["praw"][pi_c]
        o = x["p"] - d["pstarts"][pi_c]
        if mp and o + x["m"] <= len(mp):
            r0, r1 = mp[o], mp[o + x["m"] - 1] + 1
            s = rawtxt[max(0, r0 - ctx):r0] + "【" + rawtxt[r0:r1] + "】" + rawtxt[r1:r1 + ctx]
        else:
            s = "【" + x["s"] + "】"
        item = {"d": d["id"], "t": re.sub(r"^.*No\.\s*[0-9A-Za-z]+\s*", "", d.get("title") or ""), "a": d.get("author") or "",
                "lb": d["plbs"][pi_c], "m": x["m"], "s": s}
        while k < len(flat) and flat[k][0] < b:
            f = flat[k]
            if min(b, f[1]) - max(a, f[0]) >= min(4, x["m"], f[1] - f[0]):   # 短句（受、想、舍利子）只要被完整覆盖即算
                cl = paras[f[2]]["clauses"][f[3]]
                cur = cl["docs"].get(d["id"])
                if cur is None or item["m"] > cur["m"]:
                    cl["docs"][d["id"]] = item
            k += 1
    docs_ct = set()
    for p in paras:
        for cl in p["clauses"]:
            items = sorted(cl.pop("docs").values(), key=lambda it: (dyn_rank(it["a"]), -it["m"], it["d"]))
            cl["n"] = len(items)
            cl["items"] = items[:max_items]
            docs_ct.update(it["d"] for it in items)
    out = {"id": tid, "title": title, "n_docs": len(docs_ct), "paras": paras}
    path = os.path.join(outdir, f"{tid}.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    return {"id": tid, "title": title, "clauses": sum(len(p["clauses"]) for p in paras),
            "with_hits": sum(1 for p in paras for c in p["clauses"] if c["n"]), "docs": len(docs_ct),
            "bytes": os.path.getsize(path)}
