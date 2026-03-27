import { BrowserRouter, Routes, Route } from "react-router-dom";
import Layout from "./components/Layout";
import ToastContainer from "./components/Toast";
import Home from "./pages/Home";
import Inbox from "./pages/Inbox";
import Tasks from "./pages/Tasks";
import Projects from "./pages/Projects";
import Settings from "./pages/Settings";
import Agents from "./pages/Agents";
import Knowledge from "./pages/Knowledge";
import Gathering from "./pages/Gathering";
import Suggestions from "./pages/Suggestions";
import Team from "./pages/Team";
import Brainstorm from "./pages/Brainstorm";
import Skills from "./pages/Skills";
import useKeyboardShortcuts from "./hooks/useKeyboardShortcuts";

function AppRoutes() {
  useKeyboardShortcuts();

  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Home />} />
        <Route path="/inbox" element={<Inbox />} />
        <Route path="/tasks" element={<Tasks />} />
        <Route path="/team" element={<Team />} />
        <Route path="/brainstorm" element={<Brainstorm />} />
        <Route path="/suggestions" element={<Suggestions />} />
        <Route path="/agents" element={<Agents />} />
        <Route path="/gathering" element={<Gathering />} />
        <Route path="/knowledge" element={<Knowledge />} />
        <Route path="/skills" element={<Skills />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/projects" element={<Projects />} />
      </Route>
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AppRoutes />
      <ToastContainer />
    </BrowserRouter>
  );
}
