"""Compatibility entry point: download a verified archive outside checkout.

The workflow assembles it with tools/prepare_indra_runtime.py. This entry point
never extracts private modules into the public repository.
"""
from oidc_store import download_engine

def cli():
    try:
        # Preserve the shell workflow's legacy package selection.
        download_engine("engine-v4")
        return 0
    except Exception:
        import sys
        print("FAIL: private engine download failed; no response payload was printed.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(cli())
