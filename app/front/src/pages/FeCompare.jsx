import { useEffect, useMemo, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { apiFetch } from '../api.js';
import PathsSankey from '../components/PathsSankey.jsx';
import VizErrorBoundary from '../components/VizErrorBoundary.jsx';

const PAGE_SIZE = 200;
const TOP_OPTIONS = [10, 25, 50, 100];

function expertLabel(c) {
  return c.nombre || c.email || `input ${c.input_id}`;
}

function fmtMean(v) {
  return v == null ? '—' : v.toFixed(4);
}

function csvCell(v) {
  const s = String(v ?? '');
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** Tabla de comparación como CSV: columnas de etiquetas, un Mean por experto y cuántos lo comparten. */
function downloadCsv(order, columnas, paths) {
  const header = [...order.columns, ...columnas.map((c) => `Mean ${expertLabel(c)}`), 'Expertos'];
  const lines = [header, ...paths.map((p) => [...p.path, ...p.means.map((m) => (m == null ? '' : m)), p.shared])]
    .map((row) => row.map(csvCell).join(','));
  const blob = new Blob([`${lines.join('\n')}\n`], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `comparacion_orden_${order.order}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

/** Sankeys de los expertos lado a lado y tabla de caminos con una columna por experto (RF-26). */
export default function FeCompare() {
  const { projectId } = useParams();
  const [search] = useSearchParams();
  const jobIds = useMemo(() => (search.get('jobs') || '').split(',').filter(Boolean), [search]);
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [orderNo, setOrderNo] = useState(null);
  const [origin, setOrigin] = useState('');
  const [top, setTop] = useState(25);
  const [minShared, setMinShared] = useState(1);
  const [limit, setLimit] = useState(PAGE_SIZE);

  useEffect(() => {
    let cancelled = false;
    const qs = jobIds.map((j) => `job_ids=${encodeURIComponent(j)}`).join('&');
    apiFetch(`/app/projects/${encodeURIComponent(projectId)}/compare?${qs}`)
      .then(async (res) => {
        const body = await res.json().catch(() => ({}));
        if (cancelled) return;
        if (!res.ok) {
          setError(typeof body.detail === 'string' ? body.detail : `Error ${res.status}`);
          return;
        }
        setData(body);
        setOrderNo(body.orders.length ? body.orders[0].order : null);
      })
      .catch(() => !cancelled && setError('Error de red al cargar la comparación.'));
    return () => {
      cancelled = true;
    };
  }, [projectId, jobIds]);

  const order = data?.orders.find((o) => o.order === orderNo);
  const origins = useMemo(() => (order ? [...new Set(order.paths.map((p) => p.path[0]))].sort() : []), [order]);
  const filtered = useMemo(
    () => (order ? order.paths.filter((p) => (!origin || p.path[0] === origin) && p.shared >= minShared) : []),
    [order, origin, minShared],
  );
  useEffect(() => setLimit(PAGE_SIZE), [orderNo, origin, minShared]);

  // Filas de cada experto para su Sankey: sus caminos (con el filtro de origen), los `top` de mayor Mean.
  const perExpert = useMemo(() => {
    if (!order || !data) return [];
    return data.columnas.map((_, i) => order.paths
      .filter((p) => p.means[i] != null && (!origin || p.path[0] === origin))
      .sort((a, b) => b.means[i] - a.means[i])
      .slice(0, top || undefined)
      .map((p) => [...p.path, p.means[i]]));
  }, [order, data, origin, top]);

  if (error) {
    return (
      <div className="card">
        <h1>Comparar expertos</h1>
        <p className="status err">{error}</p>
        <Link to={`/projects/${projectId}`} className="small">← proyecto</Link>
      </div>
    );
  }
  if (!data) return <div className="card"><div className="empty">Cargando los caminos de cada experto…</div></div>;

  const { columnas } = data;
  const sankeyColumns = order ? [...order.columns, 'Mean'] : [];

  return (
    <div className="stack">
      <div className="card">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 12 }}>
          <h1>Comparar expertos</h1>
          <Link to={`/projects/${projectId}`} className="small muted">← proyecto</Link>
        </div>
        <table style={{ marginTop: 8 }}>
          <thead><tr><th>Experto</th><th>Cálculo</th><th>THR</th><th>Orden máx.</th><th>Input</th></tr></thead>
          <tbody>
            {columnas.map((c) => (
              <tr key={c.job_id}>
                <td>{expertLabel(c)}{c.nombre && c.email ? <span className="small muted"> {c.email}</span> : null}</td>
                <td className="mono"><Link to={`/fe/jobs/${c.job_id}`}>{c.job_id}</Link></td>
                <td>{c.thr ?? '—'}</td>
                <td>{c.maxorder ?? '—'}</td>
                <td>
                  v{c.input_version}
                  {c.desactualizado && <>{' '}<span className="badge outdated" title="El experto modificó su input después de este cálculo">input modificado</span></>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {data.warnings.map((w) => <p key={w} className="status small" style={{ marginTop: 10 }}>{w}</p>)}
      </div>

      {data.orders.length === 0 ? (
        <div className="card"><div className="empty">Ningún experto tiene caminos.</div></div>
      ) : (
        <>
          <div className="card">
            <div className="controls">
              {data.orders.map((o) => (
                <button key={o.order} type="button" className={o.order === orderNo ? 'primary' : undefined} onClick={() => setOrderNo(o.order)}>
                  Orden {o.order} ({o.paths.length})
                </button>
              ))}
            </div>
            <div className="controls">
              <select value={origin} onChange={(e) => setOrigin(e.target.value)} aria-label="Origen">
                <option value="">Todos los orígenes</option>
                {origins.map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
              <select value={top} onChange={(e) => setTop(Number(e.target.value))} aria-label="Caminos en cada Sankey">
                {TOP_OPTIONS.map((n) => <option key={n} value={n}>Sankey: top {n}</option>)}
                <option value={0}>Sankey: todos</option>
              </select>
              <span className="small muted">clic en un origen para filtrar todos los Sankeys</span>
            </div>
            <div className="compare-grid">
              {columnas.map((c, i) => (
                <div key={c.job_id}>
                  <h2 style={{ fontSize: 14, marginBottom: 4 }}>{expertLabel(c)}</h2>
                  {perExpert[i]?.length ? (
                    <VizErrorBoundary resetKey={`${orderNo}|${origin}|${top}`}>
                      <PathsSankey
                        columns={sankeyColumns}
                        rows={perExpert[i]}
                        metric="Mean"
                        causes={data.causes}
                        origin={origin}
                        onOriginChange={setOrigin}
                        minLayerWidth={130}
                      />
                    </VizErrorBoundary>
                  ) : (
                    <div className="empty">Sin caminos{origin ? ` desde ${origin}` : ''} en este orden.</div>
                  )}
                </div>
              ))}
            </div>
          </div>

          <div className="card">
            <div className="chart-head">
              <h2>Caminos por experto · orden {orderNo}</h2>
              <div className="controls" style={{ marginBottom: 0 }}>
                <select value={minShared} onChange={(e) => setMinShared(Number(e.target.value))} aria-label="Expertos que comparten el camino">
                  {columnas.map((_, i) => (
                    <option key={i} value={i + 1}>{i === 0 ? 'Todos los caminos' : i + 1 === columnas.length ? 'Compartidos por todos' : `Compartidos por ≥ ${i + 1}`}</option>
                  ))}
                </select>
                <button type="button" onClick={() => downloadCsv(order, columnas, filtered)}>Descargar CSV</button>
              </div>
            </div>
            <p className="sub">{filtered.length} caminos. Cada columna es el Mean del experto; «—» si no tiene el camino.</p>
            <div style={{ overflow: 'auto' }}>
              <table>
                <thead>
                  <tr>
                    {order.columns.map((c) => <th key={c}>{c}</th>)}
                    {columnas.map((c) => <th key={c.job_id} className="num">{expertLabel(c)}</th>)}
                    <th className="num">Expertos</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.slice(0, limit).map((p) => (
                    <tr key={p.path.join('→')}>
                      {p.path.map((label, j) => <td key={j}>{label}</td>)}
                      {p.means.map((m, j) => <td key={`m${j}`} className={`num mono${m == null ? ' muted' : ''}`}>{fmtMean(m)}</td>)}
                      <td className="num">{p.shared}/{columnas.length}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {filtered.length > limit && (
              <div className="controls" style={{ marginTop: 10 }}>
                <button type="button" onClick={() => setLimit((l) => l + PAGE_SIZE)}>Mostrar {Math.min(PAGE_SIZE, filtered.length - limit)} más</button>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
