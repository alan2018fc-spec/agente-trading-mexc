import { PuntoPatrimonio } from "./api";
import { NOMBRE_ESTRATEGIA, pct } from "./formato";

const COLORES: Record<string, string> = { agente_llm: "#4f8cff", modelo: "#9aa4b2", comprar_mantener: "#c2884a" };

// Patrimonio por estrategia (SVG propio, sin librerías).
export function GraficaPatrimonio({ puntos, capital }: { puntos: PuntoPatrimonio[]; capital: number }) {
  const fechas = Array.from(new Set(puntos.map((p) => p.fecha_vela))).sort();
  if (fechas.length < 1) return <p className="vacio">Aún no hay ciclos registrados.</p>;
  const W = 760, H = 260, x0 = 70, y0 = 20, x1 = W - 20, y1 = H - 40;
  const rend = (v: number) => (v / capital - 1) * 100;          // rendimiento acumulado en % (sin mostrar saldos)
  const valores = puntos.map((p) => rend(p.patrimonio)).concat([0]);
  const min = Math.min(...valores), max = Math.max(...valores);
  const rango = max - min || 1;
  const X = (i: number) => (fechas.length === 1 ? (x0 + x1) / 2 : x0 + (i / (fechas.length - 1)) * (x1 - x0));
  const Y = (v: number) => y1 - ((v - min) / rango) * (y1 - y0);
  const series = ["agente_llm", "modelo", "comprar_mantener"].map((e) => ({
    e, p: fechas.map((f) => { const v = puntos.find((q) => q.estrategia === e && q.fecha_vela === f)?.patrimonio; return v == null ? null : rend(v); }),
  }));
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="grafica" role="img" aria-label="Rendimiento acumulado por estrategia">
      <line x1={x0} x2={x1} y1={Y(0)} y2={Y(0)} className="ref" />
      <text x={x0 - 6} y={Y(0) + 4} textAnchor="end" className="eje">0 %</text>
      <text x={x0 - 6} y={y0 + 4} textAnchor="end" className="eje">{pct(max, 1)}</text>
      <text x={x0 - 6} y={y1 + 4} textAnchor="end" className="eje">{pct(min, 1)}</text>
      <text x={x0} y={H - 12} className="eje">{fechas[0]}</text>
      <text x={x1} y={H - 12} textAnchor="end" className="eje">{fechas[fechas.length - 1]}</text>
      {series.map(({ e, p }) => {
        const d = p.map((v, i) => (v == null ? "" : `${i === 0 || p[i - 1] == null ? "M" : "L"}${X(i)},${Y(v)}`)).join(" ");
        return (
          <g key={e}>
            <path d={d} fill="none" stroke={COLORES[e]} strokeWidth={e === "agente_llm" ? 2.5 : 1.6} />
            {p.map((v, i) => v == null ? null : (
              <circle key={i} cx={X(i)} cy={Y(v)} r={2.5} fill={COLORES[e]}>
                <title>{`${NOMBRE_ESTRATEGIA[e]} · ${fechas[i]}: ${pct(v)}`}</title>
              </circle>
            ))}
          </g>
        );
      })}
      <g transform={`translate(${x0 + 10}, ${y0 + 4})`}>
        {series.map(({ e }, i) => (
          <g key={e} transform={`translate(${i * 190}, 0)`}>
            <rect width={12} height={4} y={-4} fill={COLORES[e]} />
            <text x={18} y={0} className="leyenda">{NOMBRE_ESTRATEGIA[e]}</text>
          </g>
        ))}
      </g>
    </svg>
  );
}
