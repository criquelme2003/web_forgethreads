import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { apiFetch } from '../api.js';
import VizErrorBoundary from '../components/VizErrorBoundary.jsx';
import { ByC, ByN, fmt, RegimeLegend } from '../components/SweepCharts.jsx';

const POLL_MS = 5000;

function fmtDuration(s) {
  if (s == null) return '—';
  if (s < 90) return `${s.toFixed(1)} s`;
  if (s < 5400) return `${(s / 60).toFixed(1)} min`;
  return `${(s / 3600).toFixed(1)} h`;
}

export default function SweepResult() {
  const { jobId } = useParams();
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [view, setView] = useState('n');
  const [logY, setLogY] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let timer;
    async function poll() {
      try {
        const res = await apiFetch(`/app/sweeps/${encodeURIComponent(jobId)}`);
        const body = await res.json().catch(() => ({}));
        if (cancelled) return;
        if (!res.ok) {
          setError(body.detail || `Error ${res.status}`);
          return;
        }
        setError('');
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

  if (error && !data) {
    return (
      <div className="card">
        <h1>Barrido {jobId}</h1>
        <p className="status err">{error}</p>
        <Link to="/projects" className="small">← proyectos</Link>
      </div>
    );
  }
  if (!data) return <div className="card"><div className="empty">Cargando…</div></div>;

  const { job, estadisticas: stats } = data;
  const p = job.params || {};
  const result = job.result || {};
  const done = job.status === 'success' || job.status === 'partial';

  return (
    <div className="stack">
      <div className="card">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 12 }}>
          <h1>Barrido · job <span className="mono">{job.job_id}</span></h1>
          {job.project_id != null && <Link to={`/projects/${job.project_id}/lote`} className="small muted">← barridos</Link>}
        </div>
        <div className="kv">
          <span>Estado</span><span><span className={`badge ${job.status}`}>{job.status === 'partial' ? 'parcial' : job.status}</span></span>
          <span>Nodo</span><span>{job.node}</span>
          <span>N</span><span className="small">{(p.ns || []).join(', ')}</span>
          <span>c</span><span className="small">{(p.cs || []).map(fmt).join(', ')}</span>
          <span>Repeticiones</span><span>{p.reps}</span>
          <span>THR</span><span>{p.thr}</span>
          <span>Semilla base</span><span>{p.seed_base}</span>
          <span>Combinaciones</span>
          <span>
            {result.completed_combinations != null ? `${result.completed_combinations} de ${result.total_combinations ?? p.combinaciones} terminadas` : `${p.combinaciones} válidas`}
            {' '}· {p.ejecuciones} ejecuciones
          </span>
          <span>Tiempo</span>
          <span>{job.computation_time_s != null ? fmtDuration(job.computation_time_s) : `estimado ${fmtDuration(p.estimado_s)}`} <span className="small muted">(límite SLURM {p.time_limit})</span></span>
        </div>
        {job.status === 'pending' && (
          <p className="small muted" style={{ marginTop: 12 }}>
            El barrido está en cola o corriendo en el cluster. Esta página se actualiza sola cada {POLL_MS / 1000} s.
          </p>
        )}
        {job.status === 'partial' && (
          <p className="status small" style={{ marginTop: 12 }}>
            El barrido se cortó antes de terminar (probablemente por tiempo). Se muestran las combinaciones que alcanzaron a escribirse.
          </p>
        )}
        {error && <p className="status err small">{error}</p>}
      </div>

      {job.status === 'error' && (
        <div className="card">
          <h2>El barrido terminó con error</h2>
          <p className="sub">Log del job en el nodo (últimos bytes):</p>
          <pre className="output">{job.logs || '(sin log)'}</pre>
        </div>
      )}

      {done && (
        <div className="card">
          <div className="chart-head">
            <h2>Orden efectivo</h2>
            <div className="controls" style={{ marginBottom: 0 }}>
              <button type="button" className={view === 'n' ? 'primary' : undefined} aria-pressed={view === 'n'} onClick={() => setView('n')}>Por N</button>
              <button type="button" className={view === 'c' ? 'primary' : undefined} aria-pressed={view === 'c'} onClick={() => setView('c')}>Por c</button>
              <label className="checkbox-label"><input type="checkbox" checked={logY} onChange={(e) => setLogY(e.target.checked)} /> Eje vertical log</label>
              <a className="btn" href={`/app/sweeps/${job.job_id}/crudo.csv`}>CSV crudo</a>
              <a className="btn" href={`/app/sweeps/${job.job_id}/agregado.csv`}>CSV agregado</a>
            </div>
          </div>
          {stats.length === 0 && <div className="empty">El CSV no tiene repeticiones terminadas.</div>}
          {stats.length > 0 && view === 'n' && (
            <>
              <p className="sub">Media del orden según c en cada N, con el error estándar de la media. Líneas de referencia en c = 1, 5/4, 2 y 3.</p>
              <RegimeLegend />
              <VizErrorBoundary resetKey={`n|${logY}`}><ByN stats={stats} logY={logY} /></VizErrorBoundary>
            </>
          )}
          {stats.length > 0 && view === 'c' && (
            <>
              <p className="sub">Media del orden según N en cada c; la banda va del mínimo al máximo de las repeticiones.</p>
              <VizErrorBoundary resetKey={`c|${logY}`}><ByC stats={stats} logY={logY} /></VizErrorBoundary>
            </>
          )}
        </div>
      )}

      {done && stats.length > 0 && (
        <details className="card">
          <summary><h2 style={{ display: 'inline' }}>Tabla por (N, c)</h2></summary>
          <div style={{ overflow: 'auto', marginTop: 12 }}>
            <table>
              <thead><tr><th>N</th><th>c</th><th>Media</th><th>Desv. est.</th><th>Error est.</th><th>Mín</th><th>Máx</th><th>Rep.</th></tr></thead>
              <tbody>
                {stats.map((s) => (
                  <tr key={`${s.n}|${s.c}`}>
                    <td className="num">{s.n}</td><td className="num">{fmt(s.c)}</td><td className="num">{fmt(s.media)}</td>
                    <td className="num">{fmt(s.desviacion_estandar)}</td><td className="num">{fmt(s.error_estandar)}</td>
                    <td className="num">{s.minimo}</td><td className="num">{s.maximo}</td>
                    <td className="num">{s.repeticiones}{p.reps && s.repeticiones < p.reps ? <span className="muted"> / {p.reps}</span> : ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}

      {(data.fallas.length > 0 || data.sin_datos.length > 0 || data.omitidas.length > 0) && (
        <div className="card">
          <h2 style={{ marginBottom: 10 }}>Fuera de las estadísticas</h2>
          {data.fallas.length > 0 && (
            <>
              <p className="sub">Repeticiones que fallaron en el nodo ({data.fallas.length}):</p>
              <div style={{ overflow: 'auto', maxHeight: 260 }}>
                <table>
                  <thead><tr><th>N</th><th>c</th><th>Rep.</th><th>Error</th></tr></thead>
                  <tbody>
                    {data.fallas.map((f, i) => (
                      <tr key={i}><td>{f.n ?? '—'}</td><td>{fmt(f.c)}</td><td>{f.rep ?? '—'}</td><td className="small mono">{f.error}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
          {data.sin_datos.length > 0 && (
            <p className="small" style={{ marginTop: 12 }}>
              Sin datos (no alcanzaron a correr): {data.sin_datos.map((x) => `N=${x.n}, c=${fmt(x.c)}`).join(' · ')}
            </p>
          )}
          {data.omitidas.length > 0 && (
            <p className="small muted" style={{ marginTop: 12 }}>
              Omitidas por no cumplir N par y c ≤ N/2 − 1: {data.omitidas.map((x) => `N=${x.n}, c=${fmt(x.c)} (${x.reason})`).join(' · ')}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
