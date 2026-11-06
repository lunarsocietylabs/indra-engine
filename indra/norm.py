# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lunar Society Technologies
"""Text normalisation used before any matching: NFKC, then strip whitespace and punctuation."""
import os
import re
import unicodedata

PUNCT_RE = re.compile(
    r"[\s　"                       # whitespace / ideographic space
    r"!-/:-@"      # ASCII punctuation
    r"[-`{-~"
    r"、-〃〈-】"      # CJK punctuation and brackets
    r"〔-〟！-･"      # full-width forms
    r"ༀ-༔"                   # Tibetan punctuation
    r"༺-༽"                   # Tibetan brackets
    r"]"
)


# 可选：异体字归一表（环境变量 INDRA_VARIANTS 指向 Unihan_Variants.txt；不设则不归一）
VARIANTS = {}
if os.environ.get("INDRA_VARIANTS"):
    from .variants import load_unihan
    VARIANTS = load_unihan(os.environ["INDRA_VARIANTS"])


def norm(s: str) -> str:
    s = PUNCT_RE.sub("", unicodedata.normalize("NFKC", s))
    return s.translate(VARIANTS) if VARIANTS else s
