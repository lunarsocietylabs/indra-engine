# Indra Engine · Public License and Disclosure Policy

**This is a public AGPL-3.0 project.** The repository's existing `LICENSE` applies as documented for the public source. AGPL-3.0 allows commercial use; it is **not** an academic-only or noncommercial-use license. For modified AGPL programs interacted with over a network, the corresponding source-offer provisions may apply.

## What belongs in this public repository

- Public V3 and generic algorithm components, tests and non-sensitive development tools.
- Public GitHub Actions workflow orchestration and free standard GitHub-hosted Runner scripts.
- Original public documentation and interfaces only when its publication rights are verified.

## What must never be added

- Private V4 full-match, relation, annotation and re-commentary implementations located in the private `lunarsocietylabs/zelizuli.com/engine/v4/` library.
- V4 private test suites, detailed tuning knobs, proprietary datasets, private R2 objects, private artifact bundles or credentials.
- CBETA, MITRA, Esukhia or other third-party corpus text solely on the assumption it is freely redistributable.
- Unredacted CI logs or artifacts containing secret material, temporary tokens, sensitive corpus payloads or private source.

A brief historical V4 exposure on 2026-11-10 in public commits #11/#12 remains part of public Git history even after its removal in #13. This public-history fact is **not** erased by repo path changes.

## Guard and procedure

1. Before any public commit, run `python3 tools/check_public_boundary.py` on the local checkout or a temporary clean clone.
2. The lightweight `public-ip-boundary` GitHub Actions workflow runs on public push and pull requests, using the free standard `ubuntu-latest` Runner (no private project Actions pool, no Blacksmith, no Cloudflare Builds).
3. **The post-push check is not a guarantee against disclosure**: if someone pushes directly, source can become public before CI fails. Require PR review and repository branch rulesets, or an upstream pre-publication review, for truly preventive control.
4. Keep copyright ownership records and secure release decisions in the *private* project's `docs/legal/` directory, not in public workflow artifacts.
5. If a future independently owned original module is intentionally offered under a new noncommercial software license, release it as a separately scoped project/revision after copyright-holder approval. Do not silently rewrite existing AGPL grants or relabel third-party code.

## Rights to public V3

The public V3 release remains a contribution to the open-source community under AGPL-3.0, including the rights allowed by that license. The company can consider different license terms for **future work it fully owns** after reviewing all contributors and compatibility with existing code.

Official sources:
- https://www.gnu.org/licenses/agpl-3.0.en.html
- https://www.gnu.org/licenses/gpl-faq.html
- https://savannah.gnu.org/maintenance/DualLicensing/

## Publication-first security (updated 2026-11-13)

**Do not stage sensitive V4 candidates on any branch of this public repository.** Both pull-request branches and main are publicly visible as soon as content is pushed; CI and main-branch PR rules are too late to stop a first-time leak.

- Pre-publication scan before any public Git push **or GitHub API content write**: `python3 tools/check_public_boundary.py --scan-dir /path/to/private/staging`.
- Scanning the currently tracked public checkout: `python3 tools/check_public_boundary.py`.
- Test the scanner's own false negatives: `python3 tools/test_public_boundary.py`. These tests use synthetic data, not real credentials.
- The scanner rejects known private V4 source identifiers even after a simple rename, unexpected private archives and model files, local `.env*` credentials, common token patterns, and large tracked files. **This is an error-catching helper, not a proof of non-disclosure.** A new sensitive implementation may have no matching signature.
- The GitHub public standard Runner's `public-ip-boundary / check` job must PASS on PR. Strong enforcement requires an administrator-created branch ruleset. Repository rulesets were absent at last inspection, and the connected GitHub App lacks the Administration scope. A tested proposed rule lives in the private Zelizuli project, `docs/legal/INDRA_PUBLIC_MAIN_RULESET_IMPORT.json`; it is a **proposal, not active GitHub protection**.
- Current version of the guard has passed seven synthetic negative test cases and a public tracked-tree scan in [GitHub Actions run 43385473274](https://github.com/lunarsocietylabs/indra-engine/actions/runs/43385473274).
