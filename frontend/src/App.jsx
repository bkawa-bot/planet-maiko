import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import Layout from "./components/Layout";
import ToastContainer from "./components/Toast";
import AskMaiko from "./components/AskMaiko";
import Home from "./pages/Home";
import Inbox from "./pages/Inbox";
import Tasks from "./pages/Tasks";
import Settings from "./pages/Settings";
import Agents from "./pages/Agents";
import BrainView from "./pages/BrainView";
import Skills from "./pages/Skills";
import Training from "./pages/Training";
import useKeyboardShortcuts from "./hooks/useKeyboardShortcuts";

function AppRoutes() {
  useKeyboardShortcuts();

  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Home />} />
        <Route path="/inbox" element={<Inbox />} />
        <Route path="/tasks" element={<Tasks />} />
        <Route path="/agents" element={<Agents />} />
        <Route path="/knowledge" element={<BrainView />} />
        <Route path="/skills" element={<Skills />} />
        <Route path="/training" element={<Training />} />
        <Route path="/settings" element={<Settings />} />
        {/* Legacy routes */}
        <Route path="/brain" element={<Navigate to="/knowledge" replace />} />
        <Route path="/learn" element={<Navigate to="/knowledge" replace />} />
        <Route path="/projects" element={<Navigate to="/tasks" replace />} />
        <Route path="/team" element={<Navigate to="/knowledge" replace />} />
        <Route path="/ideas" element={<Navigate to="/inbox" replace />} />
        <Route path="/brainstorm" element={<Navigate to="/inbox" replace />} />
        <Route path="/suggestions" element={<Navigate to="/inbox" replace />} />
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
