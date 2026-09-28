import { useCallback, useEffect, useRef, useState } from 'react';
import { apiFetch, apiJson } from '../api.js';

function fmt(iso) {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function badgeClass(s) {
  return s === 'pending' ? 'pending' : s === 'success' ? 'success' : 'error';
}

export default function Jobs() {
  const [jobs, setJobs] = useState(null);
  const [filter, setFilter] = useState('pending');
  const [auto, setAuto] = useState(true);
  const [detail, setDetail] = useState(null);
  const [dbPath, setDbPath] = useState('data/jobs.db');
  const autoRef = useRef(true);
  autoRef.current = auto;

  const fetchJobs = useCallback(async (statusFilter) => {
    try {
      const qs = statusFilter ? `?status=${encodeURIComponent(statusFilter)}` : '';
      const res = await apiFetch(`/app/jobs${qs}`);
      const data = await res.json().catch(() => []);
      setJobs(Array.isArray(data) ? data : []);
    } catch {
      // apiFetch ya redirige ante 401; errores de red se ignoran hasta el próximo ciclo
    }
  }, []);

  useEffect(() => {
    apiJson('/app/ui-meta')
      .then((m) => {
        if (m.jobs_db_path) setDbPath(m.jobs_db_path);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    fetchJobs(filter);
    const iv = setInterval(() => {
      if (autoRef.current) fetchJobs(filter);
    }, 2000);
    return () => clearInterval(iv);
  }, [filter, fetchJobs]);

  async function showDetail(id) {
    try {
      const d = await apiJson(`/app/jobs/${id}`);
      setDetail({ id, json: JSON.stringify(d, null, 2) });
      document.getElementById('detail').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } catch {}
  }

  const emptyLabel = filter ? `(${filter})` : '';

  return (
    <div className="card">
      <h1>Trabajos encolados</h1>
      <p className="sub">Persistidos en <span className="mono">sqlite {dbPath}</span> via <span className="mono">JobStore</span> `app/services/job_store.py` · polling cada 2s a <span className="mono">GET /app/jobs</span>.</p>
      <div className="controls">
        <select id="filter-status" value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="pending">Pendientes</option>
          <option value="">Todos</option>
          <option value="success">Completados</option>
          <option value="error">Error</option>
        </select>
        <button id="refresh" onClick={() => fetchJobs(filter)}>Actualizar</button>
        <label className="checkbox-label">
          <input type="checkbox" id="auto" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> auto (2s)
        </label>
        <span id="count" className="small muted">{jobs === null ? '' : `${jobs.length} trabajos`}</span>
      </div>
      <div style={{ overflow: 'auto' }}>
        <table>
          <thead>
            <tr><th>Job ID</th><th>Estado</th><th>Nodo</th><th>Creado</th><th>Orden</th><th>Tiempo(s)</th></tr>
          </thead>
          <tbody id="tbody">
            {jobs === null && (
              <tr><td colSpan="6" className="empty">Cargando…</td></tr>
            )}
            {jobs !== null && jobs.length === 0 && (
              <tr><td colSpan="6" className="empty">Sin trabajos {emptyLabel}</td></tr>
            )}
            {jobs !== null && jobs.map((j) => (
              <tr key={j.job_id} style={{ cursor: 'pointer' }} onClick={() => showDetail(j.job_id)}>
                <td className="mono">{j.job_id}</td>
                <td><span className={`badge ${badgeClass(j.status)}`}>{j.status}</span></td>
                <td>{j.node}</td>
                <td className="small">{fmt(j.created_at)}</td>
                <td>{j.effective_order ?? '—'}</td>
                <td>{j.computation_time_s != null ? j.computation_time_s.toFixed(2) : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {detail && (
        <div id="detail" style={{ marginTop: 16, border: '1px solid var(--border)', borderRadius: 8, padding: 14, background: '#f9fafb' }}>
          <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: '.05em', textTransform: 'uppercase', color: '#374151', marginBottom: 8 }}>
            Detalle <span className="mono">{detail.id}</span>
          </div>
          <pre className="mono" style={{ background: '#fff', border: '1px solid var(--border)', padding: 10, borderRadius: 6, overflow: 'auto', fontSize: 12, margin: 0, whiteSpace: 'pre-wrap' }}>{detail.json}</pre>
        </div>
      )}
      {!detail && <div id="detail" hidden />}
    </div>
  );
}
