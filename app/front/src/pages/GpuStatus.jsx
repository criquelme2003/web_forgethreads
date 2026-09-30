import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '../api.js';

export default function GpuStatus() {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  const fetchStatus = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await apiFetch('/app/gpu_status');
      const body = await res.json().catch(() => ({}));
      if (res.ok) {
        setData(body);
      } else {
        setData(null);
        setError(body.detail || 'No se pudo obtener el estado de GPU.');
      }
    } catch {
      setData(null);
      setError('Error de red.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  return (
    <div className="card">
      <h1>NVIDIA-SMI Result</h1>
      <div className="controls">
        <button className="btn" onClick={fetchStatus}>Actualizar</button>
        <a className="btn" href="/front/cluster_status">ver cluster</a>
      </div>
      {loading && <div className="empty">Cargando…</div>}
      {!loading && error && <div className="status err">{error}</div>}
      {!loading && !error && data && (
        <>
          {data.node_info && (
            <p className="sub"><span className="pill">{data.node_info}</span></p>
          )}
          <pre className="output mono">{data.output}</pre>
          <p className="small muted" style={{ marginTop: 12, fontSize: 12, color: 'var(--muted)' }}>
            Salida de <span className="mono">nvidia-smi</span> vía <span className="mono">NodeSelector</span> `app/services/node_selector.py` · <a href="/front/cluster_status">ver cluster</a>
          </p>
        </>
      )}
    </div>
  );
}
