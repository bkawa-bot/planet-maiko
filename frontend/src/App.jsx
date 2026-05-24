import { lazy, Suspense, useEffect, useState } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import Layout from "./components/Layout";
import ToastContainer from "./components/Toast";
import ArrivalWatcher from "./components/ArrivalWatcher";
import PersistentPack from "./components/PersistentPack";
import QuickLaunchModal from "./components/QuickLaunchModal";
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
const AgentJobPage = lazy(() => import("./pages/AgentJobPage"));
const MaikoChat = lazy(() => import("./pages/MaikoChat"));
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

  // Global Quick-Launch modal — mounted here so Cmd+K and the Home
  // launcher button both open the same instance. open-launch-agent
  // is dispatched by useKeyboardShortcuts and by the Home trigger.
  const [launchOpen, setLaunchOpen] = useState(false);
  useEffect(() => {
    const onOpen = () => setLaunchOpen(true);
    window.addEventListener("open-launch-agent", onOpen);
    return () => window.removeEventListener("open-launch-agent", onOpen);
  }, []);

  return (
    <>
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
        <Route path="/jobs/:jobId" element={<Suspense fallback={<RouteFallback />}><AgentJobPage /></Suspense>} />
        <Route path="/maiko" element={<Suspense fallback={<RouteFallback />}><MaikoChat /></Suspense>} />
        {/* Renamed pages — keep bookmarks working. */}
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
    <QuickLaunchModal open={launchOpen} onClose={() => setLaunchOpen(false)} />
    </>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AppRoutes />
      <ToastContainer />
      <ArrivalWatcher />
      <PersistentPack />
    </BrowserRouter>
  );
}
