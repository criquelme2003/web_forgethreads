import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { apiFetch } from '../api.js';
import EffectsChart, { formatCount } from '../components/EffectsChart.jsx';

const POLL_MS = 3000;

function fmtDate(iso) {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function Stat({ label, value, hint }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  );
}

export default function MaxMinResult() {
  const { jobId } = useParams();
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [scale, setScale] = useState('linear');

  useEffect(() => {
    let cancelled = false;
    let timer;
    async function poll() {
      try {
        const res = await apiFetch(`/app/maxmin/jobs/${encodeURIComponent(jobId)}`);
        const body = await res.json().catch(() => ({}));
        if (cancelled) return;
        if (!res.ok) {
          setError(typeof body.detail === 'string' ? body.detail : `Error ${res.status}`);
          return;
        }
        setData(body);
        if (body.job.status === 'pending') timer = setTimeout(poll, POLL_MS);
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

  const effects = data?.effects_by_order || [];
  const summary = useMemo(() => {
    if (!effects.length) return null;
    const total = effects.reduce((s, d) => s + d.count, 0);
    const peak = effects.reduce((best, d) => (d.count > best.count ? d : best), effects[0]);
    return { total, peak };
  }, [effects]);

  if (error) {
    return (
      <div className="card">
        <h1>Job {jobId}</h1>
        <p className="status err">{error}</p>
        <Link to="/projects" className="small">← proyectos</Link>
      </div>
    );
  }
  if (!data) return <div className="card"><div className="empty">Cargando…</div></div>;

  const { job } = data;
  const p = job.params || {};
  const badge = ['pending', 'success', 'partial'].includes(job.status) ? job.status : 'error';

  return (
    <div className="stack">
      <div className="card">
        <div className="small">
          {job.project_id != null
            ? <Link to={`/projects/${job.project_id}`} className="muted">← proyecto</Link>
            : <Link to="/jobs" className="muted">← trabajos</Link>}
        </div>
        <h1>MaxMin · job <span className="mono">{job.job_id}</span></h1>
        <div className="kv">
          <span>Estado</span><span><span className={`badge ${badge}`}>{job.status}</span></span>
          <span>N (nodos)</span><span>{p.numero_nodos ?? '—'}</span>
          <span>c (conectividad)</span><span>{p.conectividad_promedio ?? '—'}</span>
          <span>Threshold</span><span>{p.threshold ?? '—'}</span>
          <span>Seed</span><span>{p.seed ?? '—'}</span>
          <span>Nodo</span><span>{job.node}</span>
          <span>Creado</span><span className="small">{fmtDate(job.created_at)}</span>
        </div>
        {job.status === 'pending' && (
          <p className="small muted" style={{ marginTop: 12 }}>
            El cálculo está en cola o corriendo en el cluster. Esta página se actualiza sola cada {POLL_MS / 1000} s.
          </p>
        )}
      </div>

      {job.status !== 'pending' && (
        <div className="stats">
          <Stat label="Orden efectivo" value={job.effective_order ?? '—'} />
          <Stat
            label="Convergencia"
            value={data.convergence_step != null ? `step ${data.convergence_step}` : '—'}
            hint={data.order_limit ? `límite ${data.order_limit}` : undefined}
          />
          <Stat label="Efectos totales" value={summary ? formatCount(summary.total) : '—'} />
          <Stat
            label="Orden con más efectos"
            value={summary ? summary.peak.order : '—'}
            hint={summary ? `${formatCount(summary.peak.count)} efectos` : undefined}
          />
          <Stat
            label="Tiempo de cómputo"
            value={job.computation_time_s != null ? `${job.computation_time_s.toFixed(2)} s` : '—'}
          />
        </div>
      )}

      {effects.length > 0 && (
        <div className="card">
          <div className="chart-head">
            <div>
              <h2>Efectos encontrados por orden</h2>
              <p className="sub" style={{ margin: 0 }}>
                {effects.length} órdenes{data.m ? ` · matriz ${formatCount(data.m)} × ${formatCount(data.m)}` : ''}
              </p>
            </div>
            <div className="controls" role="radiogroup" aria-label="Escala del eje Y" style={{ marginBottom: 0 }}>
              <button type="button" role="radio" aria-checked={scale === 'linear'} className={scale === 'linear' ? 'primary' : undefined} onClick={() => setScale('linear')}>Lineal</button>
              <button type="button" role="radio" aria-checked={scale === 'log'} className={scale === 'log' ? 'primary' : undefined} onClick={() => setScale('log')}>Logarítmica</button>
            </div>
          </div>
          <EffectsChart data={effects} scale={scale} />
          <details className="chart-table">
            <summary className="small">Ver tabla</summary>
            <div style={{ overflow: 'auto', maxHeight: 360 }}>
              <table>
                <thead><tr><th>Orden</th><th>Efectos</th><th>% del total</th></tr></thead>
                <tbody>
                  {effects.map((d) => (
                    <tr key={d.order}>
                      <td>{d.order}</td>
                      <td className="num">{formatCount(d.count)}</td>
                      <td className="num">{((d.count / summary.total) * 100).toFixed(2)} %</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </div>
      )}

      {job.status !== 'pending' && effects.length === 0 && (
        <div className="card"><div className="empty">El log del job no tiene efectos por orden.</div></div>
      )}

      {job.status !== 'pending' && (
        <details className="card" open={job.status !== 'success'}>
          <summary><h2 style={{ display: 'inline' }}>Log del job</h2></summary>
          <pre className="output" style={{ marginTop: 12 }}>{job.logs || '(sin log)'}</pre>
        </details>
      )}
    </div>
  );
}
