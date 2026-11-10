# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lunar Society Technologies
"""
v4_relation.py — 经疏关系从数据里来，替代"按书名猜"。

对一对文献（A 当经、B 当疏）跑 v4_locate，只看**过了確层门槛**的锚点，读出形状（v4_locate.stats）：
  clause_hit  A 的句子里，被 B 逐字牒到的比例；      order   確层引文里落在同一条顺序链上的比例；
  scale       |B| / |A|；                            frac    B 的文字里確层归属到 A 的比例。
判别（阈值是暂定的；v4_run 每次都拿"本族注疏 对 别族注疏"实测它的精确率与召回率，随结果公布，数字说话）：
  注疏      B 按次序把 A 的相当一部分句子牒了出来，且 B 自己的文字多在讲 A；
  異譯重出  与 A 长度相近、通篇顺序相同；
  節引      牒到的句子不多但有序（节要、别行、悬谈）；
  偶引      只零星引到；   無關  確层一处也没有。
"""


def classify(st):
    if not st.get("anchors_sure"):
        return "無關"
    hit = st["clauses_hit"] / max(1, st["clauses"])
    if hit >= 0.6 and st["order"] >= 0.7 and 0.5 <= st["scale"] <= 2.0 and st["cover_S"] >= 0.5:
        return "異譯重出"
    if hit >= 0.3 and st["order"] >= 0.5 and st["frac_sure"] >= 0.2:
        return "注疏"
    if hit >= 0.1 and st["order"] >= 0.5:
        return "節引"
    return "偶引"


def confusion(own, other):
    """own: 本族注疏的类别列表；other: 别族注疏（对照）的类别列表。返回"判为注疏"的精确率、召回率与各类计数。"""
    tp = sum(1 for c in own if c == "注疏")
    fp = sum(1 for c in other if c == "注疏")
    return {"precision": tp / (tp + fp) if tp + fp else None, "recall": tp / len(own) if own else None,
            "own": {c: own.count(c) for c in sorted(set(own))}, "other": {c: other.count(c) for c in sorted(set(other))}}
