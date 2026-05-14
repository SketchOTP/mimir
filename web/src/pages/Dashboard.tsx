import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getConnectionOnboarding, getDashboard, getProjects } from "../lib/api";
import StatCard from "../components/StatCard";
import PageHeader from "../components/PageHeader";
import { RefreshCw } from "lucide-react";

interface DashData {
  memory_count: number;
  skill_count: number;
  pending_approvals: number;
  rollback_events: number;
  improvements_promoted: number;
  retrieval_relevance_score?: number;
  skill_success_rate?: number;
  context_token_cost?: number;
  recent_rollbacks: { id: string; target_id: string; reason: string; created_at: string }[];
  recent_lessons: string[];
}

interface ProjectSummary {
  project: string;
  memory_count: number;
  bootstrap: { health: string; last_bootstrap_at: string | null };
}

interface ConnectionOnboardingData {
  auth_mode: string;
  oauth_enabled: boolean;
  owner_exists: boolean;
  recommended_auth: string;
  urls: {
    dashboard: string;
    connection_settings: string;
    first_run_setup: string;
    oauth_authorize: string;
    mcp_url: string;
  };
  generated: {
    oauth_local: string;
    api_key_remote: string;
  };
  warnings: { code: string; message: string; severity: string }[];
}

export default function Dashboard() {
  const [data, setData] = useState<DashData | null>(null);
  const [onboarding, setOnboarding] = useState<ConnectionOnboardingData | null>(null);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>("");
  const [onboardingError, setOnboardingError] = useState<string>("");

  const load = async () => {
    setLoading(true);
    setError("");
    setOnboardingError("");
    try {
      const [dashboardResponse, onboardingResponse] = await Promise.all([
        getDashboard(),
        getConnectionOnboarding(),
      ]);
      setData(dashboardResponse.data);
      setOnboarding(onboardingResponse.data);
      // Projects load best-effort (auth may fail for unauthenticated state)
      try {
        const projResp = await getProjects();
        setProjects(projResp.data?.projects ?? []);
      } catch {
        setProjects([]);
      }
    } catch (err: any) {
      const detail = err?.response?.status === 401
        ? "Authentication failed while loading the dashboard."
        : "Dashboard data was unavailable or returned an unexpected shape.";
      const onboardingDetail = err?.response?.status === 401
        ? "Connection onboarding is unavailable until dashboard authentication succeeds."
        : "Connection onboarding data could not be loaded.";
      setData({
        memory_count: 0,
        skill_count: 0,
        pending_approvals: 0,
        rollback_events: 0,
        improvements_promoted: 0,
        recent_rollbacks: [],
        recent_lessons: [],
      });
      setError(detail);
      setOnboardingError(onboardingDetail);
      setOnboarding(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const pct = (v?: number) => v !== undefined ? `${(v * 100).toFixed(1)}%` : "—";
  const lessons = data?.recent_lessons ?? [];
  const rollbacks = data?.recent_rollbacks ?? [];

  return (
    <div>
      <PageHeader
        title="Dashboard"
        subtitle="System-wide learning state"
        action={
          <button onClick={load} className="flex items-center gap-1.5 text-sm text-slate-400 hover:text-slate-100 transition-colors">
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
            Refresh
          </button>
        }
      />
      <div className="p-6 space-y-6">
        {error && (
          <div className="rounded-lg border border-amber-700/50 bg-amber-950/30 px-4 py-3 text-sm text-amber-200">
            <strong className="block text-amber-100">Dashboard warning</strong>
            {error}
          </div>
        )}
        {loading && (
          <div className="rounded-lg border border-slate-800 bg-slate-900 px-4 py-3 text-sm text-slate-400">
            Loading dashboard…
          </div>
        )}
        <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-slate-100">Connect Cursor</h2>
              <p className="mt-1 text-sm text-slate-400">
                Setup now starts here: check auth mode, open guided setup, and copy the MCP config that matches this server.
              </p>
            </div>
            <span className="rounded-full border border-slate-700 bg-slate-800 px-3 py-1 text-xs text-slate-300">
              Mode: {onboarding?.auth_mode ?? "unknown"}
            </span>
          </div>
          {onboardingError && (
            <p className="mt-3 text-sm text-amber-300">{onboardingError}</p>
          )}
          {onboarding && (
            <>
              <div className="mt-4 flex flex-wrap gap-3">
                <a
                  href={onboarding.urls.connection_settings}
                  className="inline-flex items-center rounded-md bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-500"
                >
                  Open Guided Connection Setup
                </a>
                <a
                  href={onboarding.urls.first_run_setup}
                  className="inline-flex items-center rounded-md border border-slate-700 px-3 py-2 text-sm font-medium text-slate-200 hover:border-slate-500 hover:text-white"
                >
                  Open First-Run Setup
                </a>
                <a
                  href={onboarding.urls.dashboard}
                  className="inline-flex items-center rounded-md border border-slate-700 px-3 py-2 text-sm font-medium text-slate-200 hover:border-slate-500 hover:text-white"
                >
                  Open Dashboard Home
                </a>
              </div>
              <div className="mt-4 grid gap-4 md:grid-cols-2">
                <div className="rounded-md border border-slate-800 bg-slate-950 p-3">
                  <p className="text-xs font-medium uppercase tracking-wide text-slate-400">Local OAuth MCP JSON</p>
                  <pre className="mt-2 overflow-x-auto text-xs text-slate-300">{onboarding.generated.oauth_local}</pre>
                </div>
                <div className="rounded-md border border-slate-800 bg-slate-950 p-3">
                  <p className="text-xs font-medium uppercase tracking-wide text-slate-400">SSH/Remote MCP JSON</p>
                  <pre className="mt-2 overflow-x-auto text-xs text-slate-300">{onboarding.generated.api_key_remote}</pre>
                </div>
              </div>
              {onboarding.warnings.length > 0 && (
                <div className="mt-3 rounded-md border border-amber-700/40 bg-amber-950/20 p-3 text-sm text-amber-200">
                  {onboarding.warnings[0]?.message}
                </div>
              )}
            </>
          )}
        </div>
        {/* Project memory profiles */}
        <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-slate-100">Repo Memory Profiles</h2>
            <Link to="/projects" className="text-xs text-brand-400 hover:text-brand-300">View all →</Link>
          </div>
          {projects.length === 0 ? (
            <p className="text-sm text-slate-500">
              No projects bootstrapped yet.{" "}
              <span className="text-slate-400">
                From Cursor: <code className="text-brand-400">project_bootstrap(project=&quot;myproject&quot;)</code>
              </span>
            </p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {projects.map((p) => (
                <Link
                  key={p.project}
                  to={`/projects/${p.project}`}
                  className="flex items-center gap-2 rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm hover:border-slate-500 transition-colors"
                >
                  <span
                    className={
                      p.bootstrap.health === "healthy"
                        ? "text-emerald-400"
                        : p.bootstrap.health === "partial"
                        ? "text-amber-400"
                        : "text-red-400"
                    }
                  >
                    ●
                  </span>
                  <span className="font-mono text-slate-200">{p.project}</span>
                  <span className="text-slate-500">{p.memory_count}</span>
                </Link>
              ))}
            </div>
          )}
        </div>

        {/* Stats grid */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard label="Memories" value={data?.memory_count ?? "—"} />
          <StatCard label="Active Skills" value={data?.skill_count ?? "—"} />
          <StatCard
            label="Pending Approvals"
            value={data?.pending_approvals ?? "—"}
            color={data && data.pending_approvals > 0 ? "yellow" : "default"}
          />
          <StatCard label="Rollbacks" value={data?.rollback_events ?? "—"} />
        </div>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
          <StatCard
            label="Retrieval Relevance"
            value={pct(data?.retrieval_relevance_score)}
            color={data?.retrieval_relevance_score && data.retrieval_relevance_score >= 0.6 ? "green" : "yellow"}
          />
          <StatCard
            label="Skill Success Rate"
            value={pct(data?.skill_success_rate)}
            color={data?.skill_success_rate && data.skill_success_rate >= 0.7 ? "green" : "yellow"}
          />
          <StatCard label="Avg Context Tokens" value={data?.context_token_cost?.toFixed(0) ?? "—"} />
        </div>

        <div className="grid md:grid-cols-2 gap-6">
          {/* Recent lessons */}
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
            <h2 className="text-sm font-medium text-slate-300 mb-3">Recent Lessons Learned</h2>
            {lessons.length > 0 ? (
              <ul className="space-y-2">
                {lessons.map((l, i) => (
                  <li key={i} className="text-sm text-slate-400 flex gap-2">
                    <span className="text-brand-500 mt-0.5">▸</span>
                    <span>{l}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-slate-500">No lessons recorded yet.</p>
            )}
          </div>

          {/* Recent rollbacks */}
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
            <h2 className="text-sm font-medium text-slate-300 mb-3">Recent Rollbacks</h2>
            {rollbacks.length > 0 ? (
              <ul className="space-y-2">
                {rollbacks.map((r) => (
                  <li key={r.id} className="text-sm">
                    <span className="text-red-400">⊘</span>
                    <span className="text-slate-300 ml-2">{r.target_id}</span>
                    <span className="text-slate-500 ml-2 truncate">— {r.reason}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-slate-500">No rollbacks recorded.</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
