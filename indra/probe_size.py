# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lunar Society Technologies
"""probe_size.py — how big is the whole corpus? (manifest only + a 40-document sample; read-only)"""
import json, os, random, collections
import requests
from .norm import norm

SITE = os.environ.get("INDRA_SITE", "")
man = requests.get(f"{SITE}/data/manifest.json", timeout=180).json()
docs = man.get("docs", man) if isinstance(man, dict) else man
print("docs:", len(docs))
print("keys:", sorted({k for d in docs[:50] for k in d})[:30])
print("by class:", collections.Counter(d.get("class") for d in docs).most_common(12))
sizes = [d.get("chars") or d.get("size") or d.get("n_chars") for d in docs]
if any(s for s in sizes):
    print("sum of size field:", sum(s for s in sizes if s))
rnd = random.Random(1)
sample = rnd.sample([d for d in docs if d.get("doc_id")], 40)
tot = 0; ok = 0
for d in sample:
    r = requests.get(f"{SITE}/data/search/{d['doc_id']}.json", timeout=300)
    if r.status_code != 200 or r.headers.get("content-type", "").startswith("text/html"):
        continue
    ok += 1
    tot += sum(len(norm(p.get("t") or "")) for p in r.json().get("paragraphs", []))
print(f"sample {ok}/40 docs: {tot:,} chars -> mean {tot // max(ok, 1):,}/doc -> est. whole {tot // max(ok, 1) * len(docs):,} chars")
