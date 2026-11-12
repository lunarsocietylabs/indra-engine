#!/usr/bin/env python3
"""V4 第三层注疏结果：R2 私有源 → R2 前端 JSON（全程 R2 HTTP API，标准库 only）。

移植自私有仓 engine/v4/export_site.py，把「写本地目录」换成「用 R2 HTTP API 直接 PUT」：
    fetch()     取 <docid>.json.gz，404 时回退到 .part000/.part001/… 顺序拼接
    convert()   paras[].clauses[].items[] + 逐条 k/r，按 TOP 截断
    反链        c/<docid>.json，值 = {"t":[[tid,title]…], "e":[[c,n,ti,k,k2,確],…]}，e 里只存 t 的下标
    大文档分片  <docid>.json 只留索引（paras=[] + parts=N），正文写 <docid>.pNNN.json
    index.json  字段 id/title/chars/docs/clauses/hit，按 docs 降序

用法：
    python3 tools/export_v4_r2.py                                   # 探针默认：写 data/v4-preview-probe/
    python3 tools/export_v4_r2.py --ids F27n1061+X26n0540 --prefix data/v4-preview-probe/
    python3 tools/export_v4_r2.py --tag 43175083562 --prefix data/v4-preview/ --allow-prod
    python3 tools/export_v4_r2.py --tag 42879981497,42892321782,43175083562 --prefix data/v4-preview/ --allow-prod
        # 注意：index.json 与 c/*.json 是「一次运行内的全量聚合」，不是增量合并。
        # 只跑一个新 tag 会把这些文件覆盖成该 tag 的子集。

安全闸：默认前缀是探针目录；前缀落在 data/v4-preview/ 之内且没给 --allow-prod 时直接退出 2。
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

# ---- 与 export_site.py 完全一致的常量 ----
TOP = 10
SHRINK = ((40, 80), (20, 70), (10, 60), (6, 40), (3, 30), (1, 0))   # (TOP, snippet 长度)
BIG = 6_000_000          # 缩小目标：top>10 时 6 MB，否则 22 MB
HARD = 22_000_000        # 超过就必须分片
PART_MAX = 15_000_000    # 单个 .pNNN.json 的上限

API = "https://api.cloudflare.com/client/v4"
DEFAULT_TOKEN_FILE = "/Users/asher/Documents/Lunar Society/.cf-token"
DEFAULT_SRC_BUCKET = "lst-private-docs"
DEFAULT_DST_BUCKET = "ai-canon-site"
DEFAULT_SRC_PREFIX = "docs/code-sync/engine-v4-out/"
PROD_PREFIX = "data/v4-preview/"


# ---------------------------------------------------------------- R2 HTTP
class R2:
    """最小 R2 对象 API 客户端（GET/PUT/LIST），带指数退避重试。"""

    def __init__(self, token, acc, retries=3, max_backoff=8.0, timeout=180):
        self.token = token
        self.acc = acc
        self.retries = retries
        self.max_backoff = max_backoff
        self.timeout = timeout

    def _url(self, bucket, key=None, query=None):
        u = f"{API}/accounts/{self.acc}/r2/buckets/{urllib.parse.quote(bucket, safe='')}/objects"
        if key is not None:
            u += "/" + urllib.parse.quote(key, safe="/")
        if query:
            u += "?" + urllib.parse.urlencode(query)
        return u

    def _call(self, method, url, body=None, content_type=None, allow404=False):
        last = None
        for attempt in range(self.retries + 1):
            try:
                req = urllib.request.Request(url, data=body, method=method)
                req.add_header("Authorization", "Bearer " + self.token)
                if body is not None:
                    req.add_header("Content-Type", content_type or "application/octet-stream")
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return r.read()
            except urllib.error.HTTPError as e:
                if e.code == 404 and allow404:
                    return None
                # 4xx（429 除外）不重试：重试也不会变好
                if 400 <= e.code < 500 and e.code != 429:
                    raise
                last = e
            except Exception as e:            # 网络中断 / 超时 / DNS
                last = e
            if attempt < self.retries:
                time.sleep(min(self.max_backoff, float(2 ** attempt)))
        raise last

    def get(self, bucket, key, allow404=False):
        return self._call("GET", self._url(bucket, key), allow404=allow404)

    def put(self, bucket, key, body, content_type="application/octet-stream"):
        raw = self._call("PUT", self._url(bucket, key), body=body, content_type=content_type)
        try:
            d = json.loads(raw or b"{}")
        except Exception:
            return raw
        if not d.get("success", False):
            raise RuntimeError("PUT %s 失败: %s" % (key, raw[:400]))
        return d

    def list(self, bucket, prefix):
        out, cursor = [], None
        while True:
            q = {"prefix": prefix, "per_page": "1000"}
            if cursor:
                q["cursor"] = cursor
            d = json.loads(self._call("GET", self._url(bucket, query=q)))
            out += d.get("result") or []
            cursor = (d.get("result_info") or {}).get("cursor")
            if not cursor or not d.get("result"):
                return out


def account_id_from_api(token):
    req = urllib.request.Request(API + "/accounts")
    req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.loads(r.read())
    res = d.get("result") or []
    if not res:
        raise RuntimeError("token 看不到任何账户: %s" % json.dumps(d)[:300])
    return res[0]["id"]


# ---------------------------------------------------------------- 移植主体
def fetch(r2, src_bucket, src_prefix, tid):
    """与 export_site.fetch 等价：先整取；404 则按 .partNNN 顺序拼接。"""
    base = src_prefix + tid + ".json.gz"
    b = r2.get(src_bucket, base, allow404=True)
    if b is not None:
        return b
    out, k = b"", 0
    while True:
        p = r2.get(src_bucket, base + ".part%03d" % k, allow404=True)
        if p is None:
            break
        out += p
        k += 1
    return out or None


def convert(j, top=TOP, snip=80):
    """逐行照抄 export_site.convert（含 best 的择优比较与排序键）。"""
    sm, docs, cls = j["summary"], j["docs"], j["clauses"]
    paras, cur, last = [], None, object()
    for c in cls:
        best = {}
        for it in c["items"]:
            d = docs[it["d"]]
            o = best.get(d["id"])
            if o is None or (it["r"] == "確") > (o["r"] == "確") or (it["r"] == o["r"] and it["n"] > o["m"]):
                best[d["id"]] = {"d": d["id"], "t": d["title"], "a": d["author"], "k": it["k"],
                                 "r": it["r"], "m": it["n"], "c": it["c"], "s": it.get("x", "")[:snip]}
        items = sorted(best.values(), key=lambda x: (x["r"] != "確", -x["m"]))[:top]
        cl = {"t": c["t"], "n": c["n"], "nq": c["nq"], "nc": c["nc"], "items": items}
        if c["lb"] != last:
            cur = {"lb": c["lb"], "clauses": []}
            paras.append(cur)
            last = c["lb"]
        cur["clauses"].append(cl)
    return {"id": sm["id"], "title": sm["title"], "chars": sm["chars"],
            "docs": [{"id": d["id"], "title": d["title"], "author": d["author"], "rel": d["relation"]} for d in docs],
            "paras": paras}


def dumps(o):
    return json.dumps(o, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def build(j):
    """缩水循环 + 大文档分片。返回 (主文件对象 w, 主文件字节, 分片块 [ [para…] … ] 或 None)。"""
    txt = b""
    w = None
    for top, snip in SHRINK:
        w = convert(j, top, snip)
        txt = dumps(w)
        if len(txt) < (BIG if top > 10 else HARD):
            break
    chunks = None
    if len(txt) >= HARD:                                   # 仍过大：按段拆成 .pNNN.json
        chunks, cur, size = [], [], 0
        for p_ in w["paras"]:
            ps = len(dumps(p_))
            if cur and size + ps > PART_MAX:
                chunks.append(cur)
                cur, size = [], 0
            cur.append(p_)
            size += ps
        chunks.append(cur)
        w = dict(w, paras=[], parts=len(chunks))
        txt = dumps(w)
    return w, txt, chunks


def put_object(r2, bucket, key, body, dry_run, stats):
    stats["objects"] += 1
    stats["bytes"] += len(body)
    if dry_run:
        print("  DRY-PUT %s %d" % (key, len(body)))
        return
    r2.put(bucket, key, body, content_type="application/json; charset=utf-8")


def read_token(args):
    """优先级：--token-file（存在的）> CF_R2_TOKEN > 默认 token 文件。"""
    if args.token_file:
        p = os.path.expanduser(args.token_file)
        if os.path.exists(p):
            return open(p, encoding="utf-8").read().strip()
        # 默认路径不存在时允许回落到环境变量（GitHub Actions 用）
        if os.path.abspath(p) != os.path.abspath(os.path.expanduser(DEFAULT_TOKEN_FILE)):
            raise SystemExit("token 文件不存在: %s" % p)
    env = (os.environ.get("CF_R2_TOKEN") or "").strip()
    if env:
        return env
    raise SystemExit("没拿到 token：既没有 --token-file，也没有 CF_R2_TOKEN 环境变量")


def normalized_prefix(p):
    p = (p or "").strip().lstrip("/")
    if not p:
        raise SystemExit("--prefix 不能为空")
    if not p.endswith("/"):
        p += "/"
    return p


def run(r2, args):
    t0 = time.time()
    dst_prefix = normalized_prefix(args.prefix)
    src_root = args.src_prefix.strip().lstrip("/")
    if not src_root.endswith("/"):
        src_root += "/"
    tags = [t.strip() for t in args.tag.split(",") if t.strip()]
    if not tags:
        raise SystemExit("--tag 不能为空")
    stats = {"objects": 0, "bytes": 0}

    # --- plan → (tag, target)。与 export_site 一致：families 顺序，跨 tag 去重 ---
    plans = {}
    for tag in tags:
        plans[tag] = [f["target"] for f in
                      json.loads(r2.get(args.src_bucket, src_root + tag + "/plan.json"))["families"]]

    if args.ids:                                   # --ids 按给定顺序处理；归属 tag 由 plan 反查
        jobs = []
        for tid in [x for x in args.ids.split("+") if x]:
            owner = next((t for t in tags if tid in plans[t]), tags[0])
            if owner == tags[0] and tid not in plans[tags[0]]:
                print("[warn] 不在任何 plan 里的 id（仍按 %s 处理）: %s" % (owner, tid), file=sys.stderr)
            jobs.append((owner, tid))
    else:
        jobs, plan_seen = [], set()
        for tag in tags:
            for tid in plans[tag]:
                if tid in plan_seen:
                    continue
                plan_seen.add(tid)
                jobs.append((tag, tid))
    if args.limit and args.limit > 0:
        jobs = jobs[: args.limit]
    print("tag=%s targets=%d plans=%s prefix=%s dry_run=%s"
          % (args.tag, len(jobs), {t: len(plans[t]) for t in tags}, dst_prefix, args.dry_run))

    index, tgt, inv, seen = [], [], {}, set()
    ok = skipped = 0
    skipped_ids = []

    for i, (tag, tid) in enumerate(jobs, 1):
        if tid in seen:
            continue
        seen.add(tid)
        raw = fetch(r2, args.src_bucket, src_root + tag + "/", tid)
        if not raw:
            skipped += 1
            skipped_ids.append(tid)
            print("[%d/%d] SKIP %s/%s（源对象与分片都不存在）" % (i, len(jobs), tag, tid))
            continue
        try:
            j = json.loads(gzip.decompress(raw))
        except Exception as e:
            skipped += 1
            skipped_ids.append(tid)
            print("[%d/%d] SKIP %s/%s（解压/解析失败: %s）" % (i, len(jobs), tag, tid, e))
            continue

        ti = len(tgt)
        tgt.append([tid, j["summary"]["title"]])
        for k, c in enumerate(j["clauses"]):               # 反向：注疏的哪一处 → 这部经的哪一句
            seen_d = set()
            for it in sorted(c["items"], key=lambda it: it["r"] != "確"):
                did = j["docs"][it["d"]]["id"]
                if did in seen_d:
                    continue
                seen_d.add(did)
                inv.setdefault(did, []).append([it["c"], it["n"], ti, k, it["k"], 1 if it["r"] == "確" else 0])

        w, txt, chunks = build(j)
        before = stats["bytes"]
        put_object(r2, args.dst_bucket, dst_prefix + tid + ".json", txt, args.dry_run, stats)
        for pk, ch in enumerate(chunks or []):
            put_object(r2, args.dst_bucket, dst_prefix + "%s.p%03d.json" % (tid, pk),
                       dumps({"paras": ch}), args.dry_run, stats)
        nbytes = stats["bytes"] - before

        ps_ = w["paras"] if not chunks else [p_ for ch in chunks for p_ in ch]
        ncl = sum(len(p["clauses"]) for p in ps_)
        nh = sum(1 for p in ps_ for c in p["clauses"] if c["n"])
        index.append({"id": tid, "title": w["title"], "chars": w["chars"],
                      "docs": len(w["docs"]), "clauses": ncl, "hit": nh})
        ok += 1
        print("[%d/%d] %s %s docs=%d paras=%d clauses=%d hit=%d parts=%d bytes=%d"
              % (i, len(jobs), tid, w["title"], len(w["docs"]), len(ps_), ncl, nh,
                 w.get("parts", 0), nbytes))
        del j

    # --- 反链 c/<docid>.json（等全部 target 处理完再写，ti 才有终值）---
    inv_obj = 0
    for did, es in inv.items():
        es.sort()
        used = sorted({e[2] for e in es})
        mp = {t: i for i, t in enumerate(used)}
        for e in es:
            e[2] = mp[e[2]]
        put_object(r2, args.dst_bucket, dst_prefix + "c/" + did + ".json",
                   dumps({"t": [tgt[t] for t in used], "e": es}), args.dry_run, stats)
        inv_obj += 1

    idx_body = dumps(sorted(index, key=lambda x: -x["docs"]))
    put_object(r2, args.dst_bucket, dst_prefix + "index.json", idx_body, args.dry_run, stats)

    print("----")
    print("tag=%s 目标=%d 成功=%d 跳过=%d 写入对象=%d(反链 %d + index 1) 总字节=%d 耗时=%.1fs"
          % (args.tag, len(jobs), ok, skipped, stats["objects"], inv_obj, stats["bytes"],
             time.time() - t0))
    if skipped_ids:
        print("跳过清单: %s" % ",".join(skipped_ids))
    print("index=%d 条 %d 字节  prefix=%s" % (len(index), len(idx_body), dst_prefix))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="V4 注疏 R2→R2 导出（默认只写探针前缀）")
    ap.add_argument("--tag", default="43175083562",
                    help="源 tag；可用逗号写多个（如 42879981497,42892321782,43175083562），"
                         "一次跑全才能保住 index/反链的跨 tag 聚合")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个 target（0=全量）")
    ap.add_argument("--ids", default="", help="只处理指定 id，用 + 分隔")
    ap.add_argument("--prefix", default="data/v4-preview-probe/", help="目标前缀（默认探针目录）")
    ap.add_argument("--dry-run", action="store_true", help="不 PUT，只打印")
    ap.add_argument("--token-file", default=DEFAULT_TOKEN_FILE, help="token 文件路径")
    ap.add_argument("--acc-id", default="", help="账户 id（默认 CF_ACC_ID 或 API 查询）")
    ap.add_argument("--src-prefix", default=DEFAULT_SRC_PREFIX)
    ap.add_argument("--src-bucket", default=DEFAULT_SRC_BUCKET)
    ap.add_argument("--dst-bucket", default=DEFAULT_DST_BUCKET)
    ap.add_argument("--retries", type=int, default=3, help="失败重试次数")
    ap.add_argument("--max-backoff", type=float, default=8.0, help="退避上限秒")
    ap.add_argument("--allow-prod", action="store_true", help="允许写 data/v4-preview/ 正式前缀")
    args = ap.parse_args(argv)

    dst = normalized_prefix(args.prefix)
    if dst.startswith(PROD_PREFIX) and not args.allow_prod:
        print("拒绝写入正式前缀 %s —— 探针请用 data/v4-preview-probe/；确要写正式请加 --allow-prod"
              % dst, file=sys.stderr)
        return 2

    token = read_token(args)
    acc = args.acc_id or os.environ.get("CF_ACC_ID") or account_id_from_api(token)
    r2 = R2(token, acc, retries=args.retries, max_backoff=args.max_backoff)
    return run(r2, args)


if __name__ == "__main__":
    sys.setrecursionlimit(50000)
    try:
        threading.stack_size(512 * 1024 * 1024)
    except Exception:
        pass
    rc = {}

    def _runner():
        try:
            rc["code"] = main()
        except BaseException as e:              # 线程里的异常不会自动冒泡，显式带出
            rc["err"] = e

    t = threading.Thread(target=_runner)
    t.start()
    t.join()
    if "err" in rc:
        raise rc["err"]
    sys.exit(rc.get("code", 0))
