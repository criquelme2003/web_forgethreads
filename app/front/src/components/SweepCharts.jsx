import { useMemo, useRef, useState } from 'react';
import { useWidth } from './pathsModel.js';

/**
 * Gráficos del barrido (RF-37), como paneles pequeños con los mismos ejes:
 * - por N: media del orden según c (eje x log), barra de error estándar, forma del marcador
 *   según el régimen de c y líneas de referencia en 1, 5/4, 2 y 3;
 * - por c: media del orden según N (eje x log) con una banda entre el mínimo y el máximo.
 * Una sola serie por panel, en el azul validado de la app; el régimen va en la forma, no en el color.
 */

const HEIGHT = 230;
const MARGIN = { top: 22, right: 14, bottom: 38, left: 44 };
const HIT = 12; // radio de la zona de hover de cada punto (24px)

export const REGIMES = [
  { key: 'r1', label: 'c < 1', shape: 'circle', test: (c) => c < 1 },
  { key: 'r2', label: '1 ≤ c ≤ 5/4', shape: 'square', test: (c) => c >= 1 && c <= 1.25 },
  { key: 'r3', label: '5/4 < c < 2', shape: 'triangle', test: (c) => c > 1.25 && c < 2 },
  { key: 'r4', label: '2 ≤ c ≤ 3', shape: 'diamond', test: (c) => c >= 2 && c <= 3 },
  { key: 'r5', label: 'c > 3', shape: 'triangleDown', test: (c) => c > 3 },
];
export const REFERENCE_C = [
  { value: 1, label: '1' },
  { value: 1.25, label: '5/4' },
  { value: 2, label: '2' },
  { value: 3, label: '3' },
];

export function regimeOf(c) {
  return REGIMES.find((r) => r.test(c)) || REGIMES[0];
}

const num = new Intl.NumberFormat('es-CL', { maximumFractionDigits: 3 });
export function fmt(v) {
  return v == null || Number.isNaN(v) ? '—' : num.format(v);
}

/** Marcador de 8–9px con anillo blanco de 2px. */
export function Marker({ shape, x = 0, y = 0, className = 'sw-mark' }) {
  const r = 4.5;
  switch (shape) {
    case 'square':
      return <rect className={className} x={x - r + 0.5} y={y - r + 0.5} width={2 * r - 1} height={2 * r - 1} rx="1" />;
    case 'triangle':
      return <path className={className} d={`M${x},${y - r - 0.5}L${x + r},${y + r - 1}L${x - r},${y + r - 1}Z`} />;
    case 'triangleDown':
      return <path className={className} d={`M${x},${y + r + 0.5}L${x + r},${y - r + 1}L${x - r},${y - r + 1}Z`} />;
    case 'diamond':
      return <path className={className} d={`M${x},${y - r - 0.5}L${x + r + 0.5},${y}L${x},${y + r + 0.5}L${x - r - 0.5},${y}Z`} />;
    default:
      return <circle className={className} cx={x} cy={y} r={r} />;
  }
}

export function RegimeLegend() {
  return (
    <div className="sw-legend small" aria-label="Régimen de c">
      <span className="muted">Régimen de c</span>
      {REGIMES.map((r) => (
        <span key={r.key}>
          <svg width="14" height="14" aria-hidden="true"><Marker shape={r.shape} x={7} y={7} /></svg>
          {r.label}
        </span>
      ))}
    </div>
  );
}

function niceStep(span, count) {
  const raw = span / count;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const err = raw / pow;
  return (err >= 7.5 ? 10 : err >= 3.5 ? 5 : err >= 1.5 ? 2 : 1) * pow;
}

/** Escala lineal o log con marcas limpias. `domain`: [min, max] de los datos. */
function makeScale([lo, hi], [r0, r1], log) {
  if (log) {
    const a = Math.log10(lo);
    const b = Math.log10(hi);
    const pad = a === b ? 0.5 : (b - a) * 0.06;
    const d0 = a - pad;
    const d1 = b + pad;
    const map = (v) => r0 + ((Math.log10(v) - d0) / (d1 - d0)) * (r1 - r0);
    const ticks = [];
    for (let e = Math.floor(d0); e <= Math.ceil(d1); e += 1) {
      for (const m of [1, 2, 5]) {
        const t = m * 10 ** e;
        if (t >= 10 ** d0 && t <= 10 ** d1) ticks.push(t);
      }
    }
    return { map, ticks: ticks.length > 7 ? ticks.filter((t) => String(t).replace(/0|\./g, '') === '1') : ticks };
  }
  const span = hi - lo || Math.abs(hi) || 1;
  const step = niceStep(span, 4);
  const d0 = Math.floor((lo - span * 0.04) / step) * step;
  const d1 = Math.ceil((hi + span * 0.04) / step) * step;
  const map = (v) => r0 + ((v - d0) / (d1 - d0)) * (r1 - r0);
  const ticks = [];
  for (let t = d0; t <= d1 + step / 2; t += step) ticks.push(Number(t.toPrecision(12)));
  return { map, ticks };
}

/**
 * Un panel. `points`: [{x, y, err?, lo?, hi?, shape?, tip: [[etiqueta, valor], …]}].
 * `xDomain`/`yDomain` vienen de afuera para que todos los paneles compartan ejes.
 */
