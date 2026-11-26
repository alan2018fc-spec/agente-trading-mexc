# Ejecuta las pruebas automatizadas (incluye los 9 errores del protocolo). No necesita Ollama ni internet.
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $raiz "backend")
& (Join-Path $raiz ".venv\Scripts\python.exe") -m pytest -v
