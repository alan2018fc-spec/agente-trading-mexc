# Arranca el gateway de Ollama y el backend (que también sirve la interfaz).
#   powershell -ExecutionPolicy Bypass -File scripts\iniciar.ps1
$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
$py = Join-Path $raiz ".venv\Scripts\python.exe"
$backend = Join-Path $raiz "backend"

try { Invoke-RestMethod http://127.0.0.1:11434/api/version -TimeoutSec 3 | Out-Null }
catch { Write-Host "AVISO: Ollama no responde. La interfaz funciona, pero NO pulses Iniciar hasta que Ollama esté listo (el agente se pausaría con 'LLM no disponible')." -ForegroundColor Yellow }

Start-Process -FilePath $py -ArgumentList "-m","uvicorn","gateway:app","--host","127.0.0.1","--port","8001" -WorkingDirectory $backend -WindowStyle Minimized
Start-Sleep -Seconds 2
Write-Host "Gateway LLM en http://127.0.0.1:8001 (protegido con API key)" -ForegroundColor Cyan
Write-Host "Abre http://127.0.0.1:8000 en el navegador. Deja esta ventana abierta; Ctrl+C para detener." -ForegroundColor Green
Set-Location $backend
& $py -m uvicorn app.main:app --host 127.0.0.1 --port 8000
