import sys, json, gzip
sys.path.insert(0, ".github/scripts")
import oidc_store as S
sys.setrecursionlimit(50000)
for tid in ("T08n0251", "T12n0366"):
    j = json.loads(gzip.decompress(S.get(f"engine-v4-out/r2/{tid}.json.gz")))
    sm = j["summary"]; print(tid, {k: sm[k] for k in ("id", "title", "chars", "clauses") if k in sm}, "ndocs", len(j["docs"]))
    print([c["t"][:12] for c in j["clauses"][:4]], [c["t"][:12] for c in j["clauses"][-2:]])
    print([ (d["id"], d["title"][:14]) for d in j["docs"][:4]])
