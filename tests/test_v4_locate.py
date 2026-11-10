# SPDX-License-Identifier: AGPL-3.0-only
"""v4_locate 难例自测：经、疏、假经、无关注疏全部出自同一套常用"词"，所以碰巧相同的二三字片段到处都是。
考的是：確层归属准不准、无关注疏会不会被"定位"、以常见词开头的引文丢不丢、长释文断不断、异文认不认。
用法：python3 -m tests.test_v4_locate"""
import random

from indra import v4_locate as V


def world(seed=7):
    rnd = random.Random(seed)
    alphabet = [chr(0x4E00 + k) for k in range(700)]
    vocab = ["".join(rnd.choice(alphabet) for _ in range(rnd.choice((2, 2, 2, 3)))) for _ in range(500)]
    weights = [1.0 / (r + 3) for r in range(len(vocab))]             # 词频近似 Zipf：前几十个词到处都是

    def words(n):
        return rnd.choices(vocab, weights, k=n)

    def text(nchars):
        out, k = [], 0
        while k < nchars:
            w = rnd.choices(vocab, weights)[0]
            out.append(w); k += len(w)
        return "".join(out)

    def sutra(nclauses):
        cls = ["".join(words(rnd.randint(3, 6))) for _ in range(nclauses)]
        formula = "".join(words(5))
        for k in (20, 31, 42, 53):                                    # 同一句套话在经里重复四次（如六方佛）
            cls[k] = formula + "".join(words(2))
        cls[10] = vocab[0] + vocab[1] + "".join(words(3))             # 以最常见的两个词开头的一句
        S, bounds = "", []
        for c in cls:
            bounds.append((len(S), len(S) + len(c))); S += c
        return cls, S, bounds

    def commentary(cls, with_truth=True):
        C, truth = [], []                                             # truth: 每个字在讲第几句（-1 = 不讲经）

        def put(s, k):
            C.append(s); truth.extend([k] * len(s))
        put(text(400), -1)
        for k, c in enumerate(cls):
            if k in (15, 16, 47):                                     # 有几句注家没有牒（跳过）
                continue
            q = c if rnd.random() < 0.6 else c[:max(4, len(c) * 2 // 3)]
            if k == 25:                                               # 引文里有一个异体字
                q = c[:3] + "〇" + c[4:]
            put(q, k)
            n = 6000 if k == 30 else rnd.choice((40, 80, 150, 300, 900))   # 第 30 句讲了六千字
            done = 0
            while done < n:
                seg = text(rnd.randint(30, 120)); put(seg, k); done += len(seg)
                r = rnd.random()
                if r < 0.25 and len(c) >= 4:                          # 讲的过程中又提到这一句里的一个词
                    a = rnd.randrange(0, len(c) - 2); put(c[a:a + rnd.choice((2, 3))], k)
                elif r < 0.30 and k > 5:                              # 引前文作证（参见）
                    put(cls[rnd.randrange(0, k - 3)], k)
        put(text(300), -1)
        return "".join(C), truth

    cls, S, bounds = sutra(64)
    C, truth = commentary(cls)
    other_cls, _, _ = sutra(64)
    U, _ = commentary(other_cls)                                      # 别的经的注疏：同样的词汇、同样的写法
    decoys = [sutra(64)[1][:len(S)] for _ in range(8)]
    bg = V.Background([text(300000)])
    return cls, S, bounds, C, truth, U, decoys, bg


def check():
    cls, S, bounds, C, truth, U, decoys, bg = world()
    r = V.locate(S, C, bounds, decoys, bg)
    st = r["stats"]
    starts = [b[0] for b in bounds]
    import bisect
    def clause(j):
        return bisect.bisect_right(starts, j) - 1
    good = bad = amb_ok = amb_n = 0
    cover = bytearray(len(C))
    for s in r["segs"]:
        k0, k1 = clause(s["s0"]), clause(s["s1"] - 1)
        for x in range(s["c0"], s["c1"]):
            cover[x] = 1
            if s["tier"] != V.SURE or s["kind"] == "參":
                continue
            t = truth[x]
            if s.get("amb"):
                amb_n += 1; amb_ok += k0 <= t <= k1
            elif k0 <= t <= k1:
                good += 1
            else:
                bad += 1
    prec = good / max(1, good + bad)
    explained = sum(1 for t in truth if t >= 0)
    cov = sum(1 for x, t in enumerate(truth) if t >= 0 and cover[x]) / explained
    print(f"true: chains {st['chains']} anchors 確{st['anchors_sure']} 候{st['anchors_cand']} th {r['th']}")
    print(f"      確层归属准确率 {prec:.4f}（{good}/{good + bad}）  覆盖率 {cov:.3f}  確 {st['frac_sure']} 候 {st['frac_cand']}")
    assert prec >= 0.97, prec
    assert cov >= 0.80, cov
    assert st["frac_sure"] >= 0.5, st

    # 以最常见词开头的那一句，引文必须被认出
    hit10 = [s for s in r["segs"] if s["kind"] == "引" and clause(s["s0"]) == 10 and s["c1"] - s["c0"] >= 4]
    assert hit10, "以高频词开头的引文丢了"
    # 讲了六千字的那一句：释文应大部分仍归它
    span30 = [x for x, t in enumerate(truth) if t == 30]
    got30 = sum(1 for x in span30 if cover[x]) / len(span30)
    print(f"      六千字长释文覆盖 {got30:.3f}")
    assert got30 >= 0.7, got30
    # 异文引文并成一处
    assert any(s.get("var") for s in r["segs"]), "异文未合并"

    # 无关注疏：確层几乎不应有任何归属
    u = V.locate(S, U, bounds, decoys, bg, th=r["th"])
    print(f"unrelated: 確 {u['stats']['frac_sure']} 候 {u['stats']['frac_cand']} anchors 確{u['stats']['anchors_sure']}")
    assert u["stats"]["frac_sure"] <= 0.01, u["stats"]

    # 没有假经也没有门槛：不出任何结果
    z = V.locate(S, C, bounds, (), bg)
    assert not z["segs"], "无对照不得出结果"

    # 确定性
    r2 = V.locate(S, C, bounds, decoys, bg)
    assert r2["segs"] == r["segs"] and r2["th"] == r["th"]
    print("OK")


if __name__ == "__main__":
    check()
