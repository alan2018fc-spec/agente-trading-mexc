// Cliente del backend. Sin secretos: la sesión viaja en una cookie httpOnly que el navegador maneja.
export class NoAutenticado extends Error {}

async function pedir<T>(ruta: string, opciones: RequestInit = {}): Promise<T> {
  const r = await fetch(ruta, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...opciones,
  });
  if (r.status === 401) throw new NoAutenticado("Sesión no iniciada o expirada");
  const texto = await r.text();
  const datos = texto ? JSON.parse(texto) : null;
  if (!r.ok) throw new Error((datos && datos.detail) || `Error HTTP ${r.status}`);
  return datos as T;
}

export const api = {
  login: (password: string) => pedir("/api/login", { method: "POST", body: JSON.stringify({ password }) }),
  logout: () => pedir("/api/logout", { method: "POST" }),
  estado: () => pedir<Estado>("/api/estado"),
  pares: (modo = "vivo") => pedir<{ fecha_vela: string | null; pares: Decision[] }>(`/api/pares?modo=${modo}`),
  decisiones: (modo = "vivo") => pedir<Decision[]>(`/api/decisiones?modo=${modo}`),
  pnl: (modo = "vivo") => pedir<Pnl>(`/api/pnl?modo=${modo}`),
  ordenes: (modo = "vivo") => pedir<Orden[]>(`/api/ordenes?modo=${modo}`),
  eventos: () => pedir<Evento[]>("/api/eventos"),
  ciclos: (modo = "vivo") => pedir<Ciclo[]>(`/api/ciclos?modo=${modo}`),
  patrimonio: (modo = "vivo") => pedir<PuntoPatrimonio[]>(`/api/patrimonio?modo=${modo}`),
  metricas: (modo = "vivo") => pedir<Metrica[]>(`/api/metricas?modo=${modo}`),
  salud: () => pedir<Salud>("/api/salud"),
  control: (accion: string) => pedir<any>(`/api/agente/${accion}`, { method: "POST" }),
  replay: (dias: number, usar_llm: boolean) =>
    pedir("/api/replay", { method: "POST", body: JSON.stringify({ dias, usar_llm }) }),
  cerebro: () => pedir<Cerebro>("/api/cerebro"),
  red: (simbolo: string) => pedir<Red>(`/api/red?simbolo=${simbolo}`),
  feeds: (verificar = false) => pedir<Record<string, Feed>>(`/api/feeds${verificar ? "?verificar=true" : ""}`),
  avisos: () => pedir<{ configurado: boolean; destino: string; proveedor: string; avisos: Aviso[] }>("/api/notificaciones"),
  probarWhatsapp: () => pedir<{ ok: boolean; destino: string }>("/api/notificaciones/prueba", { method: "POST" }),
  cambiarRiesgo: (cambios: Record<string, number>) =>
    pedir("/api/config/riesgo", { method: "PUT", body: JSON.stringify(cambios) }),
};

