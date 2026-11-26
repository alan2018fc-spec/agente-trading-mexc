# Instala todo en Windows. Ejecutar desde la carpeta del repositorio:
#   powershell -ExecutionPolicy Bypass -File scripts\instalar.ps1
$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz

Write-Host "== 1/5 Comprobando Python, Node y Ollama" -ForegroundColor Cyan
$py = $null
foreach ($c in @("python", "py")) {
  try {
    $ok = & $c -c "import sys; print(sys.version_info >= (3, 10))" 2>$null
    if ($LASTEXITCODE -eq 0 -and "$ok".Trim() -eq "True") { $py = $c; break }
  } catch { }
}
if (-not $py) {
  Write-Host "No se encontró Python 3.10 o más reciente." -ForegroundColor Red
  Write-Host "Instálalo con:  winget install -e --id Python.Python.3.12" -ForegroundColor Yellow
  Write-Host "Después CIERRA esta ventana, abre una nueva y vuelve a ejecutar este script." -ForegroundColor Yellow
  exit 1
}
& $py --version
try { node --version } catch { Write-Host "No se encontró Node.js. Instálalo y abre una ventana nueva." -ForegroundColor Red; exit 1 }
$hayOllama = $true
try { ollama --version } catch { $hayOllama = $false; Write-Host "Ollama todavía no está instalado: se instala todo lo demás y el modelo del LLM se descarga después." -ForegroundColor Yellow }

Write-Host "== 2/5 Entorno de Python (.venv) y dependencias" -ForegroundColor Cyan
if (-not (Test-Path ".venv\Scripts\python.exe")) { & $py -m venv .venv }
if (-not (Test-Path ".venv\Scripts\python.exe")) { Write-Host "No se pudo crear el entorno .venv" -ForegroundColor Red; exit 1 }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip | Out-Null
& .\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt

Write-Host "== 3/5 Claves en backend\.env (solo en tu computadora)" -ForegroundColor Cyan
if (-not (Test-Path "backend\.env")) {
  $gen = { & .\.venv\Scripts\python.exe -c "import secrets;print(secrets.token_urlsafe(32))" }
  $contenido = Get-Content "backend\.env.example" -Raw
  $pwd_admin = & .\.venv\Scripts\python.exe -c "import secrets;print(secrets.token_urlsafe(12))"
  $contenido = $contenido -replace "BACKEND_API_KEY=\r?\n", "BACKEND_API_KEY=$(& $gen)`n"
  $contenido = $contenido -replace "ADMIN_PASSWORD=\r?\n", "ADMIN_PASSWORD=$pwd_admin`n"
  $contenido = $contenido -replace "LLM_GATEWAY_KEY=\r?\n", "LLM_GATEWAY_KEY=$(& $gen)`n"
  Set-Content -Path "backend\.env" -Value $contenido -Encoding UTF8
  Write-Host "Contraseña de la interfaz: $pwd_admin  (también está en backend\.env)" -ForegroundColor Yellow
} else { Write-Host "backend\.env ya existe: se conserva." }

Write-Host "== 4/5 Modelo local de Ollama (qwen2.5:3b, ~1.9 GB)" -ForegroundColor Cyan
if ($hayOllama) { ollama pull qwen2.5:3b }
else { Write-Host "Pendiente: cuando instales Ollama, abre una ventana nueva y ejecuta:  ollama pull qwen2.5:3b" -ForegroundColor Yellow }

Write-Host "== 5/5 Interfaz React (compilada)" -ForegroundColor Cyan
Set-Location frontend
npm install --no-audit --no-fund
npm run build
Set-Location $raiz

Write-Host "`nListo. Siguiente paso: scripts\iniciar.ps1" -ForegroundColor Green
