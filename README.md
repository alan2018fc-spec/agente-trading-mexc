# Agente de trading MEXC (Paper) — proyecto final DL para Finanzas

Agente que, una vez al día, observa datos públicos de MEXC, consulta el modelo LSTM de la práctica, pide a un LLM local (Ollama) una propuesta, la pasa por controles de riesgo deterministas, la ejecuta en un simulador **Paper** y registra todo. **No envía órdenes reales**: no existe código firmado ni rutas de órdenes, y `TRADING_REAL=1` impide que arranque.

```
React + TypeScript ──(cookie de sesión)──▶ Backend FastAPI :8000 ──▶ Modelo LSTM (NumPy, pesos de la T4)
                                             │      │
                                             │      └──(X-API-Key)──▶ Gateway :8001 ──▶ Ollama local :11434 (qwen2.5:3b)
                                             ├──▶ Motor de riesgo determinista ──▶ Motor Paper ──▶ SQLite (registro)
                                             └──▶ API pública de MEXC (velas cerradas, libro, exchangeInfo)
```

## Ciclo del agente (una vez por vela diaria)

La vela diaria de MEXC cierra a las 00:00 UTC (18:00 en Ciudad de México). El backend revisa cada 60 s si cerró una vela nueva; **no acelera la frecuencia del modelo**.

1. Consulta las velas **cerradas** de los 15 pares y valida activo, frecuencia diaria, orden, continuidad y antigüedad (≤ 26 h).
2. Calcula las variables con la **misma definición del entrenamiento** (20 rendimientos, `dist_sma20`, `vol20`) y obtiene el pronóstico del ensamble LSTM-B (3 semillas).
3. Consulta a Ollama con datos estructurados y dos herramientas de **solo lectura** (`consultar_posicion`, `consultar_historial`).
4. Exige JSON `{accion, simbolo, fecha_vela, justificacion}`; valida tipos y valores; rechaza símbolos o acciones no permitidos.
5. Aplica el motor de riesgo (el LLM no puede saltárselo ni cambiar sus límites).
6. Ejecuta en Paper y actualiza efectivo, posiciones y patrimonio. La misma vela nunca se procesa dos veces.
7. Muestra el resultado en la interfaz y espera la siguiente vela o la intervención del usuario.

En paralelo se llevan tres libros Paper con el mismo periodo y costos: **agente con LLM**, **modelo sin LLM** (regla de la práctica: comprar si el pronóstico > 0.30 %) y **comprar y mantener**.

## Modelo

Archivos en `backend/modelo/`, exportados de la corrida `20261120_144755` (notebook `BD2_MEXC15_Cap7a11_v2.1.ipynb`, Colab T4):

- `lstm16_B_pesos.npz`: pesos de las 3 LSTM (16 neuronas, con indicadores) y salidas de referencia de Keras.
- `escaladores.json`: escaladores ajustados solo con TR.
- `contrato_variables.json`: orden de pares, variables, definiciones y regla congelada.

La inferencia se hace con NumPy para no instalar TensorFlow (la computadora tiene 8 GB de RAM). Al arrancar se verifica contra Keras (diferencia máxima 1.5 × 10⁻⁷), y una prueba confirma que con precios reales de MEXC da los mismos pronósticos que el notebook (BTC +0.2502 %, LTC +0.6067 %, XRP −0.1158 % para la vela del 2026-11-19).

## Instalación en Windows

