import { Routes, Route, NavLink, useLocation } from "react-router-dom";
import {
  LayoutDashboard, Brain, Clock, Zap, BookOpen,
  TrendingUp, CheckCircle, RotateCcw,
  Bell, Settings, BarChart2, FlaskConical,
} from "lucide-react";
import clsx from "clsx";

import Dashboard from "./pages/Dashboard";
import Memories from "./pages/Memories";
import TimelinePage from "./pages/Timeline";
import Skills from "./pages/Skills";
import Reflections from "./pages/Reflections";
import Improvements from "./pages/Improvements";
import Approvals from "./pages/Approvals";
import ApprovalDetail from "./pages/ApprovalDetail";
import Rollbacks from "./pages/Rollbacks";
import NotificationsPage from "./pages/Notifications";
import SettingsPage from "./pages/SettingsPage";
import Telemetry from "./pages/Telemetry";
import Simulation from "./pages/Simulation";
import SimulationPlans from "./pages/SimulationPlans";
import SimulationPlanDetail from "./pages/SimulationPlanDetail";
import SimulationCounterfactuals from "./pages/SimulationCounterfactuals";
import SimulationForecasts from "./pages/SimulationForecasts";

const NAV = [
  { to: "/", icon: LayoutDashboard, label: "Dashboard" },
  { to: "/memories", icon: Brain, label: "Memories" },
  { to: "/timeline", icon: Clock, label: "Timeline" },
  { to: "/skills", icon: Zap, label: "Skills" },
  { to: "/reflections", icon: BookOpen, label: "Reflections" },
  { to: "/improvements", icon: TrendingUp, label: "Improvements" },
  { to: "/approvals", icon: CheckCircle, label: "Approvals" },
  { to: "/rollbacks", icon: RotateCcw, label: "Rollbacks" },
  { to: "/telemetry", icon: BarChart2, label: "Telemetry" },
  { to: "/simulation", icon: FlaskConical, label: "Simulation" },
  { to: "/notifications", icon: Bell, label: "Notifications" },
  { to: "/settings", icon: Settings, label: "Settings" },
];

export default function App() {
  const loc = useLocation();

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar */}
      <aside className="w-56 bg-slate-900 border-r border-slate-800 flex flex-col shrink-0">
        <div className="px-4 py-5 border-b border-slate-800">
          <div className="flex items-center gap-2">
            <Brain className="text-brand-500" size={22} />
            <span className="text-lg font-semibold tracking-tight">Mimir</span>
          </div>
          <p className="text-xs text-slate-500 mt-0.5">Memory &amp; Learning Core</p>
        </div>
        <nav className="flex-1 overflow-y-auto py-2">
          {NAV.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                clsx(
                  "flex items-center gap-3 px-4 py-2.5 text-sm transition-colors",
                  isActive
                    ? "bg-brand-600/20 text-brand-400 border-r-2 border-brand-500"
                    : "text-slate-400 hover:text-slate-100 hover:bg-slate-800"
                )
              }
            >
              <Icon size={16} />
              {label}
            </NavLink>
          ))}
        </nav>
      </aside>

      {/* Main */}
      <main className="flex-1 overflow-y-auto bg-slate-950">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/memories" element={<Memories />} />
          <Route path="/timeline" element={<TimelinePage />} />
          <Route path="/skills" element={<Skills />} />
          <Route path="/reflections" element={<Reflections />} />
          <Route path="/improvements" element={<Improvements />} />
          <Route path="/approvals" element={<Approvals />} />
          <Route path="/approvals/:id" element={<ApprovalDetail />} />
          <Route path="/rollbacks" element={<Rollbacks />} />
          <Route path="/telemetry" element={<Telemetry />} />
          <Route path="/simulation" element={<Simulation />} />
          <Route path="/simulation/plans" element={<SimulationPlans />} />
          <Route path="/simulation/plans/:id" element={<SimulationPlanDetail />} />
          <Route path="/simulation/counterfactuals" element={<SimulationCounterfactuals />} />
          <Route path="/simulation/forecasts" element={<SimulationForecasts />} />
          <Route path="/notifications" element={<NotificationsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </main>
    </div>
  );
}
