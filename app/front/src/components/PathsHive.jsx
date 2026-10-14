import { useMemo, useRef, useState } from 'react';
import { fmtValue, maxValue, orderColor, orderLabels, pathLabel, useWidth } from './pathsModel.js';

/**
 * Hive plot de efectos olvidados con un eje por posición en el camino: Origen (From),
 * Intermedio 1, Intermedio 2, … (Through_1, Through_2, …) y Destino (To). Separar los
 * intermedios por posición evita que un solo eje central concentre todos los nodos, y un camino
 * nunca vuelve a un eje: avanza en sentido horario de Origen a Destino. Coloreado por orden.
 * Clic en un nodo del eje Origen: filtra por ese origen (otro clic lo quita).
 */

const INNER = 34;
const SIDE_BEND = 0.38; // rad: desvío si un tramo uniera dos nodos del mismo eje (no debería pasar)

/**
 * Ejes Origen, Intermedio 1..k y Destino repartidos en todo el círculo, en sentido horario y
 * simétricos respecto de la vertical: Origen abajo a la izquierda, Destino abajo a la derecha
 * (con 3 ejes: 240°, 0° y 120°; con 5: cada 72°).
 */
function buildAxes(throughCount) {
  const keys = [
    { key: 'origin', title: 'Origen' },
    ...Array.from({ length: throughCount }, (_, i) => ({
      key: `t${i + 1}`,
      title: throughCount === 1 ? 'Intermedio' : `Intermedio ${i + 1}`,
    })),
    { key: 'dest', title: 'Destino' },
  ];
  const step = 360 / keys.length;
  return keys.map((a, i) => ({
    ...a,
    angle: (((180 + step / 2 + i * step) % 360) * Math.PI) / 180,
  }));
}

function polar(angle, r) {
  return [r * Math.sin(angle), -r * Math.cos(angle)];
}

function midAngle(a1, a2) {
  let d = a2 - a1;
  if (d > Math.PI) d -= 2 * Math.PI;
  if (d < -Math.PI) d += 2 * Math.PI;
  return a1 + d / 2;
}

/** Tramo entre dos puntos (eje, radio): curva cúbica clásica de hive plot. */
function segment(a, b) {
  const [x2, y2] = polar(b.angle, b.r);
  if (a.angle === b.angle) {
    const [c1x, c1y] = polar(a.angle + SIDE_BEND, a.r + 6);
    const [c2x, c2y] = polar(b.angle + SIDE_BEND, b.r + 6);
    return `C${c1x},${c1y} ${c2x},${c2y} ${x2},${y2}`;
  }
  const m = midAngle(a.angle, b.angle);
  const [c1x, c1y] = polar(m, a.r);
  const [c2x, c2y] = polar(m, b.r);
  return `C${c1x},${c1y} ${c2x},${c2y} ${x2},${y2}`;
}

function buildLayout(paths, causes, effects, R) {
  const throughCount = paths.reduce((m, p) => Math.max(m, p.through.length), 0);
  const axes = buildAxes(throughCount);
  const used = Object.fromEntries(axes.map((a) => [a.key, new Set()]));
  for (const p of paths) {
    used.origin.add(p.from);
    p.through.forEach((t, i) => used[`t${i + 1}`].add(t));
    used.dest.add(p.to);
  }
  const nodes = [];
  const pos = {};
  for (const axis of axes) {
    // Un camino puede pasar por cualquier nodo: todos los ejes usan el mismo orden de etiquetas
    // (causas y luego efectos del proyecto).
    const labels = orderLabels(used[axis.key], [...causes, ...effects]);
    pos[axis.key] = new Map();
    labels.forEach((label, i) => {
      const r = INNER + ((i + 0.5) / labels.length) * (R - INNER);
      const kind = causes.includes(label) ? 'cause' : effects.includes(label) ? 'effect' : 'other';
      const node = { id: `${axis.key}:${label}`, axis: axis.key, label, kind, angle: axis.angle, r };
      pos[axis.key].set(label, node);
      nodes.push(node);
    });
  }
  const max = maxValue(paths);
  const lines = paths.map((p) => {
    const points = [pos.origin.get(p.from), ...p.through.map((t, i) => pos[`t${i + 1}`].get(t)), pos.dest.get(p.to)];
    const [x0, y0] = polar(points[0].angle, points[0].r);
    let d = `M${x0},${y0}`;
    for (let i = 1; i < points.length; i += 1) d += segment(points[i - 1], points[i]);
    return { ...p, d, nodeIds: new Set(points.map((n) => n.id)), width: max ? 1 + (p.value / max) * 3 : 1.5 };
  });
  // Los más intensos al final, para que queden encima.
  lines.sort((a, b) => a.value - b.value);
  return { nodes, lines, axes, axisByKey: Object.fromEntries(axes.map((a) => [a.key, a])) };
}

