# -*- coding: utf-8 -*-
"""Task5 E2E: 论文问答相关性判定 - 端到端验证脚本

POST /api/v1/papers/qa/stream, 解析 SSE, 输出每个问题的节点序列 + done 前 100 字。
输出 UTF-8 写入结果文件, 避免 GBK 控制台崩溃。
"""
import json
import sys
import time
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000"
STREAM_URL = BASE + "/api/v1/papers/qa/stream"

# (标签, 问题, 期望)
CASES = [
    ("常识", "什么是深度学习", "含 relevance_check + general_chat_node, 自由回答"),
    ("无关", "今天天气怎么样", "general_chat_node"),
    ("单篇", "PGDUN 的 PGSA 模块怎么实现", "relevance_check(rag)+direction_select+retrieve, 单篇匹配 PGDUN"),
    ("对比", "MWAT-SR 和 Dual-domain 有什么不同", "匹配两篇, done 是对比"),
    ("宽泛", "超分辨率方向有哪些方法", "rag(方向词命中), 行为不变"),
    ("闲聊", "你好", "chat_node"),
    ("清单", "有什么论文", "catalog_node"),
]


def post_question(text, session_id, timeout=600):
    body = json.dumps({"input_text": text, "session_id": session_id}).encode("utf-8")
    req = urllib.request.Request(
        STREAM_URL, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    nodes = []
    tokens = []
    done_content = ""
    citations = []
    error = None
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            # parse SSE: event: X\ndata: {...}\n\n
            buf = b""
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n\n" in buf:
                    raw, buf = buf.split(b"\n\n", 1)
                    ev_type = None
                    data_str = ""
                    for line in raw.decode("utf-8", "replace").split("\n"):
                        if line.startswith("event:"):
                            ev_type = line[6:].strip()
                        elif line.startswith("data:"):
                            data_str += line[5:].strip()
                    if ev_type is None:
                        continue
                    try:
                        payload = json.loads(data_str) if data_str else {}
                    except Exception:
                        payload = {}
                    if ev_type == "node":
                        nodes.append({
                            "node": payload.get("node", ""),
                            "status": payload.get("status", ""),
                            "intent_route": payload.get("intent_route", ""),
                            "intent": payload.get("intent", ""),
                            "direction": payload.get("direction", ""),
                            "matched_papers": payload.get("matched_papers"),
                            "citation_count": len(payload.get("citations", [])) if isinstance(payload.get("citations"), list) else None,
                        })
                    elif ev_type == "token":
                        tokens.append(payload.get("content", ""))
                    elif ev_type == "done":
                        done_content = payload.get("content", "")
                        citations = payload.get("citations", [])
                    elif ev_type == "error":
                        error = payload.get("detail", str(payload))
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    elapsed = time.time() - start
    node_seq = " -> ".join(n["node"] for n in nodes)
    intent_routes = [n["intent_route"] for n in nodes if n["intent_route"]]
    intents = [n["intent"] for n in nodes if n["intent"]]
    dirs = [n["direction"] for n in nodes if n["direction"]]
    matched = [n["matched_papers"] for n in nodes if n.get("matched_papers") is not None]
    return {
        "node_seq": node_seq,
        "nodes": nodes,
        "intent_routes": intent_routes,
        "intents": intents,
        "directions": dirs,
        "matched": matched,
        "done_first100": done_content[:100],
        "done_len": len(done_content),
        "citation_count": len(citations),
        "error": error,
        "elapsed_s": round(elapsed, 1),
    }


def main():
    out = []
    out.append("=" * 70)
    out.append("E2E 验证开始: " + time.strftime("%Y-%m-%d %H:%M:%S"))
    out.append("=" * 70)

    # 每个独立问题用独立 session(避免相互污染); 多轮用同一 session
    session_base = "e2e-task5-" + str(int(time.time()))
    results = []
    for idx, (label, q, expect) in enumerate(CASES):
        sid = f"{session_base}-{label}"
        out.append("\n" + "-" * 70)
        out.append(f"[{label}] Q: {q}")
        out.append(f"    期望: {expect}")
        res = post_question(q, sid)
        results.append((label, q, res))
        out.append(f"    节点序列: {res['node_seq']}")
        if res["intent_routes"]:
            out.append(f"    intent_route: {res['intent_routes']}")
        if res["intents"]:
            out.append(f"    intent: {res['intents']}")
        if res["directions"]:
            out.append(f"    direction: {res['directions']}")
        if res["matched"]:
            out.append(f"    matched_papers: {res['matched']}")
        out.append(f"    done前100字: {res['done_first100']!r}")
        out.append(f"    done长度: {res['done_len']}, citations: {res['citation_count']}")
        out.append(f"    耗时: {res['elapsed_s']}s")
        if res["error"]:
            out.append(f"    ERROR: {res['error']}")

    # 多轮: 同一 session, 先问 PGDUN, 再追问指代
    mt_sid = f"{session_base}-multiturn"
    out.append("\n" + "-" * 70)
    out.append("[多轮] 第一问: PGDUN 的 PGSA 模块怎么实现")
    res1 = post_question("PGDUN 的 PGSA 模块怎么实现", mt_sid)
    results.append(("多轮-1", "PGDUN 的 PGSA 模块怎么实现", res1))
    out.append(f"    节点序列: {res1['node_seq']}")
    out.append(f"    done前100字: {res1['done_first100']!r}")
    out.append(f"    耗时: {res1['elapsed_s']}s")
    out.append("")
    out.append("[多轮] 第二问(同一 session 追问指代): 那它跟普通展开网络有什么本质区别")
    res2 = post_question("那它跟普通展开网络有什么本质区别", mt_sid)
    results.append(("多轮-2", "那它跟普通展开网络有什么本质区别", res2))
    out.append(f"    节点序列: {res2['node_seq']}")
    out.append(f"    done前100字: {res2['done_first100']!r}")
    out.append(f"    耗时: {res2['elapsed_s']}s")

    out.append("\n" + "=" * 70)
    out.append("验证摘要")
    out.append("=" * 70)
    for label, q, res in results:
        seq = res["node_seq"]
        ok = "?" 
        out.append(f"[{label}] seq={seq} | done_len={res['done_len']} | err={res['error'] or '无'}")
    out.append("")
    report = "\n".join(out)
    # 写 UTF-8 结果文件
    out_path = r"E:/codex/GraphRAG--main/logs/e2e_qa_verify_report.txt"
    import os
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)
    # 同时打印(控制台可能 GBK, 但内容已落盘)
    sys.stdout.write(report)
    sys.stdout.flush()
    print("\n\nRESULT_FILE=" + out_path)


if __name__ == "__main__":
    main()
