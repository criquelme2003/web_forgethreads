import { useMemo, useRef, useState } from 'react';
import { fmtValue, orderColor, orderLabels, pathLabel, useWidth } from './pathsModel.js';

/**
 * Chord diagram dirigido de efectos olvidados. Cada etiqueta aparece una vez en el círculo
 * (primero las causas, luego los efectos): un camino puede empezar o terminar en cualquier
 * nodo del grafo, así que un mismo nodo puede ser origen de unos caminos y destino de otros.
 * Una cinta por (origen, destino, orden), coloreada por orden, con ancho proporcional a la suma
 * de la métrica de sus caminos y punta de flecha en el destino. Los intermedios van en el tooltip.
 * Clic en un nodo que sea origen de algún camino: filtra por ese origen (otro clic lo quita).
 */

const ARC_WIDTH = 10;
const LABEL_SPACE = 120;
const GROUP_GAP = 0.14; // rad entre el grupo de causas y el de efectos

function polar(angle, r) {
  return [r * Math.sin(angle), -r * Math.cos(angle)];
}

function arcPath(a0, a1, r0, r1) {
  const large = a1 - a0 > Math.PI ? 1 : 0;
  const [x0, y0] = polar(a0, r1);
  const [x1, y1] = polar(a1, r1);
  const [x2, y2] = polar(a1, r0);
  const [x3, y3] = polar(a0, r0);
  return `M${x0},${y0}A${r1},${r1} 0 ${large} 1 ${x1},${y1}L${x2},${y2}A${r0},${r0} 0 ${large} 0 ${x3},${y3}Z`;
}

/** Cinta del arco de origen [s0,s1] al de destino [t0,t1], que termina en punta de flecha. */
function ribbonPath(s0, s1, t0, t1, r) {
  const p = (a, rr = r) => polar(a, rr).join(',');
  const head = Math.min(10, r * 0.08);
  const tm = (t0 + t1) / 2;
  return `M${p(s0)}A${r},${r} 0 0 1 ${p(s1)}Q0,0 ${p(t0, r - head)}L${p(tm)}L${p(t1, r - head)}Q0,0 ${p(s0)}Z`;
}

function buildLayout(paths, causes, effects) {
  const ribbons = new Map();
  for (const p of paths) {
    const key = `${p.from}\u0000${p.to}\u0000${p.order}`;
    const rb = ribbons.get(key) || { key, from: p.from, to: p.to, order: p.order, value: 0, paths: [] };
    rb.value += p.value;
    rb.paths.push(p);
    ribbons.set(key, rb);
  }
  const list = [...ribbons.values()];
  if (!list.length) return null;

  // Un nodo por etiqueta; cada cinta suma su valor en el nodo de origen y en el de destino.
  const weight = new Map();
  const origins = new Set();
  for (const rb of list) {
    weight.set(rb.from, (weight.get(rb.from) || 0) + rb.value);
    weight.set(rb.to, (weight.get(rb.to) || 0) + rb.value);
    origins.add(rb.from);
  }
  const causeSet = new Set(causes);
  const effectSet = new Set(effects);
  const nodes = orderLabels(new Set(weight.keys()), [...causes, ...effects]).map((label) => ({
    id: label,
    label,
    kind: causeSet.has(label) ? 'cause' : effectSet.has(label) ? 'effect' : 'other',
    isOrigin: origins.has(label),
  }));
  const causeCount = nodes.filter((n) => n.kind === 'cause').length;
  const gaps = causeCount > 0 && causeCount < nodes.length ? 2 : 1; // hueco entre grupos (si hay dos)

  const total = [...weight.values()].reduce((s, v) => s + v, 0);
  const pad = Math.min(0.02, 0.6 / nodes.length);
  const available = 2 * Math.PI - gaps * GROUP_GAP - pad * Math.max(0, nodes.length - gaps);
  const byId = new Map();
  let angle = GROUP_GAP / 2;
  nodes.forEach((n, i) => {
    if (gaps === 2 && i === causeCount) angle += GROUP_GAP - pad; // salto entre causas y efectos
    n.start = angle;
    n.end = angle + (weight.get(n.id) / total) * available;
    n.cursor = n.start;
    angle = n.end + pad;
    byId.set(n.id, n);
  });

  // En cada nodo van primero las cintas que salen y luego las que llegan, por orden y por la
  // posición del otro extremo, para que se crucen menos.
  const index = new Map(nodes.map((n, i) => [n.id, i]));
  const sorted = [...list].sort((a, b) => a.order - b.order
    || index.get(a.to) - index.get(b.to) || index.get(a.from) - index.get(b.from));
  for (const rb of sorted) {
    const src = byId.get(rb.from);
    const span = (rb.value / total) * available;
    rb.s0 = src.cursor;
    rb.s1 = src.cursor + span;
    src.cursor += span;
  }
  for (const rb of [...sorted].sort((a, b) => a.order - b.order || index.get(b.from) - index.get(a.from))) {
    const dst = byId.get(rb.to);
    const span = (rb.value / total) * available;
    rb.t0 = dst.cursor;
    rb.t1 = dst.cursor + span;
    dst.cursor += span;
  }
  return { nodes, ribbons: sorted, causeCount };
}

