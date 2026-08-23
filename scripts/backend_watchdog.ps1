# GraphRAG backend watchdog: invoked by scheduled task every 2 minutes.
# Rules: listener on 8000 -> exit; any uvicorn main:app process -> exit;
#         otherwise spawn one hidden venv backend (with proxy) via WMI.
# NOTE: keep this file ASCII-only so Windows PowerShell 5.1 parses it correctly.
$port = 8000
$listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($listener) { exit 0 }

$existing = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'uvicorn main:app' }
if ($existing) { exit 0 }

$cmd = "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File E:\codex\GraphRAG--main\scripts\backend_runner.ps1"
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd; CurrentDirectory = "E:\codex\GraphRAG--main" }
if ($r.ReturnValue -ne 0) { Write-Output ("watchdog spawn failed, code=" + $r.ReturnValue) }