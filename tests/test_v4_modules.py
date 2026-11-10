# SPDX-License-Identifier: AGPL-3.0-only
"""v4 其余模块的自测：各模块自带的例子 + 短牒文补认 + 归属规则的几个边界。用法：python3 -m tests.test_v4_modules"""
import random, runpy

from indra import v4_locate as V


def test_refine():
    """常用二字词（信息量不够当主锚点）作牒文："言某某者"。只有带句式的才该被补认；同样的词在释文里随处出现的不该认。"""
    rnd = random.Random(3)
    alpha = [chr(0x4E00 + k) for k in range(3000)]
    common = [rnd.choice(alpha) + rnd.choice(alpha) for _ in range(400)]

    def text(n):
        out, k = [], 0
        while k < n:
            w = rnd.choice(common) if rnd.random() < 0.3 else rnd.choice(alpha)
            out.append(w); k += len(w)
        return "".join(out)

    def clause():
        return text(4)[:4] + rnd.choice(common) + text(4)[:4]
    cls = [clause() for _ in range(40)]
    S, bounds = "", []
    for c in cls:
        bounds.append((len(S), len(S) + len(c))); S += c
    C, want = text(200), []
    for k, c in enumerate(cls):
        C += c + text(60)
        if k % 2 == 0:
            want.append((len(C) + 1, k)); C += "言" + c[4:6] + "者" + text(80)
        C += text(40)
    decoys = ["".join(clause() for _ in range(40))[:len(S)] for _ in range(8)]
    bg = V.Background([text(300000)])
    assert bg.info(cls[0][4:6]) < V.seed_floor(len(S)), "测试前提：常用词不够当主锚点"
    r = V.locate(S, C, bounds, decoys, bg)
    weak = {s["c0"]: s for s in r["segs"] if s.get("weak")}
    hit = sum(1 for c0, k in want if c0 in weak and bounds[k][0] <= weak[c0]["s0"] < bounds[k][1])
    print(f"refine: 短牒文 {hit}/{len(want)} 认出，补认共 {len(weak)} 处")
    assert hit >= len(want) * 0.8, (hit, len(want))
    assert len(weak) <= len(want) * 1.3, "补认过多"


def test_isolated_quote():
    """别的文献里孤零零引到本经一句：只出引文，不带释文。"""
    rnd = random.Random(5)
    alpha = [chr(0x4E00 + k) for k in range(3000)]
    def text(n):
        return "".join(rnd.choice(alpha) for _ in range(n))
    cls = [text(12) for _ in range(40)]
    S, bounds = "", []
    for c in cls:
        bounds.append((len(S), len(S) + len(c))); S += c
    own = text(100) + "".join(c + text(150) for c in cls)
    other = text(3000) + cls[7] + text(3000) + cls[30] + text(3000)
    decoys = [text(len(S)) for _ in range(8)]
    bg = V.Background([text(200000)])
    r = V.locate(S, own, bounds, decoys, bg)
    u = V.locate(S, other, bounds, decoys, bg, th=r["th"])
    kinds = sorted((s["kind"], s["tier"]) for s in u["segs"])
    print("isolated:", kinds, "gloss", u["stats"]["gloss_sure"])
    long = [s for s in u["segs"] if s["kind"] in ("引", "參") and s["c1"] - s["c0"] == 12]
    assert u["stats"]["gloss_sure"] == 0 and len(long) == 2 and not [s for s in u["segs"] if s["kind"] == "釋"], kinds
    n, wrong = V.loo_exam(r["chains"], r["th"], bounds)
    assert n >= 30 and wrong == 0, (n, wrong)


if __name__ == "__main__":
    for m in ("v4_grammar", "v4_kepan", "v4_layers", "v4_cite"):
        runpy.run_module("indra." + m, run_name="__main__")
    test_refine()
    test_isolated_quote()
    print("OK")
