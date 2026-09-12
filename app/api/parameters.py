from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.api.deps import require_user

router = APIRouter(tags=["parameters"], prefix="/front")

_FORM_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Parámetros — MaxMin</title>
  <style>
    :root{ --bg:#f8f9fb; --card:#ffffff; --border:#e5e7eb; --text:#111827; --muted:#6b7280; --accent:#1f2937; --accent-hover:#111827; --ring:#93c5fd; --ok:#065f46; --err:#991b1b; --radius:8px; }
    *{box-sizing:border-box}
    html,body{margin:0;padding:0;background:var(--bg);color:var(--text);font-family: system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;line-height:1.5}
    a{color:inherit}
    .topbar{border-bottom:1px solid var(--border);background:#fff}
    .topbar-inner{max-width:960px;margin:0 auto;padding:14px 20px;display:flex;align-items:center;justify-content:space-between}
    .brand{font-size:15px;letter-spacing:.02em;font-weight:600}
    .brand span{font-weight:400;color:var(--muted)}
    .nav{font-size:13px;color:var(--muted);display:flex;gap:16px}
    .nav a{text-decoration:none;border-bottom:1px solid transparent;padding-bottom:2px}
    .nav a:hover{color:var(--text);border-color:var(--border)}
    .wrap{max-width:640px;margin:32px auto;padding:0 20px}
    .card{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:28px}
    h1{margin:0 0 6px;font-size:22px;font-weight:650;letter-spacing:-.015em}
    .sub{margin:0 0 22px;color:var(--muted);font-size:13.5px;line-height:1.6}
    form{display:grid;gap:16px}
    .field{display:grid;gap:6px}
    .field label{font-size:12.5px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:#374151}
    .field input{width:100%;padding:10px 11px;border:1px solid #d1d5db;border-radius:6px;background:#fff;font-size:14px;color:var(--text);outline:none}
    .field input:focus{border-color:#3b82f6;box-shadow:0 0 0 3px var(--ring)}
    .field .hint{font-size:12px;color:var(--muted)}
    .row{display:grid;grid-template-columns:1fr 1fr;gap:16px}
    @media(max-width:560px){ .row{grid-template-columns:1fr} }
    .actions{margin-top:4px;display:flex;align-items:center;gap:12px}
    button[type=submit]{appearance:none;border:1px solid var(--accent);background:var(--accent);color:#fff;padding:10px 16px;border-radius:6px;font-size:14px;font-weight:500;cursor:pointer}
    button[type=submit]:hover{background:var(--accent-hover)}
    button[type=submit]:disabled{opacity:.6;cursor:not-allowed}
    .status{font-size:13.5px;min-height:1.4em}
    .status.ok{color:var(--ok)} .status.err{color:var(--err)} .status.muted{color:var(--muted)}
    .foot{margin-top:18px;padding-top:16px;border-top:1px solid var(--border);font-size:12px;color:var(--muted);display:flex;justify-content:space-between;gap:12px}
    .mono{font-family: ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;font-size:12px}
  </style>
</head>
<body>
  <header class="topbar">
    <div class="topbar-inner">
      <div class="brand">forgethreads <span>· MaxMin</span></div>
      <nav class="nav">
        <a href="/front/login">Sesión</a>
        <a href="/front/gpu_status">GPU</a>
        <a href="/front/cluster_status">Cluster</a>
      </nav>
    </div>
  </header>
  <main class="wrap">
    <div class="card">
      <h1>Ejecución MaxMin</h1>
      <p class="sub">Define los parámetros del grafo. Se enviarán a <span class="mono">app/execute_maxmin</span> y quedarán encolados en el nodo con mejor disponibilidad.</p>
      <form id="parameters-form" novalidate>
        <div class="row">
          <div class="field">
            <label for="numero_nodos">Número de nodos</label>
            <input id="numero_nodos" type="number" name="numero_nodos" inputmode="numeric" min="1" step="1" required placeholder="ej. 5000" />
            <div class="hint">Entero &gt; 0</div>
          </div>
          <div class="field">
            <label for="seed">Seed</label>
            <input id="seed" type="number" name="seed" inputmode="numeric" step="1" required placeholder="ej. 42" />
            <div class="hint">Reproducibilidad</div>
          </div>
        </div>
        <div class="row">
          <div class="field">
            <label for="threshold">Threshold</label>
            <input id="threshold" type="number" name="threshold" step="any" required placeholder="ej. 0.15" />
            <div class="hint">Float</div>
          </div>
          <div class="field">
            <label for="conectividad_promedio">Conectividad promedio</label>
            <input id="conectividad_promedio" type="number" name="conectividad_promedio" inputmode="numeric" min="0" step="1" required placeholder="ej. 8" />
            <div class="hint">Entero ≥ 0</div>
          </div>
        </div>
        <div class="actions">
          <button type="submit" id="submit-btn">Encolar tarea</button>
          <span class="status muted" id="form-status" role="status" aria-live="polite"></span>
        </div>
      </form>
      <div class="foot">
        <span>POST <span class="mono">/app/execute_maxmin</span> · requiere sesión</span>
        <span class="mono">v1</span>
      </div>
    </div>
  </main>
  <script>
    const form = document.getElementById('parameters-form');
    const statusEl = document.getElementById('form-status');
    const btn = document.getElementById('submit-btn');
    function setStatus(msg, kind){ statusEl.textContent = msg; statusEl.className = 'status ' + (kind||'muted'); }
    form.addEventListener('submit', async (e)=>{
      e.preventDefault();
      setStatus('', 'muted');
      const fd = new FormData(form);
      const payload = {
        numero_nodos: parseInt(fd.get('numero_nodos'),10),
        threshold: parseFloat(fd.get('threshold')),
        conectividad_promedio: parseInt(fd.get('conectividad_promedio'),10),
        seed: parseInt(fd.get('seed'),10)
      };
      // validación cliente mínima
      if(!payload.numero_nodos || payload.numero_nodos<=0){ setStatus('Revisa número de nodos.', 'err'); return; }
      if(!Number.isFinite(payload.threshold)){ setStatus('Revisa threshold.', 'err'); return; }
      btn.disabled = true; btn.textContent = 'Enviando…';
      try{
        const res = await fetch('/app/execute_maxmin', {
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body: JSON.stringify(payload)
        });
        const body = await res.json().catch(()=>({}));
        if(res.ok){
          setStatus(body.message || 'Tarea ingresada correctamente', 'ok');
          form.reset();
        } else if(res.status===401){
          setStatus('Sesión expirada. Ve a /front/login', 'err');
        } else {
          const msg = body.detail ? (Array.isArray(body.detail)? body.detail.map(d=>d.msg).join(' · ') : body.detail) : (body.message || 'Error al encolar');
          setStatus(msg, 'err');
        }
      } catch(err){
        setStatus('Error de red.', 'err');
      } finally {
        btn.disabled = false; btn.textContent = 'Encolar tarea';
      }
    });
  </script>
</body>
</html>
"""


@router.get("/parameters", response_class=HTMLResponse)
def parameters_form(user: Annotated[str, Depends(require_user)]) -> str:
    return _FORM_HTML


@router.get("/execute_maxmin", response_class=HTMLResponse)
def execute_maxmin_form(user: Annotated[str, Depends(require_user)]) -> str:
    """Alias para /parameters, misma UI profesional."""
    return _FORM_HTML

