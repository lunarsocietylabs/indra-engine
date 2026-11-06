# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lunar Society Technologies
"""
fullcanon.py — whole-canon index (libsais) used as the *language background* for the expected-chance score.

  * reads every document from the corpus server (read-only), normalises, encodes to int32 with per-document sentinels
  * builds SA + LCP natively (about 270M characters; a few GB of RAM)
  * count(s, exclude=...) = occurrences of s in the canon, excluding chosen documents (target sutra, its commentaries,
    the Agama controls) so the background never contains the answers or the control texts.
"""
import collections, concurrent.futures, os, re, time

import numpy as np

from .norm import norm
from .native_sa import NativeIndex

SITE = os.environ.get("INDRA_SITE", "")
SENT0 = 1 << 16                    # sentinel ids start here; real characters get ids < 65536 in order of first appearance
MAXRANGE = 3_000_000


class FullCanon:
    def __init__(self, threads=0):
        import requests
        if not SITE:
            raise SystemExit("set INDRA_SITE")
        t0 = time.time()
        man = requests.get(f"{SITE}/data/manifest.json", timeout=180).json()
        allm = man.get("docs", man) if isinstance(man, dict) else man
        self.meta = [d for d in allm if d.get("doc_id")]
        tab = np.full(0x110000, -1, dtype=np.int32)
        nxt = [0]
        parts, ids, ok = {}, {}, []

        def one(d):
            for attempt in range(5):                       # 网络偶发断流：重试，确定性不受影响（内容相同）
                try:
                    r = requests.get(f"{SITE}/data/search/{d['doc_id']}.json", timeout=300)
                    if r.status_code != 200 or r.headers.get("content-type", "").startswith("text/html"):
                        return d["doc_id"], None
                    r.json()
                    break
                except (requests.RequestException, ValueError):
                    if attempt == 4:
                        raise
                    time.sleep(2 * (attempt + 1))
            s = "".join(norm(p.get("t") or "") for p in r.json().get("paragraphs", []))
            return d["doc_id"], np.frombuffer(s.encode("utf-32-le"), dtype=np.uint32)
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
            for did, arr in ex.map(one, self.meta):
                if arr is None or len(arr) == 0:
                    continue
                for u in np.unique(arr):                       # 单线程在此处理字符表，保证确定性
                    if tab[u] < 0:
                        tab[u] = nxt[0]; nxt[0] += 1
                parts[did] = tab[arr]
        assert nxt[0] < SENT0, "alphabet too large"
        self.tab = tab
        self.docs = [d for d in self.meta if d["doc_id"] in parts]
        n = sum(len(parts[d["doc_id"]]) + 1 for d in self.docs)
        T = np.empty(n, dtype=np.int32)
        starts, pos = [], 0
        for i, d in enumerate(self.docs):
            a = parts.pop(d["doc_id"])
            T[pos:pos + len(a)] = a
            starts.append(pos); pos += len(a)
            T[pos] = SENT0 + i; pos += 1
        self.starts = np.array(starts, dtype=np.int64)
        self.lens = np.diff(np.append(self.starts, n)) - 1
        self.id_of = {d["doc_id"]: i for i, d in enumerate(self.docs)}
        self.cls = np.array([d.get("class") or "UNKNOWN" for d in self.docs])
        self.n = n
        self.t_fetch = time.time() - t0
        t1 = time.time()
        self.ix = NativeIndex(T, SENT0 + len(self.docs), threads=threads, want_lcp=True)
        self.t_index = time.time() - t1

    # ---- 查询
    def encode(self, s):
        out = []
        for ch in s:
            o = ord(ch)
            c = self.tab[o] if o < len(self.tab) else -1
            if c < 0:
                return None
            out.append(c)
        return out

    def doc_index(self, pos):
        return np.searchsorted(self.starts, pos, side="right") - 1

    def count(self, s, include):
        """s 在 include[doc] 为 True 的文献中的出现次数；include 是长度 = 文献数的布尔数组。"""
        q = self.encode(s)
        if q is None:
            return 0
        lo, hi = self.ix.range(q)
        if hi <= lo:
            return 0
        step = max(1, (hi - lo) // MAXRANGE)
        sel = self.ix.SA[lo:hi:step]
        return int(include[self.doc_index(sel)].sum()) * step

    def chars(self, include):
        return int(self.lens[include].sum())

    def mask(self, exclude_ids=(), classes=None):
        m = np.ones(len(self.docs), dtype=bool) if classes is None else np.isin(self.cls, list(classes))
        for did in exclude_ids:
            i = self.id_of.get(did)
            if i is not None:
                m[i] = False
        return m

    def stats(self, nq=2000, seed=1):
        rng = np.random.default_rng(seed)
        lat, occ = [], []
        T = self.ix.T
        for _ in range(nq):
            L = int(rng.integers(4, 9)); p = int(rng.integers(0, self.n - L))
            q = T[p:p + L]
            if (q >= SENT0).any():
                continue
            t = time.perf_counter(); lo, hi = self.ix.range(q); lat.append((time.perf_counter() - t) * 1000); occ.append(hi - lo)
        lat.sort()
        lcp = self.ix.lcp
        return {"chars": self.n, "docs": len(self.docs), "alphabet": int((self.tab >= 0).sum()),
                "t_fetch_s": round(self.t_fetch, 1), "t_index_s": round(self.t_index, 1),
                "q_p50_ms": round(lat[len(lat) // 2], 4), "q_p99_ms": round(lat[int(len(lat) * .99)], 4), "q_mean_occ": round(float(np.mean(occ)), 1),
                "redundant_8": round(float((lcp >= 8).mean()), 4), "redundant_16": round(float((lcp >= 16).mean()), 4),
                "redundant_32": round(float((lcp >= 32).mean()), 4), "redundant_64": round(float((lcp >= 64).mean()), 4)}
