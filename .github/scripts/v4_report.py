import sys, json, gzip, urllib.error, collections
sys.path.insert(0, ".github/scripts")
import oidc_store as S
tag = sys.argv[1]
plan = json.loads(S.get(f"engine-v4-out/{tag}/plan.json"))["families"]
rows, missing, big = [], [], []
for f in plan:
    t = f["target"]; base = f"engine-v4-out/{tag}/{t}.json.gz"
    try: b = S.get(base)
    except urllib.error.HTTPError:
        b = b""; k = 0
        try:
            while True: b += S.get(base + ".part%03d" % k); k += 1
        except urllib.error.HTTPError: pass
        if not b: missing.append(t); continue
    big.append(len(b))
    s = json.loads(gzip.decompress(b))["summary"]; rows.append(s)
print("families ok", len(rows), "missing", len(missing), missing[:10])
print("gz bytes total %.1f MB, max %.2f MB" % (sum(big) / 1e6, max(big) / 1e6))
m = lambda k: sum(r[k] for r in rows if r.get(k) is not None) / max(1, sum(1 for r in rows if r.get(k) is not None))
print("mean frac_sure %.3f frac_cand %.3f" % (m("frac_sure"), m("frac_cand")))
cm = [r["control_max"] for r in rows if r.get("control_max") is not None]
print("control leakage mean %.4f, max %.4f, n>0.02: %d" % (sum(r["control_mean"] for r in rows if r.get("control_mean") is not None) / max(1, len(cm)), max(cm), sum(1 for x in cm if x > 0.02)))
ex = collections.Counter()
for r in rows: ex.update(r["exam"])
for b in ("長", "短"):
    n = ex["n" + b]
    if n: print(f"quote recall {b}: any {ex['any'+b]/n:.3f} sure {ex['sure'+b]/n:.3f} (n={n})")
print("kepan nodes/attached", sum(r["kepan_nodes"] for r in rows), sum(r["kepan_attached"] for r in rows))
print("clauses total", sum(r["clauses"] for r in rows), "sure-clauses", sum(r["clauses_sure"] for r in rows))
print("seconds total", sum(r["seconds"] for r in rows), "max", max(r["seconds"] for r in rows))
print("no-decoy families", sum(1 for r in rows if not r["decoys"]))