export interface Posicion { simbolo: string; cantidad: number; precio_entrada: number; costo_entrada: number; fecha_entrada: string }
export interface Cuenta { efectivo: number; patrimonio: number; posiciones: Posicion[] }
export interface Estado {
  estado: string; motivo: string; modo: string; trading_real: boolean; ultima_vela_procesada: string | null;
  ultimo_tick: string | null; duracion_ultimo_ciclo_s: number | null; poll_segundos: number;
  modelo: { version: string; huella: string; regla: { delta: number; filtro_SMA20: boolean }; dif_vs_keras: number; run_id: string };
  llm: { modelo: string }; riesgo: Record<string, any>; cuentas: Record<string, Cuenta>;
  replay: Record<string, any>;
  whatsapp: { configurado: boolean; destino: string; proveedor: string };
  version?: string; fecha_version?: string;
}
export interface Aviso { ts_utc: string; simbolo: string; fecha_vela: string; estado: string; detalle: string | null; mensaje: string }
export interface Decision {
  id: number; modo: string; simbolo: string; fecha_vela: string; ts_utc: string; cierre: number | null;
  pronostico: number | null; dist_sma20: number | null; vol20: number | null; sma20: number | null;
  accion_modelo: string | null; accion_llm: string | null; justificacion: string | null; herramientas: string | null;
  json_valido: number | null; accion_final: string; aprobada: number; motivo: string; latencia_llm_ms: number | null;
  posicion?: Posicion | null;
}
export interface Orden {
  id: number; estrategia: string; simbolo: string; fecha_vela: string; lado: string; ts_utc: string; cantidad: number;
  precio: number; monto: number; costo: number; efectivo_despues: number; precio_referencia: string;
}
export interface Evento { id: number; ts_utc: string; nivel: string; etapa: string; tipo: string; mensaje: string }
export interface Ciclo { id: number; fecha_vela: string; inicio_utc: string; fin_utc: string | null; estado: string; n_decisiones: number; n_rechazos: number; latencia_llm_ms_prom: number | null }
export interface PuntoPatrimonio { estrategia: string; fecha_vela: string; patrimonio: number }
export interface Metrica {
  estrategia: string; dias: number; desde: string | null; hasta: string | null; patrimonio_final: number;
  rendimiento_neto_pct: number; drawdown_max_pct: number; ordenes: number; operaciones_cerradas: number;
  win_rate_pct: number | null; profit_factor: number | null; costos_totales: number;
  decisiones?: number; acciones_rechazadas?: number; json_invalidos?: number; latencia_llm_ms_prom?: number | null;
  llm_difiere_de_regla?: number;
}
export interface Salud {
  trading_real: boolean; ultimo_tick: string | null;
  mexc: { ok: boolean; latencia_ms?: number; error?: string };
  llm: { ok: boolean; modelo?: string; ollama_version?: string; latencia_ms?: number; parametros?: string; cuantizacion?: string; url_local?: string; modelo_descargado?: boolean; error?: string };
  sistema: { memoria_backend_mb?: number; memoria_libre_mb?: number; cpu_pct?: number };
}

export interface Feed { velas?: number; ultima_vela?: string | null; antiguedad_h?: number | null; revisado_utc?: string; estado: string; mensaje?: string; latencia_ms?: number }
export interface Cerebro {
  estado: string; motivo: string; ultima_vela: string | null; ultimo_tick: string | null;
  salud: { mexc: { ok: boolean; latencia_ms?: number }; llm: { ok: boolean; latencia_ms?: number; ollama_version?: string } };
  ciclo: Ciclo | null; conteos: { n?: number; compras?: number; ventas?: number; mantener?: number; rechazos?: number; json_invalidos?: number; con_herramientas?: number; lat?: number | null };
  feeds_ok: number; feeds_total: number; replay?: string; modelo: string; llm_modelo: string;
  eventos: { ts_utc: string; nivel: string; etapa: string; tipo: string; mensaje: string }[];
}
export interface Red {
  simbolo: string; fecha_vela: string; fuente: string; delta: number; pronostico: number;
  entradas: { seq: number[]; seq_z: number[]; dist_sma20: number; vol20: number; ind_z: number[]; par_indice: number };
  semillas: { semilla: number; h: number[]; d: number[]; salida_z: number; salida: number }[];
  pesos_semilla_11: { d1: number[][]; d2: number[] };
  arquitectura: { pasos: number; lstm: number; pares: number; indicadores: number; densa: number; semillas: number[] };
  decision: { accion_final: string; motivo: string } | null;
}

export interface PnlEstrategia {
  estrategia: string; pnl_realizado: number; pnl_no_realizado: number; pnl_total: number; pnl_total_pct: number;
  cerradas: number; abiertas: number; ganadoras: number; win_rate_pct: number | null;
}
export interface Operacion {
  estrategia: string; simbolo: string; estado: "cerrada" | "abierta"; cantidad: number; fecha_entrada: string;
  precio_entrada: number; fecha_salida: string | null; precio_salida: number; invertido: number; pnl: number; pnl_pct: number;
}
export interface PnlDiario { estrategia: string; fecha_vela: string; pnl: number; pnl_pct: number; acumulado_pct: number }
export interface Pnl { modo: string; precios_al: string | null; estrategias: PnlEstrategia[]; operaciones: Operacion[]; diario: PnlDiario[] }
