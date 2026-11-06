# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lunar Society Technologies
"""Text normalisation used before any matching: NFKC, then strip whitespace and punctuation."""
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


def norm(s: str) -> str:
    return PUNCT_RE.sub("", unicodedata.normalize("NFKC", s))
