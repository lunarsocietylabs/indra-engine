"""OIDC -> storage token -> REST read/write of lst-private-docs/docs/code-sync/. Values are masked in the log."""
import os, json, time, urllib.request, urllib.error
import hashlib, re, tempfile
from pathlib import Path
BASE = "https://lunarsocietylabs.com/lst-private-docs/docs/code-sync/"
_tok = {"v": None, "t": 0}

def _req(url, data=None, hdr=None, method=None, retries=4):
    for a in range(retries):
        try:
            r = urllib.request.Request(url, data=data, headers=hdr or {}, method=method)
            return urllib.request.urlopen(r, timeout=120).read()
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404) and a >= 1 or a == retries - 1:
                raise
        except Exception:
            if a == retries - 1:
                raise
        time.sleep(2 * (a + 1))

def token():
    if _tok["v"] and time.time() - _tok["t"] < 5400:
        return _tok["v"]
    u = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"] + "&audience=lunarsocietylabs-model-keys"
    jwt = json.loads(_req(u, hdr={"Authorization": "Bearer " + os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]}))["value"]
    print("::add-mask::" + jwt)
    r = json.loads(_req("https://lunarsocietylabs.com/mcp/oidc/storage-token", json.dumps({"token": jwt}).encode(), {"Content-Type": "application/json"}))
    _tok["v"], _tok["t"] = r["token"], time.time()
    print("::add-mask::" + r["token"])
    time.sleep(3)
    return r["token"]

def get(path):
    return _req(BASE + path, hdr={"Authorization": "Bearer " + token()})

def put(path, data, ctype="application/octet-stream"):
    return _req(BASE + path, data=data, method="PUT", hdr={"Authorization": "Bearer " + token(), "Content-Type": ctype})

def download_engine(prefix=None):
    """Verify the private bundle, keep it outside checkout, never extract here."""
    prefix_path = Path(__file__).resolve().parents[1] / "engine_prefix"
    if prefix is None:
        prefix = prefix_path.read_text().strip() if prefix_path.exists() else "engine-v4"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", prefix):
        raise ValueError("invalid engine prefix")
    manifest = json.loads(get(prefix + "/MANIFEST.json"))
    expected = manifest.get("tar_sha256") or manifest.get("sha256")
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("engine manifest has no recognized checksum")
    parts = manifest.get("parts")
    if parts is not None and (type(parts) is not int or not 1 <= parts <= 128):
        raise ValueError("invalid engine part count")
    chunks = []
    total = 0
    for i in range(parts if parts is not None else 128):
        try:
            chunk = get(prefix + "/part-%03d" % i)
        except urllib.error.HTTPError as error:
            if error.code == 404 and parts is None:
                break
            raise
        total += len(chunk)
        if total > 64_000_000:
            raise ValueError("engine archive exceeds size limit")
        chunks.append(chunk)
    else:
        if parts is None:
            raise ValueError("engine archive has too many parts")
    blob = b"".join(chunks)
    if hashlib.sha256(blob).hexdigest() != expected:
        raise ValueError("engine archive checksum mismatch")
    temp_root = Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir())).resolve()
    for ancestor in [temp_root, *temp_root.parents]:
        if (ancestor / ".git").exists():
            raise ValueError("private download cannot use a Git checkout")
    folder = Path(tempfile.mkdtemp(prefix="indra-private-download-", dir=temp_root))
    folder.chmod(0o700)
    archive = folder / "engine.tar"
    archive.write_bytes(blob)
    archive.chmod(0o600)
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (folder / "manifest.json").chmod(0o600)
    if os.environ.get("GITHUB_ENV"):
        with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as handle:
            handle.write("INDRA_PRIVATE_ARCHIVE=" + str(archive) + "\n")
            handle.write("INDRA_PRIVATE_DOWNLOAD_DIR=" + str(folder) + "\n")
    print("private engine archive verified; source remains outside checkout")
    return archive


def main():
    import sys
    if sys.argv[1] == "engine":
        download_engine()
    elif sys.argv[1] == "put":      # put <localfile> <remote path>
        d = open(sys.argv[2], "rb").read(); put(sys.argv[3], d); print("put ok", len(d))
    elif sys.argv[1] == "get":      # get <remote path> <localfile>
        open(sys.argv[3], "wb").write(get(sys.argv[2])); print("get ok")
    elif sys.argv[1] == "putdir":   # putdir <localdir> <remote prefix>  (files >7.5MiB are split into .partNNN)
        for f in sorted(os.listdir(sys.argv[2])):
            d = open(os.path.join(sys.argv[2], f), "rb").read(); N = 7_500_000
            if len(d) <= N: put(sys.argv[3] + f, d)
            else:
                for k in range(0, len(d), N): put(sys.argv[3] + f + ".part%03d" % (k // N), d[k:k + N])
        print("putdir ok", len(os.listdir(sys.argv[2])))


def cli():
    try:
        main()
        return 0
    except Exception:
        import sys
        print("FAIL: private storage operation failed; no response payload was printed.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(cli())
