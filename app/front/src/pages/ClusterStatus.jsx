import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '../api.js';

export default function ClusterStatus() {
  const [rows, setRows] = useState(null);
  const [checkIdle, setCheckIdle] = useState(false);
  const [threshold, setThreshold] = useState(5);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  const fetchStatus = useCallback(async (withIdle) => {
    setLoading(true);
    setError('');
    try {
      const res = await apiFetch(`/app/cluster_status${withIdle ? '?check_idle=true' : ''}`);
      const body = await res.json().catch(() => ({}));
      if (res.ok) {
        setRows(Array.isArray(body.rows) ? body.rows : []);
        if (body.gpu_idle_threshold != null) setThreshold(body.gpu_idle_threshold);
      } else {
        setRows(null);
        setError(body.detail || 'No se pudo obtener el estado del cluster.');
      }
    } catch {
      setRows(null);
      setError('Error de red.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStatus(checkIdle);
  }, [checkIdle, fetchStatus]);

  return (
    <div className="card">
      <h1 style={{ fontSize: 18 }}>Cluster Status</h1>
      <p className="sub">Disponibilidad SLURM + score GPU-priorizado <span className="mono">app/services/node_selector.py:51</span> · <span className="mono">idle=100 mix=50 alloc=5</span> + <span className="mono">gpu*50 cpu*30</span> · sqlite no aplica aquí, es sondeo SSH directo.</p>
      <div className="controls">
        {checkIdle ? (
          <>
            <span className="pill mono">GPU idle threshold: {threshold}% (util &lt; threshold = idle) · nvidia-smi</span>
            <button className="btn" onClick={() => setCheckIdle(false)}>Ver sin idle</button>
          </>
        ) : (
          <button className="btn primary" onClick={() => setCheckIdle(true)}>Ver con check GPU idle (nvidia-smi)</button>
        )}
        <button className="btn" onClick={() => fetchStatus(checkIdle)}>Actualizar</button>
        <a className="btn" href="/front/gpu_status">Probar mejor nodo</a>
      </div>
      {loading && <div className="empty">Cargando…</div>}
      {!loading && error && <div className="status err">{error}</div>}
      {!loading && !error && rows && (
        <div style={{ overflow: 'auto' }}>
          <table>
            <thead>
              <tr>
                <th>Nodo</th><th>Host</th><th>Estado</th><th>GPUs libres</th><th>CPUs libres</th>
                <th>Jobs pend.</th><th>Score</th><th>Reachable</th>
                {checkIdle && <><th>GPU idle?</th><th>GPU utils</th></>}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.name}>
                  <td className="mono">{row.name}</td>
                  <td className="mono-sm small">{row.host}</td>
                  <td><span className={`badge ${row.state}`}>{row.state}</span></td>
                  <td>{row.gpus_free}/{row.gpus_total}</td>
                  <td>{row.cpus_free}/{row.cpus_total}</td>
                  <td>{row.pending_jobs}</td>
                  <td className="mono">{row.score.toFixed(1)}</td>
                  <td>{row.reachable ? <span className="badge yes">sí</span> : <span className="badge no">no</span>}</td>
                  {checkIdle && <><td>{row.idle_str}</td><td className="mono">{row.utils_str}</td></>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="small muted" style={{ marginTop: 10, fontSize: 11 }}>Filas ordenadas por <span className="mono">score</span> descendente · <span className="mono">has_idle_gpu</span> solo aparece con <span className="mono">?check_idle=true</span> vía <span className="mono">nvidia-smi --query-gpu=utilization.gpu</span> `app/repositories/slurm.py:194`.</p>
    </div>
  );
}
