# -*- coding: utf-8 -*-
import json, urllib.request, time

STREAM_URL = "http://127.0.0.1:8000/api/v1/papers/qa/stream"

def run(text, sid):
    body = json.dumps({"input_text": text, "session_id": sid}).encode("utf-8")
    req = urllib.request.Request(STREAM_URL, data=body, method="POST", headers={"Content-Type": "application/json"})
    done = ""
    with urllib.request.urlopen(req, timeout=600) as resp:
        buf = b""
        while True:
            chunk = resp.read(4096)
            if not chunk: break
            buf += chunk
            while b"\n\n" in buf:
                raw, buf = buf.split(b"\n\n", 1)
                ev = None; ds = ""
                for line in raw.decode("utf-8", "replace").split("\n"):
                    if line.startswith("event:"): ev = line[6:].strip()
                    elif line.startswith("data:"): ds += line[5:].strip()
                if ev == "done":
                    try: p = json.loads(ds); done = p.get("content", "")
                    except Exception: pass
    return done

sid = "full2-" + str(int(time.time()))
d1 = run("PGDUN 的 PGSA 模块怎么实现", sid + "-a")
d2 = run("MWAT-SR 和 Dual-domain 有什么不同", sid + "-b")
report = "==== FULL DONE A: PGDUN 的 PGSA 模块怎么实现 ====\n" + d1 + "\n\n==== FULL DONE B: MWAT-SR 和 Dual-domain 有什么不同 ====\n" + d2
with open(r"E:/codex/GraphRAG--main/logs/e2e_full_done2.txt", "w", encoding="utf-8") as f:
    f.write(report)
print("done")