export default function PathsHive({ paths, orders, causes, effects, metric, origin, onOriginChange }) {
  const wrapRef = useRef(null);
  const width = useWidth(wrapRef);
  const [hover, setHover] = useState(null); // { kind: 'line'|'node', id, tip }

  const size = Math.max(320, Math.min(width || 760, 760));
  const R = size / 2 - 90;
  const layout = useMemo(() => (paths.length ? buildLayout(paths, causes, effects, R) : null), [paths, causes, effects, R]);
  // Alto según hasta dónde suben y bajan los ejes (con 3 ejes, los de abajo solo bajan R/2).
  const ys = layout ? layout.axes.map((a) => -Math.cos(a.angle) * R) : [-R, R];
  const top = Math.min(...ys) - 44;
  const height = Math.max(...ys) + 44 - top;

  const lit = (line) => !hover || (hover.kind === 'line' ? hover.id === line.id : line.nodeIds.has(hover.id));

  function show(evt, kind, id, tip) {
    const box = wrapRef.current.getBoundingClientRect();
    const target = evt.currentTarget.getBoundingClientRect();
    const x = evt.clientX ? evt.clientX - box.left : target.left - box.left + target.width / 2;
    const y = evt.clientY ? evt.clientY - box.top : target.top - box.top;
    setHover({ kind, id, tip: { ...tip, x, y } });
  }

  function lineTip(line) {
    const extra = [line.mean != null && metric !== 'Mean' ? `Mean ${fmtValue(line.mean)}` : null,
      line.count != null && metric !== 'Count' ? `Count ${line.count}` : null].filter(Boolean).join(' · ');
    return { value: fmtValue(line.value), title: pathLabel(line), detail: `orden ${line.order}${extra ? ` · ${extra}` : ''}`, lines: [] };
  }

  function nodeTip(n) {
    const own = layout.lines.filter((l) => l.nodeIds.has(n.id));
    return {
      value: `${own.length} camino${own.length === 1 ? '' : 's'}`,
      title: n.label,
      detail: layout.axisByKey[n.axis].title,
      lines: n.axis === 'origin' ? [origin === n.label ? 'Clic para quitar el filtro' : 'Clic para filtrar por este origen'] : [],
    };
  }

  function toggleOrigin(n) {
    if (n.axis === 'origin') onOriginChange(origin === n.label ? '' : n.label);
  }

  if (!layout) return <div className="empty" ref={wrapRef}>Sin caminos para dibujar</div>;

  return (
    <div className="paths-viz" ref={wrapRef}>
      <svg
        width={size}
        height={height}
        viewBox={`${-size / 2} ${top} ${size} ${height}`}
        role="img"
        aria-label={`Hive plot de ${paths.length} caminos: ejes ${layout.axes.map((a) => a.title).join(', ')}`}
        onPointerLeave={() => setHover(null)}
        style={{ display: 'block', margin: '0 auto' }}
      >
        {layout.axes.map((a) => {
          const [x1, y1] = polar(a.angle, INNER - 8);
          const [x2, y2] = polar(a.angle, R + 8);
          const [tx, ty] = polar(a.angle, R + 26);
          return (
            <g key={a.key}>
              <line x1={x1} y1={y1} x2={x2} y2={y2} className="pv-axis" />
              <text x={tx} y={ty} dy="0.35em" textAnchor="middle" className="pv-axis-title">{a.title}</text>
            </g>
          );
        })}
        <g fill="none">
          {layout.lines.map((line) => {
            const on = lit(line);
            return (
              <g key={line.id}>
                <path
                  d={line.d}
                  stroke={orderColor(line.order, orders)}
                  strokeWidth={line.width}
                  strokeOpacity={hover ? (on ? 0.95 : 0.06) : 0.55}
                  strokeLinecap="round"
                />
                <path
                  d={line.d}
                  stroke="transparent"
                  strokeWidth={10}
                  onPointerMove={(e) => show(e, 'line', line.id, lineTip(line))}
                />
              </g>
            );
          })}
        </g>
        {layout.nodes.map((n) => {
          const [x, y] = polar(n.angle, n.r);
          // Etiqueta perpendicular al eje (como peldaños, no se pisan entre nodos vecinos);
          // las que quedarían al revés se voltean para leerse de izquierda a derecha.
          const deg = (n.angle * 180) / Math.PI;
          const flip = deg > 90 && deg < 270;
          const active = n.axis === 'origin' && origin === n.label;
          const dim = hover && hover.kind === 'node' && hover.id !== n.id;
          return (
            <g
              key={n.id}
              className={`pv-node${n.axis === 'origin' ? ' is-clickable' : ''}`}
              tabIndex={0}
              role={n.axis === 'origin' ? 'button' : undefined}
              aria-label={`${layout.axisByKey[n.axis].title}: ${n.label}`}
              opacity={dim ? 0.45 : 1}
              onPointerMove={(e) => show(e, 'node', n.id, nodeTip(n))}
              onFocus={(e) => show(e, 'node', n.id, nodeTip(n))}
              onBlur={() => setHover(null)}
              onClick={() => toggleOrigin(n)}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleOrigin(n); } }}
            >
              <circle cx={x} cy={y} r={11} fill="transparent" />
              <circle cx={x} cy={y} r={active ? 6 : 4.5} className={`pv-dot ${n.kind}${active ? ' is-active' : ''}`} />
              <text
                transform={`translate(${x},${y}) rotate(${flip ? deg + 180 : deg})`}
                x={flip ? -9 : 9}
                dy="0.35em"
                textAnchor={flip ? 'end' : 'start'}
                className={`pv-label${active ? ' is-active' : ''}`}
              >
                {n.label}
              </text>
            </g>
          );
        })}
      </svg>
      {hover && (
        <div className="sankey-tip" style={{ left: Math.min(hover.tip.x + 12, Math.max(0, (width || size) - 260)), top: hover.tip.y + 14 }}>
          <strong>{hover.tip.value}</strong> <span className="muted">{hover.kind === 'node' ? '' : metric}</span>
          <div>{hover.tip.title}</div>
          <div className="muted">{hover.tip.detail}</div>
          {hover.tip.lines.map((l) => <div key={l} className="small">{l}</div>)}
        </div>
      )}
    </div>
  );
}
