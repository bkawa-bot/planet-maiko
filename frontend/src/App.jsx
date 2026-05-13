import { lazy, Suspense } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import Layout from "./components/Layout";
import ToastContainer from "./components/Toast";
import AskMaiko from "./components/AskMaiko";
import ArrivalWatcher from "./components/ArrivalWatcher";
import PersistentPack from "./components/PersistentPack";
// Eager: the four surfaces a user hits on almost every visit. Keeping
// them in the main bundle avoids a suspense flicker on the critical
// path.
import Home from "./pages/Home";
import Tasks from "./pages/Tasks";
import Agents from "./pages/Agents";
// Lazy: the heavier / less-frequently-visited surfaces. Settings,
// Training, Review pages etc. ship as their own chunks so the first
// paint doesn't drag them in. Watchers (the lint bundle warning above
// 500KB) were complaining about exactly this.
const Settings = lazy(() => import("./pages/Settings"));
const BrainView = lazy(() => import("./pages/BrainView"));
const Automations = lazy(() => import("./pages/Automations"));
const Themes = lazy(() => import("./pages/Themes"));
// Unified agent-job surface — replaces JobReport / ReviewDiff / ReviewPlan
// as separate pages. The old paths redirect into this with ?view=...
const AgentJobPage = lazy(() => import("./pages/AgentJobPage"));
const TaskRouteRedirect = lazy(() => import("./pages/TaskRouteRedirect"));
import useKeyboardShortcuts from "./hooks/useKeyboardShortcuts";


function RouteFallback() {
  return (
    <div style={{
      padding: 40, textAlign: "center",
      color: "var(--text-muted)", fontSize: 12,
    }}>
      …
    </div>
  );
}


function AppRoutes() {
  useKeyboardShortcuts();

  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Home />} />
        <Route path="/inbox" element={<Navigate to="/" replace />} />
        <Route path="/tasks" element={<Tasks />} />
        <Route path="/agents" element={<Agents />} />
        <Route path="/knowledge" element={<Suspense fallback={<RouteFallback />}><BrainView /></Suspense>} />
        <Route path="/automations" element={<Suspense fallback={<RouteFallback />}><Automations /></Suspense>} />
        {/* /training redirect retired alongside the Knowledge → Training
            tab as part of the LoRA park. Old bookmarks just land on
            Knowledge. */}
        <Route path="/training" element={<Navigate to="/knowledge" replace />} />
        <Route path="/themes" element={<Suspense fallback={<RouteFallback />}><Themes /></Suspense>} />
        <Route path="/settings" element={<Suspense fallback={<RouteFallback />}><Settings /></Suspense>} />
        {/* Old per-task routes — kept so existing bookmarks /
            cached memos resolve. TaskRouteRedirect resolves the
            task's linked AgentJob and forwards to /jobs/<id>?view=...
            so the unified page is the canonical destination. */}
        <Route path="/tasks/:taskId/review" element={<Suspense fallback={<RouteFallback />}><TaskRouteRedirect view="diff" /></Suspense>} />
        <Route path="/tasks/:taskId/plan" element={<Suspense fallback={<RouteFallback />}><TaskRouteRedirect view="plan" /></Suspense>} />
        <Route path="/tasks/:taskId/report" element={<Suspense fallback={<RouteFallback />}><TaskRouteRedirect view="report" /></Suspense>} />
        <Route path="/jobs/:jobId" element={<Suspense fallback={<RouteFallback />}><AgentJobPage /></Suspense>} />
        {/* Legacy routes */}
        <Route path="/skills" element={<Navigate to="/automations" replace />} />
        <Route path="/brain" element={<Navigate to="/knowledge" replace />} />
        <Route path="/learn" element={<Navigate to="/knowledge" replace />} />
        <Route path="/projects" element={<Navigate to="/tasks" replace />} />
        <Route path="/team" element={<Navigate to="/knowledge" replace />} />
        <Route path="/ideas" element={<Navigate to="/" replace />} />
        <Route path="/brainstorm" element={<Navigate to="/" replace />} />
        <Route path="/suggestions" element={<Navigate to="/" replace />} />
        <Route path="/gathering" element={<Navigate to="/agents" replace />} />
        <Route path="/tournaments" element={<Navigate to="/agents" replace />} />
      </Route>
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AppRoutes />
      <ToastContainer />
      <AskMaiko />
      <ArrivalWatcher />
      <PersistentPack />
    </BrowserRouter>
  );
}
