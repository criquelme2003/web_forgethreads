import { useEffect, useMemo, useRef, useState } from 'react';
import { sankey, sankeyLinkHorizontal } from 'd3-sankey';

const NODE_WIDTH = 10;
const NODE_PADDING = 10;
const HEADER = 22; // espacio para los títulos de columna (From, Through1…, To)
const ROW_HEIGHT = 26;
const MIN_LAYER_WIDTH = 150; // por debajo, el Sankey hace scroll horizontal en vez de apretarse

function fmt(v) {
  return Number.isInteger(v) ? String(v) : v.toFixed(4);
}

/**
 * Arma el grafo del Sankey. Cada nodo es `etiqueta@capa` (capa 0 = From, 1 = Through1, …):
 * d3-sankey no admite ciclos y un camino puede repetir etiquetas, así que el nodo es la posición.
 * Los enlaces que comparten (origen, destino) se suman y recuerdan qué caminos los recorren.
 */
function buildGraph(columns, rows, metric, causes) {
  const labelCols = columns
    .map((c, i) => ({ c, i }))
    .filter(({ c }) => c === 'From' || c === 'To' || c.startsWith('Through'));
  const valueIdx = columns.indexOf(metric);
  const causeSet = new Set(causes);
  const nodes = new Map();
  const links = new Map();

  rows.forEach((row, pathId) => {
    const value = Number(row[valueIdx]);
    if (!(value > 0)) return;
    const ids = labelCols.map(({ i }, layer) => {
      const label = String(row[i]);
      const id = `${label}@${layer}`;
      if (!nodes.has(id)) nodes.set(id, { id, label, layer, kind: causeSet.has(label) ? 'cause' : 'effect' });
      return id;
    });
    for (let k = 0; k < ids.length - 1; k += 1) {
      const key = `${ids[k]}→${ids[k + 1]}`;
      const link = links.get(key) || { source: ids[k], target: ids[k + 1], value: 0, paths: new Set() };
      link.value += value;
      link.paths.add(pathId);
      links.set(key, link);
    }
  });

  return {
    layers: labelCols.map(({ c }) => c),
    nodes: [...nodes.values()],
    links: [...links.values()],
  };
}

