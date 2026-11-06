# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lunar Society Technologies
"""
dfb.py — 丁福保《佛學大辭典》作为"基因注释"：在全藏上实测它能提供什么。

数据：DDBC/DILA TEI 版（正文公有领域；TEI 版 CC BY-SA 2.5 TW，署名 Simon Wiles / DDBC Glossaries Team；
      源自 CBETA 电子档，再源自佛教电脑资讯库功德会）。运行时从公开仓库取，不提交进本仓库。

测量：
  1. 词条覆盖：全藏多少文字落在至少一个丁福保词头里（按词长、按类别）。
  2. 引证核对：释义里"某經曰：「…」"的引文，有多少能在全藏逐字找到；有多少恰在所引的那部书里找到。
  3. 别名/互见：又作、梵名、譯曰、見某條 能把多少词条连成同一概念。
  4. 名數分解：名數类词条（五蘊、六度…）能拆出多少组成词，其中多少本身也是词条。
"""
import collections, json, os, re, sys, time
import xml.etree.ElementTree as ET

import numpy as np

from .fullcanon import FullCanon
from .norm import norm

NS = {"t": "http://www.tei-c.org/ns/1.0"}
CN_NUM = "一二三四五六七八九十百千上中下"
QUOTE = re.compile(r"([一-鿿]{1,14}?)([" + CN_NUM + r"]*)(?:曰|云)[：:]?「([^」]{4,400})」")


def load(path):
    root = ET.parse(path).getroot()
    out = []
    for e in root.findall(".//t:entry", NS):
        f = e.find("t:form", NS)
        hw = "".join(f.itertext()).strip() if f is not None else ""
        out.append({"hw": hw, "usg": ["".join(u.itertext()).strip() for u in e.findall(".//t:usg", NS)],
                    "defs": ["".join(d.itertext()).strip() for d in e.findall(".//t:def", NS)],
                    "xr": ["".join(x.itertext()).strip() for x in e.findall(".//t:xr", NS)]})
    return out


