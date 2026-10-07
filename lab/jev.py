import json, os, time, pathlib, urllib.request, urllib.error

def _key():
    k = os.environ.get("TYPESAFE_API_KEY")
    if k: return k
    p = pathlib.Path.home() / ".typesafe.env"
    for line in p.read_text().splitlines():
        if line.startswith("TYPESAFE_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"')
    raise RuntimeError("no key")

def ask(state, questions, timeout=20, retries=3):
    body = json.dumps({"model": "jev-latest", "state": state, "questions": questions}).encode()
    for i in range(retries):
        req = urllib.request.Request("https://api.typesafe.ai/v1/systemone", data=body,
            headers={"Authorization": f"Bearer {_key()}", "Content-Type": "application/json"})
        t = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out = json.loads(r.read())
                out["_ms"] = round((time.perf_counter() - t) * 1000)
                return out
        except urllib.error.HTTPError as e:
            if e.code in (429, 529) and i < retries - 1:
                time.sleep(2 ** i); continue
            raise RuntimeError(f"HTTP {e.code}: {e.read()[:300]!r}")
