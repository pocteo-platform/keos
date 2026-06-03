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
const WorkloadPage = lazy(() => import("@/routes/workload"));
const PipelinePage = lazy(() => import("@/routes/pipeline"));
const IncidentsPage = lazy(() => import("@/routes/incidents"));
const IncidentDetailPage = lazy(() => import("@/routes/incident-detail"));
const UseCasesPage = lazy(() => import("@/routes/usecases"));
const ReliabilityPage = lazy(() => import("@/routes/reliability"));
const OptimizationPage = lazy(() => import("@/routes/optimization"));
const SettingsPage = lazy(() => import("@/routes/settings"));

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
              <Route path="/workload/:ns/:kind/:name" element={<WorkloadPage />} />
              <Route path="/reliability" element={<ReliabilityPage />} />
              <Route path="/incidents" element={<IncidentsPage />} />
              <Route path="/incidents/:id" element={<IncidentDetailPage />} />
              <Route path="/pipeline/:id" element={<PipelinePage />} />
              <Route path="/pipeline/live" element={<PipelinePage />} />
              <Route path="/usecases" element={<UseCasesPage />} />
              <Route path="/optimization" element={<OptimizationPage />} />
              <Route path="/settings" element={<SettingsPage />} />
              {/* Back-compat redirects from the pre-14.6 layout —
                  /control becomes Settings/Agent, /calibration
                  becomes Settings/Calibration. Bookmarks survive. */}
              <Route
                path="/control"
                element={<Navigate to="/settings?tab=agent" replace />}
              />
              <Route
                path="/calibration"
                element={<Navigate to="/settings?tab=calibration" replace />}
              />
              <Route path="*" element={<Navigate to="/dashboard" replace />} />
            </Routes>
          </Suspense>
        </main>
      </div>
    </div>
  );
}
