# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lunar Society Technologies
"""
异体字归一（只用于匹配；显示仍用原字）。

来源：Unicode Unihan 数据库 Unihan_Variants.txt 中的 kZVariant（同字异形）与 kSemanticVariant（同义异体）。
刻意不用 kSimplifiedVariant / kTraditionalVariant：繁简映射会把"后/後""发/發/髮"这类不同的字并在一起。
一对一字符映射（位置不变）；连通分量超过 MAX_COMP 个字的不并（防止链式误并）。
"""
import collections, re

MAX_COMP = 4
_CP = re.compile(r"U\+([0-9A-F]{4,6})")


def _cjk(c):
    o = ord(c)
    return 0x3400 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF or 0x20000 <= o <= 0x3134F


def load_unihan(path):
    parent = {}
    def find(a):
        parent.setdefault(a, a)
        while parent[a] != a:
            parent[a] = parent[parent[a]]; a = parent[a]
        return a
    for line in open(path, encoding="utf-8"):
        if line.startswith("#") or "\t" not in line:
            continue
        cp, field, val = line.rstrip("\n").split("\t", 2)
        if field not in ("kZVariant", "kSemanticVariant"):
            continue
        a = chr(int(cp[2:], 16))
        for h in _CP.findall(val):
            b = chr(int(h, 16))
            if a != b and _cjk(a) and _cjk(b):
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[ra] = rb
    comp = collections.defaultdict(list)
    for c in list(parent):
        comp[find(c)].append(c)
    table = {}
    for cs in comp.values():
        if 2 <= len(cs) <= MAX_COMP:
            canon = min(cs)
            for c in cs:
                if c != canon:
                    table[ord(c)] = canon
    return table
