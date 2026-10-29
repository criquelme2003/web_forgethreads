import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import PostView, { fmtDay, TIPO_PROYECTO } from '../components/PostView.jsx';

/** Blog público (sin sesión): lista de publicaciones y cada publicación (RF-56). */

function BlogShell({ children }) {
  useEffect(() => {
    const prev = document.title;
    document.title = 'Blog · forgethreads';
    return () => {
      document.title = prev;
    };
  }, []);
  return (
    <>
      <header className="topbar">
        <div className="topbar-inner blog-topbar">
          <Link to="/blog" className="brand" style={{ textDecoration: 'none' }}>forgethreads <span>· Blog</span></Link>
          <nav className="nav"><Link to="/projects">Acceso administradores</Link></nav>
        </div>
      </header>
      <main className="wrap-blog">{children}</main>
    </>
  );
}

export function BlogIndex() {
  const [posts, setPosts] = useState(null);
  const [error, setError] = useState('');
  const [project, setProject] = useState('');

  useEffect(() => {
    fetch('/app/blog')
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(`Error ${res.status}`))))
      .then(setPosts)
      .catch(() => setError('No se pudieron cargar las publicaciones.'));
  }, []);

  const projects = useMemo(
    () => [...new Set((posts || []).map((p) => p.proyecto?.nombre).filter(Boolean))].sort(),
    [posts],
  );
  const shown = (posts || []).filter((p) => !project || p.proyecto?.nombre === project);

  return (
    <BlogShell>
      <section className="blog-hero">
        <h1>Publicaciones</h1>
        <p>
          Resultados de los proyectos de forgethreads: consultas a expertos sobre causas y efectos olvidados,
          y simulaciones sobre redes. Cada figura se calculó en el cluster del proyecto y sus datos se pueden descargar cuando los autores lo permiten.
        </p>
      </section>
      {error && <p className="status err">{error}</p>}
      {!posts && !error && <div className="empty">Cargando…</div>}
      {posts && projects.length > 1 && (
        <div className="controls" role="group" aria-label="Filtrar por proyecto">
          <button type="button" className={!project ? 'primary' : undefined} onClick={() => setProject('')}>Todos</button>
          {projects.map((name) => (
            <button key={name} type="button" className={project === name ? 'primary' : undefined} onClick={() => setProject(name)}>{name}</button>
          ))}
        </div>
      )}
      {posts && !posts.length && <div className="card"><div className="empty">Todavía no hay publicaciones.</div></div>}
      <div className="blog-list">
        {shown.map((p) => (
          <Link key={p.slug} to={`/blog/${p.slug}`} className="blog-item">
            {p.proyecto && <div className="post-kicker">{TIPO_PROYECTO[p.proyecto.tipo] || p.proyecto.tipo} · {p.proyecto.nombre}</div>}
            <h2>{p.titulo}</h2>
            {p.autores && <div className="small" style={{ marginBottom: 6 }}>{p.autores}</div>}
            {p.resumen && <p>{p.resumen}</p>}
            <div className="small muted">
              {fmtDay(p.publicado_at)}
              {p.palabras_clave?.length ? ` · ${p.palabras_clave.join(', ')}` : ''}
              {p.graficos > 0 ? ` · ${p.graficos} ${p.graficos === 1 ? 'gráfico' : 'gráficos'}` : ''}
            </div>
          </Link>
        ))}
      </div>
    </BlogShell>
  );
}

export function BlogPost() {
  const { slug } = useParams();
  const [post, setPost] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    setPost(null);
    setError('');
    fetch(`/app/blog/${encodeURIComponent(slug)}`)
      .then(async (res) => {
        if (res.status === 404) throw new Error('Esta publicación no existe o ya no está disponible.');
        if (!res.ok) throw new Error(`Error ${res.status}`);
        return res.json();
      })
      .then((data) => {
        setPost(data);
        document.title = `${data.titulo} · forgethreads`;
      })
      .catch((e) => setError(e.message || 'No se pudo cargar la publicación.'));
  }, [slug]);

  return (
    <BlogShell>
      <div className="small" style={{ marginBottom: 16 }}><Link to="/blog" className="muted">← todas las publicaciones</Link></div>
      {error && <div className="card"><p className="status err">{error}</p></div>}
      {!post && !error && <div className="empty">Cargando…</div>}
      {post && <PostView post={post} />}
    </BlogShell>
  );
}
