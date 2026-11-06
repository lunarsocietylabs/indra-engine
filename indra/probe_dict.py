# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lunar Society Technologies
"""probe_dict.py — find dictionary-like documents in the corpus and show their paragraph shape (read-only)."""
import collections, json, os, re
import requests

SITE = os.environ.get("INDRA_SITE", "")
man = requests.get(f"{SITE}/data/manifest.json", timeout=180).json()
docs = man.get("docs", man) if isinstance(man, dict) else man
print("classes:", collections.Counter(d.get("class") for d in docs).most_common())
pat = re.compile(r"辭典|辞典|詞典|词典|字典|辭彙|名義|名數|音義|翻譯名|法數|大辭|dictionary|Dictionary", re.I)
hits = [d for d in docs if pat.search((d.get("title") or "") + " " + (d.get("doc_id") or "")) or d.get("class") in ("dictionary", "reference", "glossary")]
print("dictionary-like:", len(hits))
for d in hits[:60]:
    print(" ", d.get("doc_id"), "|", d.get("class"), "|", (d.get("title") or "")[:40], "|", d.get("author"), "|", {k: d[k] for k in d if k not in ("doc_id", "title", "class", "author")})
for d in hits[:6]:
    r = requests.get(f"{SITE}/data/search/{d['doc_id']}.json", timeout=300)
    if r.status_code != 200:
        print("!!", d["doc_id"], r.status_code); continue
    j = r.json(); ps = j.get("paragraphs", [])
    print(f"== {d['doc_id']} paragraphs={len(ps)} keys={sorted(j)[:10]} pkeys={sorted(ps[0]) if ps else None}")
    for p in ps[:3] + ps[len(ps) // 2:len(ps) // 2 + 4]:
        print("   ", json.dumps({k: (v[:120] if isinstance(v, str) else v) for k, v in p.items()}, ensure_ascii=False))
