import { lazy, Suspense } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import Layout from "./components/Layout";
import ToastContainer from "./components/Toast";
import AskMaiko from "./components/AskMaiko";
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
const Training = lazy(() => import("./pages/Training"));
const Themes = lazy(() => import("./pages/Themes"));
const ReviewDiff = lazy(() => import("./pages/ReviewDiff"));
const ReviewPlan = lazy(() => import("./pages/ReviewPlan"));
const PetLog = lazy(() => import("./pages/PetLog"));
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
        <Route path="/training" element={<Suspense fallback={<RouteFallback />}><Training /></Suspense>} />
        <Route path="/themes" element={<Suspense fallback={<RouteFallback />}><Themes /></Suspense>} />
        <Route path="/settings" element={<Suspense fallback={<RouteFallback />}><Settings /></Suspense>} />
        <Route path="/tasks/:taskId/review" element={<Suspense fallback={<RouteFallback />}><ReviewDiff /></Suspense>} />
        <Route path="/tasks/:taskId/plan" element={<Suspense fallback={<RouteFallback />}><ReviewPlan /></Suspense>} />
        <Route path="/pet-log" element={<Suspense fallback={<RouteFallback />}><PetLog /></Suspense>} />
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
    </BrowserRouter>
  );
}