Requisitos: Python 3.11+ (con "Add to PATH"), Node.js 20+ y [Ollama](https://ollama.com/download).

```powershell
powershell -ExecutionPolicy Bypass -File scripts\instalar.ps1   # entorno, dependencias, claves, modelo de Ollama, interfaz
powershell -ExecutionPolicy Bypass -File scripts\iniciar.ps1    # gateway + backend
```

Abre <http://127.0.0.1:8000>, entra con la contraseña que mostró el instalador (está en `backend\.env`) y pulsa **Iniciar**. Deja la computadora encendida y sin suspender a las 18:00. Si estaba apagada, al encenderla se procesa la última vela, siempre que no tenga más de 26 h.

Pruebas automatizadas (no necesitan Ollama ni internet):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\probar.ps1
```

## Controles de riesgo (`backend/config/riesgo.json`)

| Control | Valor |
| --- | --- |
| Capital inicial | 50,000 USDT |
| Costo por compra o venta | 0.0 % |
| Exposición máxima por par | 6.67 % del patrimonio (1/15) |
| Exposición máxima total | 100 % (sin apalancamiento) |
| Pérdida diaria máxima | 5 % → bloquea nuevas órdenes |
| Antigüedad máxima del dato | 26 h |
| Deslizamiento supuesto | 0.05 % (compra al ask, venta al bid) |
| Precisión y tamaño mínimo | `baseSizePrecision` y `quoteAmountPrecision` de `exchangeInfo` de MEXC |

Solo se pueden cambiar con sesión iniciada y con el agente detenido o pausado; cada cambio queda en la bitácora. **Detener** bloquea nuevas órdenes pero **no liquida**: liquidar es una acción aparte y explícita.

## Seguridad

- Las claves viven solo en `backend/.env` (excluido de git). `.env.example` no tiene valores.
- La interfaz no contiene secretos ni variables `VITE_`: el usuario entra con contraseña y recibe una cookie de sesión httpOnly.
- Iniciar, pausar, detener, liquidar, replay y cambiar la configuración requieren sesión o `X-API-Key`.
- Ollama no exige autenticación por defecto; `backend/gateway.py` agrega la API key, escucha solo en 127.0.0.1, solo permite el modelo configurado y rechaza claves ausentes o incorrectas.
- El LLM nunca recibe claves. Los datos externos se tratan como datos, no como instrucciones.
- Los logs filtran las claves.

## Los 9 errores del protocolo

`backend/tests/test_errores_protocolo.py` los provoca y verifica la respuesta:

| # | Error | Respuesta del sistema |
| --- | --- | --- |
| 1 | API de MEXC sin respuesta o con límite (429) | 3 reintentos con espera creciente → estado PAUSADO_ERROR, evento registrado, sin órdenes |
| 2 | Dato atrasado | El par queda SIN_DECISION ("dato atrasado") y el riesgo rechaza datos de más de 26 h |
| 3 | Ollama detenido | 3 reintentos → PAUSADO_ERROR; la vela queda pendiente, sin órdenes del agente |
| 4 | JSON inválido (o símbolo no permitido) | Propuesta rechazada, se registra, no se opera |
| 5 | Clave incorrecta | 401 en la API y en el gateway; eventos `clave_incorrecta` |
| 6 | Saldo insuficiente | El simulador y el riesgo rechazan la compra; el saldo no cambia |
| 7 | Evento duplicado | La misma vela no se reprocesa; no se duplica ninguna orden |
| 8 | Reinicio del backend | Recupera estado, posiciones y última vela desde SQLite sin duplicar |
| 9 | Botón de detener | Bloquea nuevas órdenes; conserva posiciones; liquidar es aparte |

## Interfaz

Pestañas: **Operación** (pares, pronóstico, indicadores, acción propuesta y motivo, riesgo, posición y patrimonio), **Cerebro** (constelación animada de los componentes con su estado: feeds, validador, variables, LSTM, LLM, herramientas, validador JSON, riesgo, Paper y registro), **Red neuronal** (la LSTM capa por capa con las activaciones reales del último pronóstico de cada par y los pesos entrenados), **Rendimiento** (agente con LLM contra modelo sin LLM y comprar y mantener), **Riesgo**, **Historial**, **Replay** y **Sistema** (latencias, memoria, feeds e integridad de los 15 pares y bitácora).

## Replay

La pestaña **Replay** repite los últimos N días cerrados con el mismo ciclo, ejecutando cada señal en la apertura del día siguiente. Sus resultados se guardan en modo `replay`, separados de la operación en vivo, y se marcan como REPLAY en la interfaz. Con el LLM en CPU tarda alrededor de 1 minuto por día.

## Persistencia y evidencia

`backend/data/agente.db` (SQLite) guarda velas, ciclos, decisiones (incluye la justificación del LLM, las herramientas usadas y la latencia), órdenes Paper, costos, patrimonio y eventos. `backend/logs/` tiene los logs del backend y del gateway (modelo, tiempo de respuesta). La API documentada está en <http://127.0.0.1:8000/docs>.
