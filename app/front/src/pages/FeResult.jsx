import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { apiFetch } from '../api.js';
import PathsSankey from '../components/PathsSankey.jsx';

const POLL_MS = 3000;
const PAGE_SIZE = 200;

function fmtNumber(v) {
  return typeof v === 'number' && !Number.isInteger(v) ? v.toFixed(4) : v;
}

const TOP_OPTIONS = [25, 50, 100, 200];

function OrderView({ jobId, table, causes }) {
  const [origin, setOrigin] = useState('');
  const [top, setTop] = useState(50);
  const metrics = ['Mean', 'Count'].filter((m) => table.columns.includes(m));
  const [metric, setMetric] = useState(metrics[0] || 'Mean');
  const [limit, setLimit] = useState(PAGE_SIZE);
  const origins = useMemo(() => [...new Set(table.rows.map((r) => r[0]))].sort(), [table]);
  const metricIdx = table.columns.indexOf(metric);
  const meanIdx = table.columns.indexOf('Mean');
  // Filtros compartidos: el Sankey y la tabla muestran siempre el mismo subconjunto.
  const rows = useMemo(() => {
    const filtered = origin ? table.rows.filter((r) => r[0] === origin) : table.rows;
    return metricIdx >= 0 ? [...filtered].sort((a, b) => b[metricIdx] - a[metricIdx]) : filtered;
  }, [table, origin, metricIdx]);
  const sankeyRows = useMemo(() => (top ? rows.slice(0, top) : rows), [rows, top]);

  return (
    <div>
      <div className="controls">
        <select value={origin} onChange={(e) => { setOrigin(e.target.value); setLimit(PAGE_SIZE); }} aria-label="Origen">
          <option value="">Todos los orígenes</option>
          {origins.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
        <select value={top} onChange={(e) => setTop(Number(e.target.value))} aria-label="Caminos en el Sankey">
          {TOP_OPTIONS.map((n) => <option key={n} value={n}>Sankey: top {n}</option>)}
          <option value={0}>Sankey: todos</option>
        </select>
        {metrics.length > 1 && (
          <select value={metric} onChange={(e) => setMetric(e.target.value)} aria-label="Ancho de los enlaces">
            {metrics.map((m) => <option key={m} value={m}>Ancho: {m}</option>)}
          </select>
        )}
        <span className="small muted">
          {rows.length} caminos{top && rows.length > top ? ` · Sankey con los ${top} de mayor ${metric}` : ''}
        </span>
        <a className="btn" href={`/app/fe/jobs/${jobId}/paths_order_${table.order}.csv`}>Descargar CSV</a>
      </div>
      <PathsSankey columns={table.columns} rows={sankeyRows} metric={metric} causes={causes} />
      <div style={{ overflow: 'auto', marginTop: 16 }}>
        <table>
          <thead>
            <tr>{table.columns.map((c) => <th key={c}>{c}</th>)}</tr>
          </thead>
          <tbody>
            {rows.slice(0, limit).map((r, i) => (
              <tr key={i}>{r.map((v, j) => <td key={j} className={j === meanIdx ? 'mono' : undefined}>{fmtNumber(v)}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length > limit && (
        <div className="controls" style={{ marginTop: 10 }}>
          <button type="button" onClick={() => setLimit((l) => l + PAGE_SIZE)}>Mostrar {Math.min(PAGE_SIZE, rows.length - limit)} más</button>
        </div>
      )}
    </div>
  );
}

export default function FeResult() {
  const { jobId } = useParams();
  const [job, setJob] = useState(null);
  const [jobError, setJobError] = useState('');
  const [paths, setPaths] = useState(null);
  const [pathsError, setPathsError] = useState('');
  const [order, setOrder] = useState(null);

  useEffect(() => {
    let cancelled = false;
    let timer;
    async function poll() {
      try {
        const res = await apiFetch(`/app/jobs/${encodeURIComponent(jobId)}`);
        const body = await res.json().catch(() => ({}));
        if (cancelled) return;
        if (!res.ok) {
          setJobError(body.detail || `Error ${res.status}`);
          return;
        }
        setJob(body);
        if (body.status === 'pending') timer = setTimeout(poll, POLL_MS);
      } catch {
        if (!cancelled) timer = setTimeout(poll, POLL_MS);
      }
    }
    poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [jobId]);

  useEffect(() => {
    if (job?.status !== 'success' || job.kind !== 'fe') return;
    let cancelled = false;
    apiFetch(`/app/fe/jobs/${encodeURIComponent(jobId)}/paths`)
      .then(async (res) => {
        const body = await res.json().catch(() => ({}));
        if (cancelled) return;
        if (!res.ok) {
          setPathsError(body.detail || `Error ${res.status}`);
          return;
        }
        setPaths(body);
        setOrder(body.tables.length ? body.tables[0].order : null);
      })
      .catch(() => !cancelled && setPathsError('Error de red al cargar los caminos.'));
    return () => {
      cancelled = true;
    };
  }, [job, jobId]);

  if (jobError) {
    return (
      <div className="card">
        <h1>Job {jobId}</h1>
        <p className="status err">{jobError}</p>
        <Link to="/fe" className="small">← nuevo cálculo</Link>
      </div>
    );
  }
  if (!job) return <div className="card"><div className="empty">Cargando…</div></div>;

  const params = job.params || {};
  const result = job.result || {};
  const table = paths?.tables.find((t) => t.order === order);

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr)', gap: 20 }}>
      <div className="card">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 12 }}>
          <h1>Caminos · job <span className="mono">{job.job_id}</span></h1>
          <Link to="/fe" className="small muted">nuevo cálculo →</Link>
        </div>
        <div className="kv">
          <span>Estado</span><span><span className={`badge ${job.status}`}>{job.status}</span></span>
          <span>Nodo</span><span>{job.node}</span>
          <span>THR</span><span>{params.thr ?? '—'}</span>
          <span>Orden máximo</span><span>{params.maxorder ?? '—'}</span>
          <span>Causas</span><span className="small">{(params.causes || []).join(', ') || '—'}</span>
          <span>Efectos</span><span className="small">{(params.effects || []).join(', ') || '—'}</span>
          <span>Tiempo</span><span>{job.computation_time_s != null ? `${job.computation_time_s.toFixed(2)} s` : '—'}</span>
        </div>
        {job.status === 'pending' && (
          <p className="small muted" style={{ marginTop: 12 }}>
            El cálculo está en cola o corriendo en el cluster. Esta página se actualiza sola cada {POLL_MS / 1000} s.
          </p>
        )}
      </div>

      {(job.status === 'error' || job.status === 'partial') && (
        <div className="card">
          <h2>El cálculo terminó con {job.status === 'error' ? 'error' : 'resultado parcial'}</h2>
          <p className="sub">Log del job en el nodo (últimos bytes):</p>
          <pre className="output">{job.logs || '(sin log)'}</pre>
        </div>
      )}

      {job.status === 'success' && (
        <div className="card">
          <h2 style={{ marginBottom: 10 }}>Caminos por orden</h2>
          {pathsError && <p className="status err">{pathsError}</p>}
          {!paths && !pathsError && <div className="empty">Descargando resultados del nodo…</div>}
          {paths && paths.tables.length === 0 && (
            <div className="empty">No hay efectos olvidados con THR = {params.thr}.</div>
          )}
          {paths && paths.tables.length > 0 && (
            <>
              <div className="controls">
                {paths.tables.map((t) => (
                  <button key={t.order} type="button" className={t.order === order ? 'primary' : undefined} onClick={() => setOrder(t.order)}>
                    Orden {t.order} ({result.rows_per_order?.[String(t.order)] ?? t.rows.length})
                  </button>
                ))}
              </div>
              {table && <OrderView key={table.order} jobId={job.job_id} table={table} causes={paths.causes} />}
            </>
          )}
        </div>
      )}
    </div>
  );
}
