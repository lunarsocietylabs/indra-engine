import sys, json, gzip, re, random, collections
sys.path.insert(0, ".github/scripts")
import oidc_store as S
tag = sys.argv[1]
plan = json.loads(S.get(f"engine-v4-out/{tag}/plan.json"))["families"]
def walk(ns):
    for n in ns:
        yield n
        for it in n["items"]: yield from walk(it["children"])
cnt = collections.Counter(); cands = []
for f in plan[:120]:
    try: b = S.get(f"engine-v4-out/{tag}/{f['target']}.json.gz")
    except Exception: continue
    for d in json.loads(gzip.decompress(b))["docs"]:
        for n in walk(d["kepan"]):
            cnt["所有"] += 1; cnt[n["type"]] += 1
            labs = [it["label"] for it in n["items"]]
            sec = sum(1 for l in labs if re.search(r"明|序分|正宗|流通|釋名|辨體|顯體|宗趣|分別|彰", l))
            if n["type"] == "數" and sec >= max(2, len(labs) // 2):
                cnt["數但像科判"] += 1
                if len(cands) < 4000: cands.append((d["id"], n["n"], labs[:4]))
            if n["type"] == "數": cnt["數·有头已解析"] += any(it["head"] for it in n["items"])
print(dict(cnt))
random.seed(1)
for c in random.sample(cands, min(14, len(cands))): print(c[0], c[1], [l[:16] for l in c[2]])
