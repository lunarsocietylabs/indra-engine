import sys, json, gzip
sys.path.insert(0, ".github/scripts")
import oidc_store as S
sys.setrecursionlimit(50000)
for tid in ("T08n0251", "T12n0366"):
    j = json.loads(gzip.decompress(S.get(f"engine-v4-out/r2/{tid}.json.gz")))
    print("diagnostic counts", "clauses", len(j.get("clauses", [])), "docs", len(j.get("docs", [])))
