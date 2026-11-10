# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lunar Society Technologies
"""
v4_relation.py — 经疏关系从数据里来（共线性），替代"按书名猜"。
对任意一对文献 (A, B) 做 locate，读出形状：
  cover_A  B 的锚点覆盖了 A 的多少（A 被"走了一遍"的程度）；  cover_B  A 的锚点覆盖了 B 的多少；
  order    主链上的锚点数 / 全部锚点数（越接近 1，顺序越一致）；  scale    |B| / |A|。
典型形状：随文注疏——A 被按序走一遍以上（cover_A 高、order 高）且 B 远长于 A；同本异译——长度相近、有序、共有中等；
抄集／类书——大块照搬但无序（order 低）；偶引——cover_A 低。
下面的规则是**暂定的**：run 时用已知的真实例子（有大量「」明引的注疏）与无关文献一起测它的精确率，数字随结果一并公布，再冻结。
"""
from . import v4_locate as V


def shape(A, B, min_score=8.0):
    chs = V.chains(A, B, min_score=min_score)
    if not chs:
        return {"cover_A": 0.0, "cover_B": 0.0, "order": 0.0, "scale": len(B) / max(1, len(A)), "chains": 0, "top": 0.0}
    covA, covB = bytearray(len(A)), bytearray(len(B))
    total = sum(len(c["seeds"]) for c in chs)
    for c in chs:
        for i, j, L, _ in c["seeds"]:
            covA[j:j + L] = b"\x01" * L
            covB[i:i + L] = b"\x01" * L
    return {"cover_A": sum(covA) / len(A), "cover_B": sum(covB) / len(B), "order": len(chs[0]["seeds"]) / total,
            "scale": len(B) / len(A), "chains": len(chs), "top": chs[0]["score"]}


def classify(sh):
    if sh["chains"] == 0 or sh["cover_A"] < 0.05:
        return "無關"
    if sh["cover_A"] >= 0.5 and sh["order"] >= 0.6:
        if sh["scale"] >= 1.5:
            return "注疏"
        if 0.5 <= sh["scale"] < 1.5 and sh["cover_B"] >= 0.5:
            return "異譯或重出"
    if sh["order"] < 0.4 and sh["cover_A"] >= 0.2:
        return "抄集類書"
    return "偶引"
