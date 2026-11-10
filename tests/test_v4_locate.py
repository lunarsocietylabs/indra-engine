# SPDX-License-Identifier: AGPL-3.0-only
"""合成数据自测：经 = 60 个十字小句；疏 = 序言 + 逐句（牒文 + 释文），其中有一次"走第二遍"。
通过标准：牒文定位正确率 >= 95%；释文归属到正确小句 >= 90%；假经上最优链得分明显低于真经；FDR 曲线可用。
运行：python3 -m tests.test_v4_locate"""
import random, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from indra import v4_locate as V

ALPHA = [chr(0x4E00 + i) for i in range(500)]
W = [1.0 / (r + 1) for r in range(len(ALPHA))]


def rnd(rng, n):
    return "".join(rng.choices(ALPHA, W, k=n))


def build(seed=1, clauses=60, skip=0.1):
    rng = random.Random(seed)
    cl = [rnd(rng, 10) for _ in range(clauses)]
    S = "".join(cl)
    bounds = [(k * 10, k * 10 + 10) for k in range(clauses)]
    C, truth, quoted = rnd(rng, 150), [], {}
    for rep in range(2):                              # 第二遍只走前 20 句
        for k in range(clauses if rep == 0 else 20):
            if rng.random() < skip:
                continue
            q = cl[k] if rng.random() < .6 else cl[k][:6]
            start = len(C); C += q; quoted[start] = (k, len(q))
            e = rnd(rng, rng.randint(25, 70)); truth.append((len(C), len(C) + len(e), k)); C += e
    return S, C, bounds, truth, quoted


def run():
    S, C, bounds, truth, quoted = build()
    R = V.locate(S, C, bounds)
    segs = R["segs"]
    # 牒文：每个真实牒文起点处，应有一个「引」段，且其经文区间落在正确小句
    ok = tot = 0
    for st, (k, L) in quoted.items():
        tot += 1
        for s in segs:
            if s["kind"] == "引" and s["c0"] <= st < s["c1"]:
                ok += (bounds[k][0] <= s["s0"] < bounds[k][1] or s["s0"] <= bounds[k][0] < s["s1"]); break
    rec = ok / tot
    # 释文：真实释文区间里，被归属到正确小句（该小句 = 牒文所在小句）的字数比例
    good = all_ = 0
    for c0, c1, k in truth:
        for x in range(c0, c1):
            all_ += 1
            for s in segs:
                if s["kind"] == "釋" and s["c0"] <= x < s["c1"]:
                    good += (s["s0"] <= bounds[k][0] and bounds[k][0] < s["s1"]) or (bounds[k][0] <= s["s0"] < bounds[k][1]); break
    sh = good / all_
    # 假经
    rng = random.Random(7)
    dec = []
    for _ in range(8):
        D = rnd(rng, len(S)); dc = V.chains(D, C); dec.append([c["score"] for c in dc])
    true_scores = [c["score"] for c in R["chains"]]
    thr, curve = V.fdr_thresholds(true_scores, dec)
    print("chains", len(R["chains"]), "top", round(R["chains"][0]["score"], 1), "decoy top", [round(max(d), 1) if d else 0 for d in dec][:8])
    print("anchor recall %.3f  gap-attribution %.3f  located %.3f" % (rec, sh, R["frac"]))
    print("fdr thresholds", thr)
    assert rec >= .95, rec
    assert sh >= .90, sh
    assert (max(max(d) if d else 0 for d in dec)) < R["chains"][0]["score"] / 2
    assert thr[0.1] is not None
    # 确定性
    R2 = V.locate(S, C, bounds); assert R2["segs"] == R["segs"]
    print("OK")


if __name__ == "__main__":
    run()
