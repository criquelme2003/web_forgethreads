import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import Layout from './components/Layout.jsx';
import Login from './pages/Login.jsx';
import Jobs from './pages/Jobs.jsx';
import GpuStatus from './pages/GpuStatus.jsx';
import ClusterStatus from './pages/ClusterStatus.jsx';
import FeExecute from './pages/FeExecute.jsx';
import FeResult from './pages/FeResult.jsx';
import FeCompare from './pages/FeCompare.jsx';
import SweepResult from './pages/SweepResult.jsx';
import MaxMinResult from './pages/MaxMinResult.jsx';
import Projects from './pages/Projects.jsx';
import ProjectDetail from './pages/ProjectDetail.jsx';
import ExpertInvite from './pages/ExpertInvite.jsx';
import Activate from './pages/Activate.jsx';
import Users from './pages/Users.jsx';

export default function App() {
  return (
    <BrowserRouter basename="/front">
      <Routes>
        <Route path="/login" element={<Login />} />
        {/* Pública: el experto entra con el token del enlace, sin sesión. */}
        <Route path="/invite/:token" element={<ExpertInvite />} />
        {/* Pública: el admin nuevo elige su contraseña con el enlace de activación. */}
        <Route path="/activate/:token" element={<Activate />} />
        <Route element={<Layout />}>
          <Route path="/projects" element={<Projects />} />
          <Route path="/projects/:projectId" element={<ProjectDetail />} />
          <Route path="/projects/:projectId/compare" element={<FeCompare />} />
          <Route path="/users" element={<Users />} />
          {/* MaxMin ahora se lanza dentro de un proyecto de simulación. */}
          <Route path="/parameters" element={<Navigate to="/projects" replace />} />
          <Route path="/execute_maxmin" element={<Navigate to="/projects" replace />} />
          <Route path="/jobs" element={<Jobs />} />
          <Route path="/gpu_status" element={<GpuStatus />} />
          <Route path="/cluster_status" element={<ClusterStatus />} />
          <Route path="/fe" element={<FeExecute />} />
          <Route path="/fe/jobs/:jobId" element={<FeResult />} />
          <Route path="/maxmin/jobs/:jobId" element={<MaxMinResult />} />
          <Route path="/sweeps/:jobId" element={<SweepResult />} />
          <Route path="/" element={<Navigate to="/projects" replace />} />
          <Route path="*" element={<Navigate to="/projects" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