def main(xml, out):
    t0 = time.time()
    E = load(xml)
    F = FullCanon()
    st = F.stats(); print("FULLCANON", json.dumps(st), flush=True)
    R = {"entries": len(E), "canon_chars": F.n}
    allmask = np.ones(len(F.docs), dtype=bool)
    lex_ids = {d["doc_id"] for d in F.docs if d.get("class") == "lexicon"}
    nolex = F.mask(list(lex_ids))
    SA = F.ix.SA
    starts = F.starts
    # ---- 1. 词条覆盖（差分数组标记被覆盖的位置）
    diff_all = np.zeros(F.n + 1, dtype=np.int32)
    by_len = collections.Counter(); occ_by_cat = collections.Counter(); ent_by_cat = collections.Counter()
    hw_occ = {}
    for e in E:
        h = norm(e["hw"])
        if len(h) < 2 or h in hw_occ:
            continue
        q = F.encode(h)
        if q is None:
            hw_occ[h] = 0; continue
        lo, hi = F.ix.range(q)
        pos = SA[lo:hi]
        if len(pos):
            d = np.searchsorted(starts, pos, side="right") - 1
            pos = pos[nolex[d]]
        hw_occ[h] = int(len(pos))
        if len(pos):
            diff_all[pos] += 1                      # 同一词头的出现位置互不重复，可直接向量化
            diff_all[pos + len(h)] -= 1
            by_len[min(len(h), 6)] += len(pos)
            for u in set(e["usg"]):
                occ_by_cat[u] += len(pos); ent_by_cat[u] += 1
    cov = np.cumsum(diff_all[:-1]) > 0
    text_mask = F.ix.T < (1 << 16)
    tot = int((text_mask & np.repeat(nolex, F.lens + 1)[:F.n]).sum())
    covered = int((cov & text_mask).sum())
    del diff_all, cov
    found = sum(1 for v in hw_occ.values() if v > 0)
    R["coverage"] = {"headwords_len_ge2": len(hw_occ), "found_in_canon": found, "canon_chars_non_lexicon": tot,
                     "chars_covered": covered, "share_covered": round(covered / max(1, tot), 4),
                     "occurrences_by_len": dict(sorted(by_len.items())),
                     "top_categories_by_occurrence": occ_by_cat.most_common(20),
                     "entries_found_by_category": ent_by_cat.most_common(20),
                     "most_frequent": sorted(hw_occ.items(), key=lambda kv: -kv[1])[:30]}
    print("COVERAGE", json.dumps(R["coverage"], ensure_ascii=False), flush=True)
    # ---- 2. 引证核对
    titles = [(d["doc_id"], norm(re.sub(r"^.*No\.\s*[0-9A-Za-z]+\s*", "", d.get("title") or ""))) for d in F.docs]
    sub2docs = collections.defaultdict(set)               # 书名的所有子串 -> 文献；用"引文出处的最长后缀"找书
    for i, (_, t) in enumerate(titles):
        t = re.sub(r"[（(].*$", "", t)
        for a in range(len(t)):
            for b in range(a + 2, min(len(t), a + 14) + 1):
                sub2docs[t[a:b]].add(i)
    n_q = n_found = n_at_src = n_src_known = 0; by_src = collections.Counter(); miss_samples = []
    for e in E:
        for dtext in e["defs"]:
            for src, juan, quote in QUOTE.findall(dtext):
                qn = norm(re.sub(r"（[^）]*）", "", quote))
                if len(qn) < 8:
                    continue
                n_q += 1
                enc = F.encode(qn[:64])
                if enc is None:
                    continue
                lo, hi = F.ix.range(enc)
                if hi <= lo:
                    if len(miss_samples) < 12:
                        miss_samples.append(f"{e['hw']}｜{src}{juan}｜{quote[:40]}")
                    continue
                n_found += 1
                cand = set()
                for k in range(0, max(0, len(src) - 1)):
                    if src[k:] in sub2docs and len(src) - k >= 2:
                        cand = sub2docs[src[k:]]; break
                if cand:
                    n_src_known += 1
                    ds = set((np.searchsorted(starts, SA[lo:min(hi, lo + 5000)], side="right") - 1).tolist())
                    if ds & cand:
                        n_at_src += 1; by_src[src] += 1
    R["citations"] = {"quotes_ge8": n_q, "found_verbatim_in_canon": n_found, "share_found": round(n_found / max(1, n_q), 4),
                      "source_title_resolvable": n_src_known, "found_in_the_cited_book": n_at_src,
                      "share_at_cited_book_of_resolvable": round(n_at_src / max(1, n_src_known), 4),
                      "top_verified_sources": by_src.most_common(20), "not_found_samples": miss_samples}
    print("CITATIONS", json.dumps(R["citations"], ensure_ascii=False), flush=True)
    # ---- 3. 别名 / 互见
    hws = {norm(e["hw"]) for e in E}
    alias_pat = re.compile(r"(?:又作|又曰|亦云|亦名|亦作|略曰|略云|具名|具云|舊譯|新譯|譯曰|舊稱|新稱)[：:]?([一-鿿]{2,12})")
    edges = set()
    for e in E:
        a = norm(e["hw"])
        for x in e["xr"]:
            b = norm(x)
            if b in hws and b != a:
                edges.add(tuple(sorted((a, b))))
        for dtext in e["defs"]:
            for m in alias_pat.findall(dtext):
                for k in range(len(m), 1, -1):              # 取最长的、本身也是词条的前缀
                    if m[:k] in hws and m[:k] != a:
                        edges.add(tuple(sorted((a, m[:k])))); break
    parent = {}
    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    for a, b in edges:
        parent[find(a)] = find(b)
    groups = collections.Counter(find(h) for h in parent)
    R["aliases"] = {"edges": len(edges), "entries_in_groups": len(parent), "groups": len(groups),
                    "largest_groups": [[h for h in parent if find(h) == g][:12] for g, _ in groups.most_common(5)],
                    "舍利子_group": sorted(h for h in parent if "舍利子" in parent and find(h) == find("舍利子"))[:20]}
    print("ALIASES", json.dumps(R["aliases"], ensure_ascii=False), flush=True)
    # ---- 4. 名數分解
    num = [e for e in E if "名數" in e["usg"]]
    comp_total = comp_entries = 0; examples = []
    for e in num:
        body = "".join(e["defs"])
        parts = re.findall(r"[一二三四五六七八九十]+(?:者|、)?([一-鿿]{1,8}?)(?:[，。、；]|$)", body)
        parts = [p for p in parts if 1 <= len(p) <= 8]
        hit = [p for p in parts if norm(p) in hws]
        comp_total += len(parts); comp_entries += len(hit)
        if len(examples) < 8 and len(parts) >= 3:
            examples.append({"hw": e["hw"], "parts": parts[:12], "parts_that_are_entries": hit[:12]})
    R["numbered_lists"] = {"entries": len(num), "components": comp_total, "components_that_are_entries": comp_entries, "examples": examples}
    print("NUMBERED", json.dumps(R["numbered_lists"], ensure_ascii=False), flush=True)
    R["elapsed_s"] = round(time.time() - t0, 1)
    os.makedirs(out, exist_ok=True)
    json.dump(R, open(os.path.join(out, "dfb.json"), "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "/tmp/dfb")
