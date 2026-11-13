# Indra Engine（因陀罗网引擎）

A deterministic, model-free way to find **which sentence of a sutra a commentary is explaining**, by treating the canon like a genome: closed, fixed, ordered text in which relationships show up as repeated passages.

确定性、零模型地找出"某段注疏在讲经文的哪一句"。把大藏经当作基因组：封闭、不变、有序，关系靠"同一段文字在别处重现"来揭示。

Built by **Lunar Society Labs** (research department of Lunar Society Technologies) for the 以经注经 reading site [zelizuli.com](https://zelizuli.com). Licensed **AGPL-3.0**.

## What it does

1. Builds a suffix array + LCP over the whole text (no word segmentation, no quote marks needed).
2. **Matching statistics**: one scan gives, for every position of a commentary, the longest passage it shares with a target sutra.
3. **Attribution**: a passage that has a *longer* match in another sutra is attributed to that other sutra, not this one.
4. **Lemma walk (v3)**: commentaries walk through the sutra in order. Long passages that agree in order with a neighbour become confirmed anchors (co-linear chaining, as in genome alignment). A short fragment must fall inside the sutra interval *between* its two flanking anchors, and is accepted only if its **expected chance count** is small: `E = f(s) × B`, where `f(s)` is the string's frequency in a large background of Buddhist prose and `B` is the interval length. Tight intervals let common phrases through; loose ones demand rare strings. Commentators' citation markers (X者, 從X下, 至X …) relax the threshold.
5. **Self-measured error rate, no human labels**: the same rules are run on texts that cannot cite the target (Agama collections vs. Mahayana sutras); their hit density is the noise floor. Recall is measured against passages the commentators themselves put in quotation marks.

Only verbatim text is ever reported. Nothing here is generated or guessed by a model.

## Results so far (a 5.25M-character sample, free CI runner, about 1 minute)

Precision is estimated three independent ways (two Agama collections that cannot cite Mahayana sutras, and commentaries on a different sutra); recall is against passages the commentators themselves quoted.

| Target | Version | Hits | Est. precision (中阿含 / 增一 / cross) | Recall |
|---|---|---|---|---|
| Heart Sutra (63 commentaries) | v1 | 3,455 | 1.000 | 0.911 |
| | **v3** | 3,796 | **0.993 / 0.989 / 0.990** | **0.955** |
| Lotus Sutra (4 commentaries) | v1 | 5,701 | 0.956 (combined) | 0.652 |
| | v2 | 7,620 | 0.903 / 0.782 / 0.943 | 0.817 |
| | **v3** | 7,351 | **0.976 / 0.963 / 0.972** | **0.866** |

v3 = `lemma_walk(alpha=0.01, lemma=True, lemma_ext=True, lemma_k=5)`. These are estimates from a sample, not a full-canon evaluation. Known weaknesses: string frequencies come from a ~3.5M-character background, so strings that straddle word boundaries look rarer than they are; variant characters are not yet unified; commentaries on commentaries are not yet linked. See `indra/lemma_walk.py` and `indra/suffix_experiment.py`.


### Held-out test (parameters frozen, sutras never used for tuning)

`python3 -m indra.heldout` — same v3 parameters, 7.3M-character corpus, 77 s on a free runner.

| Sutra | Commentaries (chars) | Hits | Est. precision (中阿含 / 增一 / cross) | Recall |
|---|---|---|---|---|
| 阿彌陀經 T12n0366 | 27 (0.73M) | 6,399 | 0.978 / 0.972 / 0.987 | 0.970 |
| 維摩經 T14n0475 | 11 (1.49M) | 20,792 | 0.941 / 0.943 / 0.952 | 0.969 |
| 金剛經 T08n0235 | 36 (1.50M) | 18,550 | 0.937 / 0.918 / 0.979 | 0.965 |

Most residual Agama-null hits are narrative formulae (受教而聽佛言, 歎曰善哉善哉) that Agama and Mahayana sutras share; commentaries rarely produce them, which is why the cross-commentary control reads higher.

### Whole-canon index (free runner, 5 minutes end to end)

`python3 -m indra.heldout --full` builds a suffix array over **every document the corpus server holds: 4,947 documents, 228,074,731 characters** (after stripping punctuation), with libsais through ctypes (`indra/native_sa.py`, `native/sa_tools.c`; verified against brute force). Fetch 103 s, SA + LCP 16.5 s, about 5 GB RAM. Random 4–8 character lookups: **p50 0.006 ms, p99 0.008 ms**, 172 occurrences on average.

Redundancy of the canon: 35% of all positions sit inside a passage of ≥8 characters that occurs elsewhere, 27% ≥16, 21% ≥32, 17% ≥64.

Using the whole canon as the language background for the chance score (excluding the target sutra, its commentaries and the Agama controls), with α recalibrated on the two tuning sutras only (Heart, Lotus):

| Sutra | v3, 5M-char background (α=0.01) | v3, whole-canon sutra background (α=0.006) |
|---|---|---|
| 阿彌陀經 (held-out) | 0.978 / 0.972 · recall 0.970 | **0.996 / 0.970** · recall 0.970 |
| 維摩經 (held-out) | 0.940 / 0.942 · 0.970 | **0.971 / 0.957** · 0.964 |
| 金剛經 (held-out) | 0.936 / 0.917 · 0.964 | **0.977 / 0.942** · 0.961 |
| 心經 (tuning) | 0.996 / 0.992 · 0.952 | 0.997 / 0.991 · 0.907 |
| 法華經 (tuning) | 0.959 / 0.939 · 0.871 | 0.921 / 0.898 · 0.887 |

(precision vs. 中阿含 / 增一, then recall.) A better background moves the held-out sutras up on precision at a small recall cost; on the two tuning sutras (short, 4-character-heavy) it mostly trades one for the other. We do not claim more than that.

### Things tried that did not help (kept for the record)

- **Sub-commentary propagation** (`lemma_walk.propagate`): inherit confirmed links through verbatim runs of ≥16 characters shared between two commentaries. Recall +0.0 to +0.3 points; later commentaries cite earlier ones in short lemmas, not long verbatim runs.
- **Variant characters** (`INDRA_VARIANTS=Unihan_Variants.txt`): Unicode Unihan kZVariant + kSemanticVariant only (1,943 characters; simplified/traditional pairs deliberately excluded). Quoted passages verifiable in the sutra +0.8% to +6%, precision unchanged. The remaining one-character differences are real wording changes (即/則, 土/國), which should not be merged.

## Run it

```
pip install numpy requests
python3 -m indra.short_quote --selftest
python3 -m indra.suffix_experiment --selftest
INDRA_SITE=https://your-corpus-server python3 -m indra.suffix_experiment --out ./out
```

The matching code is corpus-agnostic. The fetcher in `suffix_experiment.py` expects a server that exposes `/data/manifest.json` and `/data/search/<doc_id>.json` in the layout used by zelizuli.com; adapt `fetch_docs()` for other corpora.

## Data

This repository contains **no canon text**. Texts come from CBETA and other sources under their own terms; check those terms before redistributing any text or derived data.

## License

GNU Affero General Public License v3.0. If you run a modified version as a network service, you must offer its source to the users of that service.

## Source publication and commercial-use boundary

This public V3 repository remains licensed under **AGPL-3.0**, which **permits commercial use** subject to its conditions. It does *not* grant any blanket rights to third-party Buddhist canon texts or private V4 source. For the disclosure boundary and the lightweight public-CI check, read [PUBLICATION-POLICY.md](PUBLICATION-POLICY.md) and run `python3 tools/check_public_boundary.py` before proposing code for publication.
