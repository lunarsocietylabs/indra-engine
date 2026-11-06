# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lunar Society Technologies
"""
short_quote.py — 经—疏登记表驱动的「短引文」有序匹配（纯确定性，无模型、无概率）

背景：align.py 要求 ≥18 字连续相同才建边；而注疏实际引经多为 2–8 字起句
（「如是我聞」「色即是空」…），导致"谁在注这一句"大面积漏判。本模块只对
**登记表里已声明的 (疏 → 经) 配对**工作，把搜索空间从全库缩到一部经，
再用 引号/科判标记 + 经内唯一性 + 次序单调 三道约束，保持可复核。

判级（与前端 EVID 同义，不得混用）：
  ok   : 带引号 且 归一后 ≥8 字 且 在该经内**唯一出现** 且 通过次序约束
  cand : 其余通过约束者（含重复出现但被次序唯一确定、或无引号的「科判」词条）
  丢弃 : 在该经内出现次数 > MAX_OCC（如「如是我聞」这类处处可见的套语）

用法：
  python3 -m indra.short_quote --selftest
  python3 -m indra.short_quote --registry registry.json \
        --corpus-dir DIR --out OUT.json     # DIR/<doc_id>.json = [{"lb":..,"t":..},..]
"""
import argparse, json, os, re, sys

from .norm import norm

MIN_LEN = 4        # 短引文下限（归一后字数）
OK_LEN = 8         # 判 ok 的下限
MAX_OCC = 3        # 经内出现次数上限
QUOTE_RE = re.compile(r"[「『]([^」』]{%d,200})[」』]" % MIN_LEN)
SPLIT_RE = re.compile(r"…+|⋯+|乃至|等")          # 引文内部省略 → 切成多个片段
LEMMA_RE = re.compile(r"^([^，。；：「」]{%d,18}?)(者|下|等者)" % MIN_LEN)


class Sutra:
    """把一部经的段落拼成归一字符流，并记录每个字符所属段落（lb）。"""
    def __init__(self, paras):
        self.lbs, self.owner, buf = [], [], []
        for i, p in enumerate(paras):
            n = norm(p["t"])
            self.lbs.append(p["lb"])
            buf.append(n)
            self.owner.extend([i] * len(n))
        self.text = "".join(buf)

    def find_all(self, frag):
        out, s = [], 0
        while True:
            j = self.text.find(frag, s)
            if j < 0:
                return out
            out.append(j)
            s = j + 1


def extract_fragments(para_text):
    """从疏文段落取候选片段：[(frag_norm, source, quoted)]，保持出现次序。"""
    frags = []
    for m in QUOTE_RE.finditer(para_text):
        for piece in SPLIT_RE.split(m.group(1)):
            n = norm(piece)
            if len(n) >= MIN_LEN:
                frags.append((n, "quote", True))
    m = LEMMA_RE.match(para_text.strip())
    if m:
        n = norm(m.group(1))
        if len(n) >= MIN_LEN:
            frags.append((n, "lemma", False))
    return frags


def match_paragraph(para_text, sutra, max_occ=MAX_OCC):
    """返回 [{to_lb, len, tier, src, n_occ, text}]；次序单调由 DP 保证、结果确定。"""
    frags = extract_fragments(para_text)
    cands = []                                   # 每片段的 [(pos, len)] 候选
    for f, src, quoted in frags:
        occ = sutra.find_all(f)
        if not occ or len(occ) > max_occ:
            cands.append(None)
            continue
        cands.append((f, src, quoted, occ))
    # DP：在疏文次序下选经内位置非降的方案，最大化总匹配字数；并列取位置靠前（确定性）
    best = {}                                    # (i, k) -> (score, prev)
    order = [i for i, c in enumerate(cands) if c]
    for ii, i in enumerate(order):
        f, _, _, occ = cands[i]
        for k, pos in enumerate(occ):
            sc, prev = len(f), None
            for jj in range(ii):
                j = order[jj]
                for kk, pp in enumerate(cands[j][3]):
                    if pp + len(cands[j][0]) <= pos and (j, kk) in best:
                        s2 = best[(j, kk)][0] + len(f)
                        if s2 > sc or (s2 == sc and prev is not None and (j, kk) < prev):
                            sc, prev = s2, (j, kk)
            best[(i, k)] = (sc, prev)
    if not best:
        return []
    end = min(best, key=lambda x: (-best[x][0], x[0], x[1]))
    chosen, cur = [], end
    while cur is not None:
        chosen.append(cur)
        cur = best[cur][1]
    chosen.reverse()
    edges = []
    for i, k in chosen:
        f, src, quoted, occ = cands[i]
        pos = occ[k]
        uniq = len(occ) == 1
        tier = "ok" if (quoted and len(f) >= OK_LEN and uniq) else "cand"
        edges.append({"to_lb": sutra.lbs[sutra.owner[pos]], "len": len(f), "tier": tier,
                      "src": src, "n_occ": len(occ), "text": f})
    return edges


