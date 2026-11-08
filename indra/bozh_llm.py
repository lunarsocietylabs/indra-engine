# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lunar Society Technologies
"""
bozh_llm.py — "提议—核验"：模型只在候选清单里做选择，不得自造藏文串；采纳与否由语料统计说了算。

输入：bozh.py 产出的 cands.json（每个没配上的汉文词的 top-K 藏文候选 + 例句）。
流程：
  1. 对每个词，让模型从候选里挑出"与该汉文词同义的完整藏文词（可含助词边界修正）"，或回答 none。
     模型版本/推理开关由环境变量决定：DEEPSEEK_MODEL（默认 deepseek-flash），推理关闭，temperature=0，输出 JSON。
  2. 核验（确定性）：被选中的串必须在候选清单里（即语料里真实存在，且与该汉文词共现 >= MIN_C 次）；
     覆盖率 = c / df_t（该词出现的段里有多少段含这个藏文串）必须 >= MIN_COV。不满足则丢弃。
  3. 只输出通过核验的对；每条附统计量与例句行号。密钥只从环境变量读取，不写入任何文件或日志。
"""
import json, os, sys, time

import requests

API = os.environ.get("DEEPSEEK_BASE", "https://api.deepseek.com") + "/chat/completions"
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")
MIN_COV = float(os.environ.get("BOZH_MIN_COV", "0.3"))
MIN_PREC = float(os.environ.get("BOZH_MIN_PREC", "0.5"))  # 精度 = c/df_b：该藏文串出现的段里有多少含这个汉文词
LOW_COV = float(os.environ.get("BOZH_LOW_COV", "0.1"))
LOW_C = int(os.environ.get("BOZH_LOW_C", "50"))
MIN_C = int(os.environ.get("BOZH_MIN_C", "3"))

SYS = ("你是汉藏佛典术语对齐的选择器。给你一个汉文佛学词、若干候选藏文音节串（均来自语料统计）和几个汉藏对照例句。"
       "任务：只能从候选里按编号选出与该汉文词意义对应的藏文串；若没有任何候选对应，回答 0。不得编造，不要解释。"
       '只输出 JSON：{"pick": <候选编号，整数；没有则 0>}')


def ask(key, term, item, timeout=60):
    cl = "\n".join(f"{i+1}. {c.get('show', c['bo'])}  (共现{c['c']}次, Dice={c['dice']})" for i, c in enumerate(item["cands"]))
    ex = "\n".join(f"汉：{e['zh']}\n藏：{e['bo']}" for e in item["examples"])
    body = {"model": MODEL, "temperature": 0, "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "messages": [{"role": "system", "content": SYS},
                         {"role": "user", "content": f"汉文词：{term}\n\n候选：\n{cl}\n\n例句：\n{ex}"}]}
    for attempt in range(5):
        r = requests.post(API, headers={"Authorization": "Bearer " + key}, json=body, timeout=timeout)
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(2 ** attempt); continue
        break
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200].replace(key, '[KEY]')}")
    txt = r.json()["choices"][0]["message"]["content"]
    p = json.loads(txt).get("pick", 0)
    return int(p) if str(p).lstrip("-").isdigit() else 0


def main(cands_path, out, limit):
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        sys.exit("DEEPSEEK_API_KEY not set")
    C = json.load(open(cands_path, encoding="utf-8"))
    terms = list(C)[:limit]
    acc, rej, err = {}, [], 0
    t0 = time.time()
    from concurrent.futures import ThreadPoolExecutor
    W = int(os.environ.get("BOZH_WORKERS", "8"))

    def one(t):
        it = C[t]
        if not it["cands"]:
            return t, None, None
        try:
            return t, ask(key, t, it), None
        except Exception as e:
            return t, None, str(e)[:200]
    with ThreadPoolExecutor(W) as ex:
        results = list(ex.map(one, terms))
    for i, (t, pick, e) in enumerate(results):
        it = C[t]
        if e:
            err += 1
            if err <= 3:
                print("ERR", t, e, flush=True)
            continue
        if pick is None:
            continue
        hit = it["cands"][pick - 1] if 1 <= pick <= len(it["cands"]) else None
        if hit is None:
            rej.append({"zh": t, "why": "none"}); continue
        cov = hit["c"] / it["df"]
        prec = hit["c"] / hit["dfb"] if hit.get("dfb") else 0.0
        ok = hit["c"] >= MIN_C and (cov >= MIN_COV or (prec >= MIN_PREC and cov >= LOW_COV and hit["c"] >= LOW_C))
        shown = hit.get("show", hit["bo"])
        if ok:
            acc[t] = {"bo": shown, "c": hit["c"], "df": it["df"], "cov": round(cov, 3), "prec": round(prec, 3), "dice": hit["dice"],
                      "lbs": [x["lb"] for x in it["examples"]]}
        else:
            rej.append({"zh": t, "why": f"cov {cov:.2f}, prec {prec:.2f}", "pick": shown})
    if err > len(terms) * 0.2:
        sys.exit(f"too many errors: {err}/{len(terms)}")
    R = {"model": MODEL, "asked": len(terms), "accepted": len(acc), "rejected": len(rej), "errors": err,
         "min_cov": MIN_COV, "min_prec": MIN_PREC, "low_cov": LOW_COV, "seconds": round(time.time() - t0, 1)}
    os.makedirs(out, exist_ok=True)
    json.dump(acc, open(os.path.join(out, "llm_pairs.json"), "w", encoding="utf-8"), ensure_ascii=False)
    json.dump({**R, "rejected_samples": rej[:40], "accepted_samples": dict(list(acc.items())[:40])},
              open(os.path.join(out, "llm_report.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(R, ensure_ascii=False))
    print("ACCEPTED_SAMPLE", json.dumps(dict(list(acc.items())[:25]), ensure_ascii=False))
    print("REJECTED_SAMPLE", json.dumps(rej[:15], ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 100000)
