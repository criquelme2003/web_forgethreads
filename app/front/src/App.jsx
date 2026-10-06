import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import Layout from './components/Layout.jsx';
import Login from './pages/Login.jsx';
import Parameters from './pages/Parameters.jsx';
import Jobs from './pages/Jobs.jsx';
import GpuStatus from './pages/GpuStatus.jsx';
import ClusterStatus from './pages/ClusterStatus.jsx';
import FeExecute from './pages/FeExecute.jsx';
import FeResult from './pages/FeResult.jsx';

export default function App() {
  return (
    <BrowserRouter basename="/front">
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route element={<Layout />}>
          <Route path="/parameters" element={<Parameters />} />
          <Route path="/execute_maxmin" element={<Parameters />} />
          <Route path="/jobs" element={<Jobs />} />
          <Route path="/gpu_status" element={<GpuStatus />} />
          <Route path="/cluster_status" element={<ClusterStatus />} />
          <Route path="/fe" element={<FeExecute />} />
          <Route path="/fe/jobs/:jobId" element={<FeResult />} />
          <Route path="/" element={<Navigate to="/parameters" replace />} />
          <Route path="*" element={<Navigate to="/parameters" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
