"""OIDC -> storage token -> REST read/write of lst-private-docs/docs/code-sync/. Values are masked in the log."""
import os, json, time, urllib.request, urllib.error
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

if __name__ == "__main__":
    import sys, hashlib, tarfile, io
    if sys.argv[1] == "engine":
        pre = (open(".github/engine_prefix").read().strip() if os.path.exists(".github/engine_prefix") else "engine-v4") + "/"
        man = json.loads(get(pre + "MANIFEST.json")); blob = b""; i = 0
        while True:
            try: blob += get(pre + "part-%03d" % i)
            except urllib.error.HTTPError as e:
                if e.code == 404: break
                raise
            i += 1
        assert hashlib.sha256(blob).hexdigest() in json.dumps(man), "engine sha mismatch"
        os.makedirs("/tmp/eng_pkg", exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(blob)) as t:
            t.extractall("/tmp/eng_pkg", filter="data")
        print("engine ok, parts=%d" % i)
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
