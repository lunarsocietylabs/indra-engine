# Indra Engine（因陀罗网引擎）

A deterministic, model-free way to find **which sentence of a sutra a commentary is explaining**, by treating the canon like a genome: closed, fixed, ordered text in which relationships show up as repeated passages.

确定性、零模型地找出"某段注疏在讲经文的哪一句"。把大藏经当作基因组：封闭、不变、有序，关系靠"同一段文字在别处重现"来揭示。

Built by **Lunar Society Labs** (research department of Lunar Society Technologies) for the 以经注经 reading site [zelizuli.com](https://zelizuli.com). Licensed **AGPL-3.0**.

## What it does

1. Builds a suffix array + LCP over the whole text (no word segmentation, no quote marks needed).
2. **Matching statistics**: one scan gives, for every position of a commentary, the longest passage it shares with a target sutra.
3. **Attribution**: a passage that has a *longer* match in another sutra is attributed to that other sutra, not this one.
4. **Order chain**: commentaries follow the sutra in order, so a passage must lie near its neighbouring reliable anchors in the sutra.
5. **Self-measured error rate, no human labels**: the same rules are run on texts that cannot cite the target (Agama collections vs. Mahayana sutras); their hit density is the noise floor. Recall is measured against passages the commentators themselves put in quotation marks.

Only verbatim text is ever reported. Nothing here is generated or guessed by a model.

## Results so far (a 5.25M-character sample, one CPU, 68 s)

| Target | Hits | Estimated precision | Recall vs. quoted passages | Found beyond quote-based matching |
|---|---|---|---|---|
| Heart Sutra (63 commentaries) | 3,186 | 1.000 | 0.877 | 2,872 |
| Lotus Sutra (4 commentaries) | 6,109 | 0.943 | 0.676 | 3,926 |

These are estimates from a sample, not a full-canon evaluation. Known weaknesses: Lotus recall is low because its commentaries quote in 4–8 character fragments; variant characters are not yet unified; commentaries on commentaries are not yet linked to the commentary they explain. See `indra/suffix_experiment.py` for the exact rules.

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