export function SweepPanel({
  title, points, xDomain, yDomain, xLabel, yLabel, logY, band = false, refLines = [],
}) {
  const wrapRef = useRef(null);
  const width = Math.max(260, useWidth(wrapRef) || 320);
  const [hover, setHover] = useState(null);
  const innerW = width - MARGIN.left - MARGIN.right;
  const innerH = HEIGHT - MARGIN.top - MARGIN.bottom;
  const sx = useMemo(() => makeScale(xDomain, [0, innerW], true), [xDomain, innerW]);
  const sy = useMemo(() => makeScale(yDomain, [innerH, 0], logY), [yDomain, innerH, logY]);
  const sorted = useMemo(() => [...points].sort((a, b) => a.x - b.x), [points]);
  const line = sorted.map((p, i) => `${i ? 'L' : 'M'}${sx.map(p.x)},${sy.map(p.y)}`).join('');
  const area = band && sorted.length
    ? `${sorted.map((p, i) => `${i ? 'L' : 'M'}${sx.map(p.x)},${sy.map(p.hi)}`).join('')}${[...sorted].reverse().map((p) => `L${sx.map(p.x)},${sy.map(p.lo)}`).join('')}Z`
    : null;
  const tipPoint = hover != null ? sorted[hover] : null;

  return (
    <figure className="sw-panel">
      <figcaption className="sw-title">{title}</figcaption>
      <div className="sw-plot" ref={wrapRef}>
      <svg width={width} height={HEIGHT} role="img" aria-label={`${title}: ${yLabel} según ${xLabel}`}>
        <g transform={`translate(${MARGIN.left},${MARGIN.top})`}>
          {sy.ticks.map((t) => (
            <g key={`y${t}`}>
              <line className="ec-grid" x1={0} x2={innerW} y1={sy.map(t)} y2={sy.map(t)} />
              <text className="ec-tick" x={-6} y={sy.map(t)} dy="0.32em" textAnchor="end">{fmt(t)}</text>
            </g>
          ))}
          {refLines.filter((r) => { const x = sx.map(r.value); return x >= 0 && x <= innerW; }).map((r) => (
            <g key={`r${r.value}`}>
              <line className="sw-ref" x1={sx.map(r.value)} x2={sx.map(r.value)} y1={0} y2={innerH} />
              <text className="ec-tick" x={sx.map(r.value)} y={-6} textAnchor="middle">{r.label}</text>
            </g>
          ))}
          <line className="ec-axis" x1={0} x2={innerW} y1={innerH} y2={innerH} />
          {sx.ticks.map((t) => (
            <text key={`x${t}`} className="ec-tick" x={sx.map(t)} y={innerH + 16} textAnchor="middle">{fmt(t)}</text>
          ))}
          <text className="ec-axis-label" x={innerW / 2} y={innerH + 32} textAnchor="middle">{xLabel}</text>
          <text className="ec-axis-label" transform={`translate(${-34},${innerH / 2}) rotate(-90)`} textAnchor="middle">{yLabel}</text>

          {area && <path className="sw-band" d={area} />}
          {sorted.length > 1 && <path className="sw-line" d={line} />}
          {sorted.map((p, i) => {
            const x = sx.map(p.x);
            const y = sy.map(p.y);
            const e = p.err != null && p.err > 0 && (!logY || p.y - p.err > 0)
              ? [sy.map(p.y + p.err), sy.map(p.y - p.err)]
              : null;
            return (
              <g key={i}>
                {e && (
                  <g className="sw-err">
                    <line x1={x} x2={x} y1={e[0]} y2={e[1]} />
                    <line x1={x - 3} x2={x + 3} y1={e[0]} y2={e[0]} />
                    <line x1={x - 3} x2={x + 3} y1={e[1]} y2={e[1]} />
                  </g>
                )}
                <Marker shape={p.shape} x={x} y={y} className={`sw-mark${hover === i ? ' is-hover' : ''}`} />
                <circle
                  className="ec-hit"
                  cx={x}
                  cy={y}
                  r={HIT}
                  tabIndex={0}
                  aria-label={p.tip.map(([k, v]) => `${k} ${v}`).join(', ')}
                  onPointerEnter={() => setHover(i)}
                  onPointerLeave={() => setHover(null)}
                  onFocus={() => setHover(i)}
                  onBlur={() => setHover(null)}
                />
              </g>
            );
          })}
        </g>
      </svg>
      {tipPoint && (
        <div
          className="ec-tip"
          style={{ left: MARGIN.left + sx.map(tipPoint.x), top: MARGIN.top + sy.map(tipPoint.y) - 10 }}
        >
          <strong>{fmt(tipPoint.y)}</strong>
          {tipPoint.tip.map(([k, v]) => <span key={k}>{k}: {v}</span>)}
        </div>
      )}
      </div>
    </figure>
  );
}

/** Extensión [min, max] de una lista de valores positivos (para ejes log). */
export function extent(values, positive = false) {
  const list = values.filter((v) => v != null && Number.isFinite(v) && (!positive || v > 0));
  if (!list.length) return [1, 10];
  return [Math.min(...list), Math.max(...list)];
}
