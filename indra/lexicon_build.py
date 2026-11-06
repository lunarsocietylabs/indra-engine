# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lunar Society Technologies
"""
lexicon_build.py — 把丁福保《佛學大辭典》切成静态分片，供 Cloudflare 上的浏览器端"划词即释义"使用。

输出（目录 out/）：
  heads.json          词头表（归一化后去重、按长度降序之外的稳定序），浏览器整体加载做划词识别
  s/<NN>.json         NN = 词头 sha1 前 2 位十六进制（256 片）；{词头: {h:原词头, c:[类别], d:[释义段], x:[互见]}}
  manifest.json       条数、分片数、署名与授权说明
数据：DDBC/DILA TEI 版（正文公有领域；TEI 版 CC BY-SA 2.5 TW）。只做呈现，不改写释义文字。
同一词头出现多条（异义）时全部保留，按原顺序。
"""
import hashlib, json, os, sys

from .dfb import load
from .norm import norm

CREDIT = ("丁福保《佛學大辭典》，DDBC/DILA TEI 版（Simon Wiles / DDBC Glossaries Team），"
          "源自 CBETA 电子档与佛教电脑资讯库功德会；TEI 版 CC BY-SA 2.5 TW。")


def shard_of(h):
    return hashlib.sha1(h.encode("utf-8")).hexdigest()[:2]


def build(xml, out):
    E = load(xml)
    os.makedirs(os.path.join(out, "s"), exist_ok=True)
    shards, heads = {}, {}
    for e in E:
        h = norm(e["hw"])
        if len(h) < 1 or not e["defs"]:
            continue
        rec = {"h": e["hw"], "c": e["usg"], "d": e["defs"], "x": e["xr"]}
        shards.setdefault(shard_of(h), {}).setdefault(h, []).append(rec)
        heads[h] = heads.get(h, 0) + 1
    for k, v in shards.items():
        with open(os.path.join(out, "s", k + ".json"), "w", encoding="utf-8") as f:
            json.dump(v, f, ensure_ascii=False, separators=(",", ":"))
    hl = sorted(heads)
    with open(os.path.join(out, "heads.json"), "w", encoding="utf-8") as f:
        json.dump(hl, f, ensure_ascii=False, separators=(",", ":"))
    man = {"entries": len(E), "headwords": len(hl), "shards": len(shards), "credit": CREDIT,
           "license": "CC BY-SA 2.5 TW (TEI edition); text public domain"}
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    sizes = [os.path.getsize(os.path.join(out, "s", k + ".json")) for k in shards]
    print(json.dumps({**man, "heads_bytes": os.path.getsize(os.path.join(out, "heads.json")),
                      "shard_max": max(sizes), "shard_avg": sum(sizes) // len(sizes)}, ensure_ascii=False))


if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2])
