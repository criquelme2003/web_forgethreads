import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { apiJson, send } from '../api.js';
import { errorText } from '../components/MatricesInput.jsx';
import { fmtDate, TIPO_LABELS } from './Projects.jsx';

/** Estado de una publicación para las listas del admin. */
export function PostState({ post }) {
  if (post.estado === 'borrador') return <span className="badge estado-borrador">borrador</span>;
  return (
    <>
      <span className="badge estado-abierto">publicada</span>
      {post.cambios_sin_publicar && <>{' '}<span className="badge outdated" title="El borrador tiene cambios que el público todavía no ve">cambios sin publicar</span></>}
    </>
  );
}

/** Tabla de publicaciones; `showProject` agrega la columna del proyecto. */
export function PostTable({ posts, showProject }) {
  if (!posts.length) return <div className="empty">No hay publicaciones todavía.</div>;
  return (
    <div style={{ overflow: 'auto' }}>
      <table>
        <thead>
          <tr><th>Título</th>{showProject && <th>Proyecto</th>}<th>Estado</th><th>Actualizada</th><th /></tr>
        </thead>
        <tbody>
          {posts.map((p) => (
            <tr key={p.id}>
              <td><Link to={`/posts/${p.id}`}>{p.titulo}</Link></td>
              {showProject && <td className="small">{p.proyecto ? <Link to={`/projects/${p.proyecto.id}`}>{p.proyecto.nombre}</Link> : <span className="muted">—</span>}</td>}
              <td><PostState post={p} /></td>
              <td className="small muted">{fmtDate(p.updated_at)}</td>
              <td className="small">{p.estado === 'publicado' && <a href={`/front/blog/${p.slug}`} target="_blank" rel="noreferrer">ver en el blog ↗</a>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Crea una publicación (con la plantilla del proyecto) y abre el editor. */
export async function createPost(navigate, body) {
  const { ok, data } = await send('/app/posts', 'POST', body).catch(() => ({ ok: false, data: {} }));
  if (ok) navigate(`/posts/${data.id}`);
  return ok ? '' : errorText(data, 'No se pudo crear la publicación');
}

export default function Posts() {
  const navigate = useNavigate();
  const [posts, setPosts] = useState(null);
  const [projects, setProjects] = useState([]);
  const [titulo, setTitulo] = useState('');
  const [projectId, setProjectId] = useState('');
  const [msg, setMsg] = useState('');

  useEffect(() => {
    apiJson('/app/posts').then((d) => setPosts(Array.isArray(d) ? d : [])).catch(() => setPosts([]));
    apiJson('/app/projects').then((d) => setProjects(Array.isArray(d) ? d : [])).catch(() => {});
  }, []);

  async function onSubmit(e) {
    e.preventDefault();
    setMsg(await createPost(navigate, { titulo, project_id: projectId ? Number(projectId) : null }));
  }

  return (
    <div className="stack">
      <div className="card">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
          <h1>Blog</h1>
          <a href="/front/blog" target="_blank" rel="noreferrer" className="small">Ver el blog público ↗</a>
        </div>
        <p className="sub">
          Artículos sobre los proyectos y sus hallazgos, con secciones de texto y figuras de los cálculos en una barra lateral.
          Un proyecto puede tener varias publicaciones. Una figura se publica solo si el texto la cita y le das permiso; nadie ve
          la publicación hasta que la publicas, y los cambios posteriores no se muestran hasta publicar de nuevo.
        </p>
        <form className="controls" onSubmit={onSubmit} style={{ marginBottom: 0 }}>
          <input className="inline-input" placeholder="Título de la publicación" value={titulo} onChange={(e) => setTitulo(e.target.value)} required maxLength={200} />
          <select value={projectId} onChange={(e) => setProjectId(e.target.value)} aria-label="Proyecto">
            <option value="">Sin proyecto</option>
            {projects.map((p) => <option key={p.id} value={p.id}>{p.nombre} ({TIPO_LABELS[p.tipo]})</option>)}
          </select>
          <button type="submit" className="primary">Nueva publicación</button>
        </form>
        {projectId && <p className="small muted" style={{ marginBottom: 0 }}>Se precarga con las secciones de un artículo (Introducción con la descripción del proyecto, Metodología, Resultados…) y su cálculo terminado más reciente como figura.</p>}
        {msg && <p className="status err small">{msg}</p>}
      </div>
      <div className="card">
        <h2 style={{ marginBottom: 10 }}>Publicaciones</h2>
        {posts ? <PostTable posts={posts} showProject /> : <div className="empty">Cargando…</div>}
      </div>
    </div>
  );
}
