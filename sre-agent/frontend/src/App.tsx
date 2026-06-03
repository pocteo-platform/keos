import { lazy, Suspense } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useApp } from "@/lib/store";
import { useWsLifecycle } from "@/lib/ws";
import { Sidebar } from "@/components/layout/Sidebar";
import { TopBar } from "@/components/layout/TopBar";
import { LiveTailBanner } from "@/components/layout/LiveTailBanner";
import LoginPage from "@/routes/login";

const DashboardPage = lazy(() => import("@/routes/dashboard"));
const ClusterPage = lazy(() => import("@/routes/cluster"));
const ControlPage = lazy(() => import("@/routes/control"));
const PipelinePage = lazy(() => import("@/routes/pipeline"));
const IncidentsPage = lazy(() => import("@/routes/incidents"));
const IncidentDetailPage = lazy(() => import("@/routes/incident-detail"));
const UseCasesPage = lazy(() => import("@/routes/usecases"));
const CalibrationPage = lazy(() => import("@/routes/calibration"));

export default function App() {
  const token = useApp((s) => s.token);
  useWsLifecycle();
  const location = useLocation();

  if (!token) {
    if (location.pathname !== "/login") {
      return <Navigate to="/login" replace />;
    }
    return <LoginPage />;
  }

  if (location.pathname === "/login") {
    return <Navigate to="/dashboard" replace />;
  }

  return (
    <div className="flex h-screen w-screen overflow-hidden">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        <LiveTailBanner />
        <main className="flex-1 overflow-auto p-3 sm:p-4 lg:p-6">
          <Suspense fallback={<p className="text-sm text-muted-foreground">…</p>}>
            <Routes>
              <Route path="/" element={<Navigate to="/dashboard" replace />} />
              <Route path="/dashboard" element={<DashboardPage />} />
              <Route path="/cluster" element={<ClusterPage />} />
              <Route path="/control" element={<ControlPage />} />
              <Route path="/incidents" element={<IncidentsPage />} />
              <Route path="/incidents/:id" element={<IncidentDetailPage />} />
              <Route path="/pipeline/:id" element={<PipelinePage />} />
              <Route path="/pipeline/live" element={<PipelinePage />} />
              <Route path="/usecases" element={<UseCasesPage />} />
              <Route path="/calibration" element={<CalibrationPage />} />
              <Route path="*" element={<Navigate to="/dashboard" replace />} />
            </Routes>
          </Suspense>
        </main>
      </div>
    </div>
  );
}
