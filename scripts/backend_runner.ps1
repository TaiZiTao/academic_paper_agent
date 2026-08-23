# GraphRAG backend runner: started hidden by the watchdog.
# Uses venv + proxy so the new code and external sources work.
# NOTE: keep this file ASCII-only so Windows PowerShell 5.1 parses it correctly.
$env:HTTPS_PROXY = "http://127.0.0.1:7897"
$env:HTTP_PROXY  = "http://127.0.0.1:7897"
$env:PYTHONPATH  = "E:/codex/GraphRAG--main"
Set-Location "E:/codex/GraphRAG--main"
& "E:/codex/GraphRAG--main/venv/Scripts/python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8000 --log-level warning