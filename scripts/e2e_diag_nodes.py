# -*- coding: utf-8 -*-
import json, urllib.request, time

STREAM_URL = "http://127.0.0.1:8000/api/v1/papers/qa/stream"

def run(text, sid):
    body = json.dumps({"input_text": text, "session_id": sid}).encode("utf-8")
    req = urllib.request.Request(STREAM_URL, data=body, method="POST", headers={"Content-Type": "application/json"})
    events = []
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
                if ev == "node":
                    try: p = json.loads(ds)
                    except Exception: p = {}
                    events.append(p)
    return events

out = []
out.append("==== Q1: PGDUN 的 PGSA 模块怎么实现 ====")
evs = run("PGDUN 的 PGSA 模块怎么实现", "diag-pgdun-" + str(int(time.time())))
for e in evs:
    keep = {k: v for k, v in e.items() if k not in ("evidence", "raw_citations")}
    out.append(json.dumps(keep, ensure_ascii=False))
out.append("")
out.append("==== Q2: MWAT-SR 和 Dual-domain 有什么不同 ====")
evs2 = run("MWAT-SR 和 Dual-domain 有什么不同", "diag-mwat-" + str(int(time.time())))
for e in evs2:
    keep = {k: v for k, v in e.items() if k not in ("evidence", "raw_citations")}
    out.append(json.dumps(keep, ensure_ascii=False))
report = "\n".join(out)
with open(r"E:/codex/GraphRAG--main/logs/e2e_diag_nodes.txt", "w", encoding="utf-8") as f:
    f.write(report)
print("done")
