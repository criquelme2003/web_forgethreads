import { useEffect, useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { fetchMe, logout } from '../api.js';

const LINKS = [
  { to: '/projects', label: 'Proyectos' },
  { to: '/fe', label: 'Caminos' },
  { to: '/jobs', label: 'Trabajos' },
  { to: '/posts', label: 'Blog' },
  { to: '/gpu_status', label: 'GPU' },
  { to: '/cluster_status', label: 'Cluster' },
];
const ADMIN_LINKS = [{ to: '/users', label: 'Usuarios' }];

const SECTIONS = {
  '/projects': 'Proyectos',
  '/jobs': 'Trabajos',
  '/gpu_status': 'GPU',
  '/cluster_status': 'Cluster',
  '/fe': 'Caminos',
  '/users': 'Usuarios',
  '/posts': 'Blog',
};

export default function Layout() {
  const { pathname } = useLocation();
  const [me, setMe] = useState(null);
  useEffect(() => {
    fetchMe().then(setMe).catch(() => {});
  }, []);
  const section = SECTIONS[pathname]
    || (pathname.startsWith('/fe/') ? 'Caminos' : '')
    || (pathname.startsWith('/projects/') ? 'Proyectos' : '')
    || (pathname.startsWith('/maxmin/') ? 'MaxMin' : '')
    || (pathname.startsWith('/posts/') ? 'Blog' : '');
  const links = me?.rol === 'superadmin' ? [...LINKS, ...ADMIN_LINKS] : LINKS;
  return (
    <>
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand">forgethreads <span>· {section}</span></div>
          <nav className="nav">
            {links.map((l) => (
              <NavLink key={l.to} to={l.to} className={({ isActive }) => (isActive ? 'active' : undefined)}>
                {l.label}
              </NavLink>
            ))}
          </nav>
          {me && (
            <div className="session">
              <span className="small" title={me.email}>
                {me.nombre || me.email}
                {me.rol === 'superadmin' && <span className="muted"> · superadmin</span>}
              </span>
              <button type="button" className="link-btn small" onClick={logout}>Salir</button>
            </div>
          )}
        </div>
      </header>
      <main className="wrap">
        <Outlet />
      </main>
    </>
  );
}