def run(registry, corpus_dir, out):
    reg = json.load(open(registry, encoding="utf-8"))
    res, errs = [], []
    for ent in reg["entries"]:
        try:
            sut = Sutra(json.load(open(os.path.join(corpus_dir, ent["sutra"] + ".json"), encoding="utf-8")))
            com = json.load(open(os.path.join(corpus_dir, ent["commentary"] + ".json"), encoding="utf-8"))
        except FileNotFoundError as e:       # 登记表里的 ID 必须真实存在（fail-closed）
            errs.append(f'{ent["commentary"]}→{ent["sutra"]}: 缺文件 {e.filename}')
            continue
        for p in com:
            for e in match_paragraph(p["t"], sut):
                res.append(dict(e, from_doc=ent["commentary"], from_lb=p["lb"], to_doc=ent["sutra"]))
    json.dump({"edges": res, "errors": errs}, open(out, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"edges={len(res)} errors={len(errs)}")
    return 1 if errs else 0


def selftest():
    # 合成夹具：经文取《心經》通行本；"疏文"为自编的测试句，非真实注疏
    sutra = Sutra([
        {"lb": "0848a01", "t": "觀自在菩薩行深般若波羅蜜多時照見五蘊皆空度一切苦厄"},
        {"lb": "0848a02", "t": "舍利子色不異空空不異色色即是空空即是色受想行識亦復如是"},
        {"lb": "0848a03", "t": "舍利子是諸法空相不生不滅不垢不淨不增不減"},
    ])
    # 1 唯一+引号+≥8字 → ok
    e = match_paragraph("釋曰：「行深般若波羅蜜多時照見五蘊皆空」者，謂照見也。", sutra)
    assert len(e) == 1 and e[0]["tier"] == "ok" and e[0]["to_lb"] == "0848a01", e
    # 2 唯一的 8 字引文 → ok；经内重复出现(≤MAX_OCC)的引文 → 只能是 cand
    e = match_paragraph("「舍利子是諸法空相」", sutra)
    assert e and e[0]["to_lb"] == "0848a03" and e[0]["tier"] == "ok", e
    s_rep = Sutra([{"lb": "x1", "t": "云何應住云何降伏其心"}, {"lb": "x2", "t": "云何應住云何降伏其心"}])
    e = match_paragraph("「云何應住云何降伏其心」", s_rep)
    assert len(e) == 1 and e[0]["tier"] == "cand" and e[0]["n_occ"] == 2, e
    # 3 次序约束：两个片段按疏文次序，必须落在经内单调位置
    e = match_paragraph("先云「不生不滅」，次云「受想行識」。", sutra)
    assert [x["to_lb"] for x in e] in (["0848a03"], ["0848a02"]) and len(e) == 1, e   # 次序冲突只留一个
    e = match_paragraph("先云「受想行識亦復如是」，次云「不生不滅不垢不淨」。", sutra)
    assert [x["to_lb"] for x in e] == ["0848a02", "0848a03"], e
    # 4 过短 / 不在经内 → 无边
    assert match_paragraph("「色即」", sutra) == []
    assert match_paragraph("「本經沒有的句子啊」", sutra) == []
    # 5 过于常见的片段被丢弃
    s2 = Sutra([{"lb": "a", "t": "如是我聞一時佛在"}, {"lb": "b", "t": "如是我聞一時佛在"},
                {"lb": "c", "t": "如是我聞一時佛在"}, {"lb": "d", "t": "如是我聞一時佛在"}])
    assert match_paragraph("「如是我聞一時佛在」", s2) == []
    # 6 科判（lemma）词条 → 只能是 cand
    e = match_paragraph("度一切苦厄者，明離苦也。", sutra)
    assert e and e[0]["tier"] == "cand" and e[0]["src"] == "lemma", e
    # 7 确定性：重复运行结果相同
    t = "「照見五蘊皆空」「受想行識亦復如是」"
    assert match_paragraph(t, sutra) == match_paragraph(t, sutra)
    print("short_quote selftest: OK (7 cases)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--registry")
    ap.add_argument("--corpus-dir")
    ap.add_argument("--out")
    a = ap.parse_args()
    if a.selftest:
        selftest()
    else:
        sys.exit(run(a.registry, a.corpus_dir, a.out))
