# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lunar Society Technologies
"""
genome.py — 全藏"基因组"测量：
  基因 = 在 ≥2 部文献中逐字出现、≥L 字的最长重复片段（左右都无法再延伸的重复，即后缀树的极大重复）。
  同时测"词条层"：库内辞书（翻譯名義集【…】、一切經音義、三藏法數等）能给出多少词条，其中多少在经藏里真的出现。
"""
import collections, json, os, re, sys, time

import numpy as np
import requests

from .fullcanon import FullCanon, SITE
from .norm import norm

LEXICA = {  # doc_id -> headword rule
    "T54n2131": "bracket",       # 翻譯名義集：【詞】釋文
    "T54n2128": "shortpara",     # 慧琳 一切經音義：詞條獨佔一段
    "T54n2129": "shortpara",     # 希麟 續一切經音義
    "C56n1163": "shortpara", "C57n1163": "shortpara",   # 玄應 一切經音義
    "K34n1257": "shortpara", "K35n1257": "shortpara",   # 可洪 隨函錄
    "B22n0117": "shortpara",     # 三藏法數（丁福保重校）
    "P181n1615": "shortpara", "P182n1615": "shortpara", "P183n1615": "shortpara",  # 大明三藏法數
    "T54n2127": "shortpara",     # 釋氏要覽
    "T54n2130": "lead",          # 翻梵語：詞 + 譯曰…
    "T54n2135": "shortpara",     # 梵語雜名
    "X32n0620": "shortpara", "X64n1262": "shortpara",   # 法華經大成音義、禪林寶訓音義
}


def headwords(rule, paras):
    out = []
    for p in paras:
        t = p.get("t") or ""
        if rule == "bracket":
            out += re.findall(r"【([^】]{1,16})】", t)
        elif rule == "shortpara":
            s = norm(t)
            if 2 <= len(s) <= 8 and not re.search(r"[卷第品序]$", s):
                out.append(s)
        elif rule == "lead":
            m = re.match(r"^([^。，、；：（(]{2,10})[（(。，]", t)
            if m:
                out.append(m.group(1))
    return [norm(h) for h in out if 2 <= len(norm(h)) <= 12]


def main(out):
    t0 = time.time()
    F = FullCanon()
    print("FULLCANON", json.dumps(F.stats(), ensure_ascii=False), flush=True)
    doc_of_pos = np.searchsorted(F.starts, np.arange(F.n, dtype=np.int64), side="right").astype(np.int32) - 1
    R = {"canon": {"chars": F.n, "docs": len(F.docs)}, "genome": {}}
    for L in (8, 16, 32):
        t = time.time()
        g = F.ix.genome_stats(doc_of_pos, L)
        g["t_s"] = round(time.time() - t, 1)
        R["genome"][L] = g
        print("GENOME", L, json.dumps(g), flush=True)
    del doc_of_pos
    # ---- 词条层
    canon_only = F.mask(classes=("canon", "treatise", "vinaya"))
    terms = collections.Counter(); src = collections.defaultdict(set)
    for did, rule in LEXICA.items():
        r = requests.get(f"{SITE}/data/search/{did}.json", timeout=300)
        if r.status_code != 200:
            continue
        hs = headwords(rule, r.json().get("paragraphs", []))
        for h in hs:
            terms[h] += 1; src[h].add(did)
        R.setdefault("lexica", {})[did] = len(set(hs))
    uniq = list(terms)
    inc = [h for h in uniq if F.count(h, canon_only) > 0]
    by_len = collections.Counter(len(h) for h in inc)
    R["terms"] = {"headwords": len(uniq), "in_canon_text": len(inc), "by_len": dict(sorted(by_len.items())),
                  "multi_source": sum(1 for h in uniq if len(src[h]) >= 2),
                  "sample": [h for h in inc if len(src[h]) >= 2][:60]}
    print("TERMS", json.dumps(R["terms"], ensure_ascii=False), flush=True)
    for probe in ("涅槃", "舍利子", "受想行識", "五蘊", "般若", "七重羅網", "八功德水", "阿耨多羅三藐三菩提", "明觀行境"):
        R.setdefault("probe", {})[probe] = {"in_lexica": terms.get(norm(probe), 0), "canon_occ": F.count(norm(probe), canon_only),
                                            "all_occ": F.count(norm(probe), np.ones(len(F.docs), dtype=bool))}
    print("PROBE", json.dumps(R["probe"], ensure_ascii=False), flush=True)
    R["elapsed_s"] = round(time.time() - t0, 1)
    os.makedirs(out, exist_ok=True)
    json.dump(R, open(os.path.join(out, "genome.json"), "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/genome")
