export const usd = (x: number | null | undefined, d = 2) =>
  x == null || !isFinite(x) ? "—" : x.toLocaleString("es-MX", { minimumFractionDigits: d, maximumFractionDigits: d });
export const pct = (x: number | null | undefined, d = 2) => (x == null || !isFinite(x) ? "—" : `${x >= 0 ? "+" : ""}${x.toFixed(d)} %`);
export const pctDec = (x: number | null | undefined, d = 3) => (x == null || !isFinite(x) ? "—" : pct(x * 100, d));
export const num = (x: number | null | undefined, d = 6) => (x == null || !isFinite(x) ? "—" : Number(x.toPrecision(d)).toString());
export const hora = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString("es-MX") : "—");
export const NOMBRE_ESTRATEGIA: Record<string, string> = {
  agente_llm: "Agente con LLM",
  modelo: "Modelo sin LLM",
  comprar_mantener: "Comprar y mantener",
};
