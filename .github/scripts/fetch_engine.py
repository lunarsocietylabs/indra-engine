import os,sys,json,urllib.request,urllib.error,hashlib,tarfile,io
def req(url,data=None,hdr=None):
    r=urllib.request.Request(url,data=data,headers=hdr or {})
    return urllib.request.urlopen(r,timeout=60).read()
aud="lunarsocietylabs-model-keys"
u=os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"]+"&audience="+aud
jwt=json.loads(req(u,hdr={"Authorization":"Bearer "+os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]}))["value"]
print("::add-mask::"+jwt)
r=json.loads(req("https://lunarsocietylabs.com/mcp/oidc/storage-token",json.dumps({"token":jwt}).encode(),{"Content-Type":"application/json"}))
tok=r["token"]; print("::add-mask::"+tok)
print("storage token ok; agent=%s scope=%s expires=%s"%(r.get("agent"),r.get("scope"),r.get("expires_at")))
import time; time.sleep(3)
base="https://lunarsocietylabs.com/lst-private-docs/docs/code-sync/engine-v4/"
H={"Authorization":"Bearer "+tok}
man=json.loads(req(base+"MANIFEST.json",hdr=H))
print("manifest keys:",sorted(man) if isinstance(man,dict) else type(man).__name__)
blob=b""; i=0
while True:
    try: blob+=req(base+"part-%03d"%i,hdr=H)
    except urllib.error.HTTPError as e:
        if e.code==404: break
        raise
    i+=1
h=hashlib.sha256(blob).hexdigest()
print("parts=%d bytes=%d sha256=%s"%(i,len(blob),h[:8]))
want=json.dumps(man)
if h not in want: print("WARNING: sha not found in manifest"); sys.exit(1)
os.makedirs("/tmp/eng_pkg",exist_ok=True)
with tarfile.open(fileobj=io.BytesIO(blob)) as t:
    names=t.getnames(); print("members=%d top=%s"%(len(names),sorted({n.split('/')[0] for n in names})))
    t.extractall("/tmp/eng_pkg",filter="data")
