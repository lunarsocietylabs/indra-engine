"""全藏批量（分片）：按 plan.json 把「经」均分到各分片，逐部跑 V4，结果逐经压缩落盘。
用法：python3 -m indra.v4_batch --plan plan.json --shard 0 --nshards 8 --out /tmp/outgz [--only ID,ID] [--workers 4]
"""
import argparse, contextlib, gzip, hashlib, json, os, re, sys, time, concurrent.futures
from . import heldout, v4_run as R, v4_locate as V
from .norm import norm

SITE = os.environ.get("INDRA_SITE", "")
NCTL = 12


def get_json(url):
    import requests
    for a in range(6):
        try:
            r = requests.get(url, timeout=300)
            if r.status_code == 200 and not r.headers.get("content-type", "").startswith("text/html"):
                return r.json()
            if r.status_code in (404, 403):
                return None
        except Exception:
            pass
        time.sleep(2 * (a + 1))
    return None


def get_doc(did, fam, meta):
    j = get_json(f"{SITE}/data/search/{did}.json")
    if not j:
        return None
    paras = [(p.get("lb0") or "", p.get("t") or "") for p in j.get("paragraphs", []) if p.get("t")]
    m = meta.get(did, {})
    return {"id": did, "fam": fam, "title": re.sub(r"^.*No\. *[0-9A-Za-z]+ *", "", m.get("title") or ""),
            "author": m.get("author") or "", "paras": paras}


def get_docs(ids, fam, meta, workers=12):
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        return [d for d in ex.map(lambda i: get_doc(i, fam, meta), ids) if d]


def assign(fams, n):
    bins = [[0, []] for _ in range(n)]
    for f in sorted(fams, key=lambda f: (-f["est_sec"], f["target"])):
        b = min(bins, key=lambda x: x[0]); b[0] += f["est_sec"]; b[1].append(f["target"])
    return [set(b[1]) for b in bins]


def main(a):
    plan = json.load(open(a.plan))["families"]
    mine = assign(plan, a.nshards)[a.shard]
    only = set(filter(None, a.only.split(",")))
    todo = [f for f in plan if f["target"] in mine and (not only or f["target"] in only)]
    todo.sort(key=lambda f: f["est_sec"])
    man = get_json(f"{SITE}/data/manifest.json")
    meta = {d["doc_id"]: d for d in (man.get("docs", man) if isinstance(man, dict) else man) if d.get("doc_id")}
    os.makedirs(a.out, exist_ok=True)
    log = open(os.path.join(a.out, "_log.txt"), "a", encoding="utf8")
    ctl_ids = list(dict.fromkeys(heldout.OTHER_SUTRAS + heldout.BG + heldout.NULL))
    ctl = get_docs(ctl_ids, "bg", meta)
    bg = V.Background([norm("".join(t for _, t in d["paras"])) for d in ctl])
    pool = sorted({c["id"]: c for f in plan for c in f["comms"] if 20000 <= c["chars"] <= 300000}.values(),
                  key=lambda c: hashlib.sha1(c["id"].encode()).hexdigest())
    cache, errors, done = {}, [], 0
    t0 = time.time()
    for f in todo:
        tid = f["target"]
        t1 = time.time()
        try:
            target = get_doc(tid, "sutra:" + tid, meta)
            own_ids = sorted(c["id"] for c in f["comms"] if c.get("primary"))
            if not target or not own_ids:
                raise RuntimeError("no target or no primary commentary")
            bad = {c["id"] for c in f["comms"]}
            ctl_com = [c["id"] for c in pool if c["id"] not in bad][:NCTL]
            for i in ctl_com:
                if i not in cache:
                    cache[i] = get_doc(i, "com:_ctl", meta)
            own = get_docs(own_ids, "com:" + tid, meta)
            docs = [target] + [d for d in ctl if d["id"] != tid] + own + [dict(cache[i]) for i in ctl_com if cache.get(i)]
            byid = {d["id"]: d for d in docs}
            with contextlib.redirect_stdout(log):
                R.run_family(tid, tid, target["title"], docs, byid, bg, a.out, a.workers)
            p = os.path.join(a.out, tid + ".json")
            raw = open(p, "rb").read()
            with gzip.open(p + ".gz", "wb", 9) as g:
                g.write(raw)
            os.remove(p)
            done += 1
            print(f"{tid} ok {time.time() - t1:.0f}s", flush=True)
        except Exception as e:                       # 一部失败不拖累其它
            errors.append({"id": tid, "err": f"{type(e).__name__}: {str(e)[:200]}"})
            print(f"{tid} FAILED {type(e).__name__}", flush=True)
        del_docs = None
    json.dump({"shard": a.shard, "done": done, "errors": errors, "seconds": round(time.time() - t0)},
              open(os.path.join(a.out, f"_shard{a.shard}.json"), "w"))
    print(f"shard {a.shard}: done {done}/{len(todo)}, errors {len(errors)}, {time.time() - t0:.0f}s")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--nshards", type=int, default=1)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--workers", type=int, default=4)
    main(ap.parse_args())
