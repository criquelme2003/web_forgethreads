import { NavLink, Outlet, useLocation } from 'react-router-dom';

const LINKS = [
  { to: '/parameters', label: 'Encolar' },
  { to: '/jobs', label: 'Trabajos' },
  { to: '/gpu_status', label: 'GPU' },
  { to: '/cluster_status', label: 'Cluster' },
];

const SECTIONS = {
  '/parameters': 'MaxMin',
  '/execute_maxmin': 'MaxMin',
  '/jobs': 'Trabajos',
  '/gpu_status': 'GPU',
  '/cluster_status': 'Cluster',
};

export default function Layout() {
  const { pathname } = useLocation();
  const section = SECTIONS[pathname] || '';
  return (
    <>
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand">forgethreads <span>· {section}</span></div>
          <nav className="nav">
            {LINKS.map((l) => (
              <NavLink key={l.to} to={l.to} className={({ isActive }) => (isActive ? 'active' : undefined)}>
                {l.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <main className="wrap">
        <Outlet />
      </main>
    </>
  );
}