function useWidth(ref) {
  const [width, setWidth] = useState(0);
  useEffect(() => {
    if (!ref.current) return undefined;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, [ref]);
  return width;
}

export default function PathsSankey({ columns, rows, metric, causes }) {
  const wrapRef = useRef(null);
  const width = useWidth(wrapRef);
  const [hover, setHover] = useState(null); // { paths:Set, tip:{x,y,title,value,detail} }

  const graph = useMemo(() => buildGraph(columns, rows, metric, causes), [columns, rows, metric, causes]);

  const layout = useMemo(() => {
    if (!width || graph.links.length === 0) return null;
    const chartWidth = Math.max(width, graph.layers.length * MIN_LAYER_WIDTH);
    const perLayer = new Map();
    graph.nodes.forEach((n) => perLayer.set(n.layer, (perLayer.get(n.layer) || 0) + 1));
    const height = Math.min(1400, Math.max(260, Math.max(...perLayer.values()) * ROW_HEIGHT + HEADER + 10));
    const generator = sankey()
      .nodeId((d) => d.id)
      .nodeAlign((node) => node.layer)
      .nodeWidth(NODE_WIDTH)
      .nodePadding(NODE_PADDING)
      .extent([[1, HEADER], [chartWidth - 1, height - 6]]);
    const { nodes, links } = generator({
      nodes: graph.nodes.map((d) => ({ ...d })),
      links: graph.links.map((d) => ({ ...d })),
    });
    nodes.forEach((n) => {
      n.paths = new Set();
      [...n.sourceLinks, ...n.targetLinks].forEach((l) => l.paths.forEach((p) => n.paths.add(p)));
    });
    const layerX = graph.layers.map((_, layer) => {
      const n = nodes.find((d) => d.layer === layer);
      return n ? n.x0 : 0;
    });
    return { nodes, links, height, layerX, chartWidth };
  }, [graph, width]);

  const lastLayer = graph.layers.length - 1;
  const isLit = (paths) => !hover || [...paths].some((p) => hover.paths.has(p));

  function show(evt, paths, tip) {
    const box = wrapRef.current.getBoundingClientRect();
    const target = evt.currentTarget.getBoundingClientRect();
    const x = evt.clientX ? evt.clientX - box.left : target.left - box.left + target.width / 2;
    const y = evt.clientY ? evt.clientY - box.top : target.top - box.top;
    setHover({ paths, tip: { ...tip, x, y } });
  }

  function nodeTip(n) {
    return {
      title: n.label,
      value: `${fmt(n.value)}`,
      detail: `${graph.layers[n.layer]} · ${n.paths.size} camino${n.paths.size === 1 ? '' : 's'}`,
    };
  }

  return (
    <div className="sankey" ref={wrapRef}>
      <div className="sankey-legend small">
        <span><svg width="10" height="10" aria-hidden="true"><rect width="10" height="10" rx="2" fill="var(--viz-cause)" /></svg> Causa</span>
        <span><svg width="10" height="10" aria-hidden="true"><rect width="10" height="10" rx="2" fill="var(--viz-effect)" /></svg> Efecto</span>
        <span className="muted">Ancho del enlace = suma de {metric} de los caminos que lo recorren</span>
      </div>
      {!layout && <div className="empty">{graph.links.length === 0 ? 'Sin caminos para dibujar' : ''}</div>}
      {layout && (
        <div className="sankey-scroll">
        <svg
          width={layout.chartWidth}
          height={layout.height}
          role="img"
          aria-label={`Sankey de ${rows.length} caminos`}
          onPointerLeave={() => setHover(null)}
        >
          {graph.layers.map((name, layer) => (
            <text
              key={name}
              x={layer === lastLayer ? layout.layerX[layer] + NODE_WIDTH : layout.layerX[layer]}
              y={12}
              textAnchor={layer === lastLayer ? 'end' : 'start'}
              className="sankey-col"
            >
              {name}
            </text>
          ))}
          <g fill="none">
            {layout.links.map((l, i) => {
              const lit = isLit(l.paths);
              const d = sankeyLinkHorizontal()(l);
              const tip = {
                title: `${l.source.label} → ${l.target.label}`,
                value: fmt(l.value),
                detail: `${graph.layers[l.source.layer]} → ${graph.layers[l.target.layer]} · ${l.paths.size} camino${l.paths.size === 1 ? '' : 's'}`,
              };
              return (
                <g key={i}>
                  <path
                    d={d}
                    stroke={hover && lit ? `var(--viz-${l.source.kind})` : 'var(--viz-link)'}
                    strokeOpacity={hover ? (lit ? 0.55 : 0.08) : 0.35}
                    strokeWidth={Math.max(1, l.width)}
                  />
                  <path
                    d={d}
                    stroke="transparent"
                    strokeWidth={Math.max(10, l.width)}
                    onPointerMove={(e) => show(e, l.paths, tip)}
                  />
                </g>
              );
            })}
          </g>
          {layout.nodes.map((n) => {
            const lit = isLit(n.paths);
            const right = n.layer === lastLayer;
            return (
              <g
                key={n.id}
                tabIndex={0}
                className="sankey-node"
                opacity={lit ? 1 : 0.35}
                onPointerMove={(e) => show(e, n.paths, nodeTip(n))}
                onFocus={(e) => show(e, n.paths, nodeTip(n))}
                onBlur={() => setHover(null)}
              >
                <rect
                  x={n.x0 - 4}
                  y={n.y0 - 2}
                  width={n.x1 - n.x0 + 8}
                  height={Math.max(4, n.y1 - n.y0 + 4)}
                  fill="transparent"
                />
                <rect
                  x={n.x0}
                  y={n.y0}
                  width={n.x1 - n.x0}
                  height={Math.max(2, n.y1 - n.y0)}
                  rx={2}
                  fill={`var(--viz-${n.kind})`}
                />
                <text
                  x={right ? n.x0 - 6 : n.x1 + 6}
                  y={(n.y0 + n.y1) / 2}
                  dy="0.35em"
                  textAnchor={right ? 'end' : 'start'}
                  className="sankey-label"
                >
                  {n.label}
                </text>
              </g>
            );
          })}
        </svg>
        </div>
      )}
      {hover && (
        <div
          className="sankey-tip"
          style={{
            left: Math.min(hover.tip.x + 12, Math.max(0, width - 220)),
            top: hover.tip.y + 14,
          }}
        >
          <strong>{hover.tip.value}</strong> <span className="muted">{metric}</span>
          <div>{hover.tip.title}</div>
          <div className="muted">{hover.tip.detail}</div>
        </div>
      )}
    </div>
  );
}
