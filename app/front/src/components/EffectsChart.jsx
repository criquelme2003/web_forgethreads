import { useEffect, useMemo, useRef, useState } from 'react';

/**
 * Efectos encontrados por orden (una sola serie): columnas con escala lineal o logarítmica.
 * Marcas según la guía de visualización: columnas de hasta 24px con 2px de separación,
 * extremo redondeado de 4px y base recta, cuadrícula de 1px, tooltip por columna con foco
 * de teclado, y una etiqueta directa solo en el máximo.
 */

const HEIGHT = 300;
const MARGIN = { top: 26, right: 12, bottom: 40, left: 60 };
const BAR_MAX = 24;
const GAP = 2;
const RADIUS = 4;

const full = new Intl.NumberFormat('es-CL');
const oneDecimal = new Intl.NumberFormat('es-CL', { maximumFractionDigits: 1 });

/** 950 · 20 k · 1,3 M (Intl compacto en es-CL mezcla "1 K" con "800 k"). */
function compact(n) {
  if (n >= 1e6) return `${oneDecimal.format(n / 1e6)} M`;
  if (n >= 1e3) return `${oneDecimal.format(n / 1e3)} k`;
  return full.format(n);
}

export function formatCount(n) {
  return full.format(n);
}

function niceStep(max, count) {
  const raw = max / count;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const err = raw / pow;
  const mult = err >= 7.5 ? 10 : err >= 3.5 ? 5 : err >= 1.5 ? 2 : 1;
  return mult * pow;
}

function linearScale(max, innerH) {
  const step = niceStep(Math.max(max, 1), 4);
  const top = Math.ceil(max / step) * step || 1;
  const ticks = [];
  for (let v = 0; v <= top + step / 2; v += step) ticks.push(v);
  return { y: (v) => innerH - (v / top) * innerH, base: innerH, ticks };
}

function logScale(max, innerH) {
  // Base en 0,5 para que un orden con 1 efecto se vea (log10(1) = 0 tendría altura cero).
  const lo = Math.log10(0.5);
  // Un poco de aire sobre el máximo para su etiqueta; las marcas solo en potencias de 10.
  const hi = Math.log10(Math.max(max, 1)) + 0.3;
  const ticks = [];
  for (let e = 0; e <= hi; e += 1) ticks.push(10 ** e);
  const y = (v) => innerH - ((Math.log10(Math.max(v, 0.5)) - lo) / (hi - lo)) * innerH;
  return { y, base: innerH, ticks };
}

/** Columna con extremo superior redondeado y base recta. */
function barPath(x, y, w, h) {
  const r = Math.min(RADIUS, w / 2, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

export default function EffectsChart({ data, scale }) {
  const wrapRef = useRef(null);
  const [width, setWidth] = useState(800);
  const [hover, setHover] = useState(null); // índice

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return undefined;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.max(320, entry.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const innerW = width - MARGIN.left - MARGIN.right;
  const innerH = HEIGHT - MARGIN.top - MARGIN.bottom;
  const max = useMemo(() => Math.max(...data.map((d) => d.count), 1), [data]);
  const peak = useMemo(() => data.reduce((best, d, i) => (d.count > data[best].count ? i : best), 0), [data]);
  const sy = scale === 'log' ? logScale(max, innerH) : linearScale(max, innerH);
  const slot = innerW / data.length;
  const barW = Math.max(1, Math.min(BAR_MAX, slot - GAP));
  // Etiquetas del eje X separadas al menos 28px; siempre el primer y el último orden.
  const labelEvery = Math.max(1, Math.ceil(28 / slot));
  const showLabel = (i) => i === 0 || i === data.length - 1 || (i % labelEvery === 0 && data.length - 1 - i >= labelEvery / 2);

  const hovered = hover != null ? data[hover] : null;
  const tipX = hover != null ? MARGIN.left + slot * hover + slot / 2 : 0;
  const tipY = hovered ? MARGIN.top + sy.y(hovered.count) : 0;

  return (
    <div className="effects-chart" ref={wrapRef}>
      <svg
        width={width}
        height={HEIGHT}
        role="img"
        aria-label={`Efectos encontrados por orden, ${data.length} órdenes, máximo ${formatCount(data[peak].count)} en el orden ${data[peak].order}`}
      >
        <g transform={`translate(${MARGIN.left},${MARGIN.top})`}>
          {sy.ticks.map((t) => (
            <g key={t} transform={`translate(0,${sy.y(t)})`}>
              <line className="ec-grid" x1={0} x2={innerW} />
              <text className="ec-tick" x={-8} dy="0.32em" textAnchor="end">{compact(t)}</text>
            </g>
          ))}
          {data.map((d, i) => {
            const x = slot * i + (slot - barW) / 2;
            const y = sy.y(d.count);
            return (
              <path
                key={d.order}
                className={`ec-bar${hover === i ? ' is-hover' : ''}`}
                d={barPath(x, y, barW, Math.max(0, sy.base - y))}
              />
            );
          })}
          {hover !== peak && <text
            className="ec-peak"
            x={slot * peak + slot / 2}
            y={sy.y(data[peak].count) - 8}
            textAnchor="middle"
          >
            {compact(data[peak].count)}
          </text>}
          <line className="ec-axis" x1={0} x2={innerW} y1={sy.base} y2={sy.base} />
          {data.map((d, i) => showLabel(i) && (
            <text key={d.order} className="ec-tick" x={slot * i + slot / 2} y={innerH + 16} textAnchor="middle">
              {d.order}
            </text>
          ))}
          <text className="ec-axis-label" x={innerW / 2} y={innerH + 34} textAnchor="middle">Orden</text>
          {/* Zonas de hover/foco: toda la franja de cada orden, más grande que la columna. */}
          {data.map((d, i) => (
            <rect
              key={d.order}
              className="ec-hit"
              x={slot * i}
              y={0}
              width={slot}
              height={innerH}
              tabIndex={0}
              aria-label={`Orden ${d.order}: ${formatCount(d.count)} efectos`}
              onPointerEnter={() => setHover(i)}
              onPointerLeave={() => setHover(null)}
              onFocus={() => setHover(i)}
              onBlur={() => setHover(null)}
            />
          ))}
        </g>
      </svg>
      {hovered && (
        <div
          className="ec-tip"
          style={{
            left: Math.min(Math.max(tipX, 70), width - 70),
            top: Math.max(tipY - 10, 0),
          }}
        >
          <strong>{formatCount(hovered.count)}</strong>
          <span>efectos · orden {hovered.order}</span>
        </div>
      )}
    </div>
  );
}