export default function PathsChord({ paths, orders, causes, effects, metric, origin, onOriginChange }) {
  const wrapRef = useRef(null);
  const width = useWidth(wrapRef);
  const [hover, setHover] = useState(null); // { kind: 'ribbon'|'node', id, tip }

  const layout = useMemo(() => buildLayout(paths, causes, effects), [paths, causes, effects]);
  const size = Math.max(320, Math.min(width || 760, 760));
  const r = Math.max(110, size / 2 - LABEL_SPACE);

  const lit = (rb) => {
    if (!hover) return true;
    if (hover.kind === 'ribbon') return hover.id === rb.key;
    return hover.id === rb.from || hover.id === rb.to;
  };

  function show(evt, kind, id, tip) {
    const box = wrapRef.current.getBoundingClientRect();
    const target = evt.currentTarget.getBoundingClientRect();
    const x = evt.clientX ? evt.clientX - box.left : target.left - box.left + target.width / 2;
    const y = evt.clientY ? evt.clientY - box.top : target.top - box.top;
    setHover({ kind, id, tip: { ...tip, x, y } });
  }

  function ribbonTip(rb) {
    const best = [...rb.paths].sort((a, b) => b.value - a.value).slice(0, 3);
    return {
      value: fmtValue(rb.value),
      title: `${rb.from} → ${rb.to}`,
      detail: `orden ${rb.order} · ${rb.paths.length} camino${rb.paths.length === 1 ? '' : 's'}`,
      lines: best.map(pathLabel),
    };
  }

  function nodeTip(n) {
    const out = layout.ribbons.filter((rb) => rb.from === n.label);
    const inc = layout.ribbons.filter((rb) => rb.to === n.label);
    const count = (list) => list.reduce((s, rb) => s + rb.paths.length, 0);
    const sum = (list) => list.reduce((s, rb) => s + rb.value, 0);
    const kind = { cause: 'causa', effect: 'efecto', other: 'nodo' }[n.kind];
    return {
      value: fmtValue(sum(out) + sum(inc)),
      title: n.label,
      detail: `${kind} · sale en ${count(out)} camino${count(out) === 1 ? '' : 's'} · llega en ${count(inc)}`,
      lines: n.isOrigin ? [origin === n.label ? 'Clic para quitar el filtro' : 'Clic para filtrar por este origen'] : [],
    };
  }

  function toggleOrigin(n) {
    if (n.isOrigin || origin === n.label) onOriginChange(origin === n.label ? '' : n.label);
  }

  if (!layout) return <div className="empty" ref={wrapRef}>Sin caminos para dibujar</div>;

  return (
    <div className="paths-viz" ref={wrapRef}>
      <svg
        width={size}
        height={size}
        viewBox={`${-size / 2} ${-size / 2} ${size} ${size}`}
        role="img"
        aria-label={`Chord diagram dirigido de ${paths.length} caminos entre ${layout.nodes.length} nodos`}
        onPointerLeave={() => setHover(null)}
        style={{ display: 'block', margin: '0 auto' }}
      >
        <g>
          {layout.ribbons.map((rb) => {
            const on = lit(rb);
            return (
              <path
                key={rb.key}
                d={ribbonPath(rb.s0, rb.s1, rb.t0, rb.t1, r - 2)}
                fill={orderColor(rb.order, orders)}
                fillOpacity={hover ? (on ? 0.9 : 0.07) : 0.7}
                className="pv-ribbon"
                onPointerMove={(e) => show(e, 'ribbon', rb.key, ribbonTip(rb))}
              />
            );
          })}
        </g>
        {layout.nodes.map((n) => {
          const mid = (n.start + n.end) / 2;
          const deg = (mid * 180) / Math.PI;
          const left = mid > Math.PI;
          const active = origin === n.label;
          const dim = hover && hover.kind === 'node' && hover.id !== n.id;
          return (
            <g
              key={n.id}
              className={`pv-node${n.isOrigin ? ' is-clickable' : ''}`}
              tabIndex={0}
              role={n.isOrigin ? 'button' : undefined}
              aria-label={`${n.label}${n.isOrigin ? ', filtrar por este origen' : ''}`}
              opacity={dim ? 0.45 : 1}
              onPointerMove={(e) => show(e, 'node', n.id, nodeTip(n))}
              onFocus={(e) => show(e, 'node', n.id, nodeTip(n))}
              onBlur={() => setHover(null)}
              onClick={() => toggleOrigin(n)}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleOrigin(n); } }}
            >
              <path d={arcPath(n.start - 0.004, n.end + 0.004, r - 2, r + ARC_WIDTH + 6)} fill="transparent" />
              <path
                d={arcPath(n.start, Math.max(n.end, n.start + 0.002), r, r + ARC_WIDTH)}
                className={`pv-arc ${n.kind}${active ? ' is-active' : ''}`}
              />
              <text
                transform={`rotate(${deg - 90}) translate(${r + ARC_WIDTH + 6},0)${left ? ' rotate(180)' : ''}`}
                dy="0.35em"
                textAnchor={left ? 'end' : 'start'}
                className={`pv-label${active ? ' is-active' : ''}`}
              >
                {n.label}
              </text>
            </g>
          );
        })}
      </svg>
      {hover && (
        <div className="sankey-tip" style={{ left: Math.min(hover.tip.x + 12, Math.max(0, (width || size) - 240)), top: hover.tip.y + 14 }}>
          <strong>{hover.tip.value}</strong> <span className="muted">{metric}</span>
          <div>{hover.tip.title}</div>
          <div className="muted">{hover.tip.detail}</div>
          {hover.tip.lines.map((l) => <div key={l} className="small">{l}</div>)}
        </div>
      )}
    </div>
  );
}
