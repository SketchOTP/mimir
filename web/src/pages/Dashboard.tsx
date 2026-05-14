import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getConnectionOnboarding, getDashboard, getProjects } from "../lib/api";
import StatCard from "../components/StatCard";
import PageHeader from "../components/PageHeader";
import { RefreshCw, Copy, Check, Laptop, Terminal, Wifi } from "lucide-react";

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

type ConnectionTab = "local" | "ssh";

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };
  return (
    <button
      onClick={copy}
      className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-slate-100 transition-colors"
    >
      {copied ? <Check size={12} className="text-emerald-400" /> : <Copy size={12} />}
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

function McpConfigBlock({ label, json, icon: Icon }: { label: string; json: string; icon: React.ElementType }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-950 p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Icon size={14} className="text-slate-400" />
          <span className="text-xs font-medium text-slate-300">{label}</span>
        </div>
        <CopyButton text={json} />
      </div>
      <pre className="text-xs text-slate-400 overflow-x-auto leading-relaxed">{json}</pre>
    </div>
  );
}

export default function Dashboard() {
  const [data, setData] = useState<DashData | null>(null);
  const [onboarding, setOnboarding] = useState<ConnectionOnboardingData | null>(null);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>("");
  const [tab, setTab] = useState<ConnectionTab>("local");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const [dashboardResponse, onboardingResponse] = await Promise.all([
        getDashboard(),
        getConnectionOnboarding(),
      ]);
      setData(dashboardResponse.data);
      setOnboarding(onboardingResponse.data);
      try {
        const projResp = await getProjects();
        setProjects(projResp.data?.projects ?? []);
      } catch {
        setProjects([]);
      }
    } catch (err: any) {
      const detail = err?.response?.status === 401
        ? "Authentication required — check your API key."
        : "Could not reach the Mimir API. Is it running?";
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
        subtitle="System-wide memory &amp; learning state"
        action={
          <button onClick={load} className="flex items-center gap-1.5 text-sm text-slate-400 hover:text-slate-100 transition-colors">
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
            Refresh
          </button>
        }
      />
      <div className="p-6 space-y-6">

        {/* First-run banner — only when no owner exists */}
        {!loading && onboarding && !onboarding.owner_exists && (
          <div className="rounded-lg border border-brand-600/50 bg-brand-950/30 px-5 py-4">
            <div className="flex flex-wrap items-center justify-between gap-4">
              <div>
                <h2 className="text-base font-semibold text-slate-100">Finish setup to connect Cursor</h2>
                <p className="mt-1 text-sm text-slate-400">
                  No owner account yet. Create one to get your API key — takes 30 seconds.
                </p>
              </div>
              <a
                href={onboarding.urls.first_run_setup}
                className="inline-flex items-center rounded-md bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-500 whitespace-nowrap"
              >
                Create owner &amp; get API key →
              </a>
            </div>
          </div>
        )}

        {error && (
          <div className="rounded-lg border border-red-700/50 bg-red-950/30 px-4 py-3 text-sm text-red-200">
            {error}
          </div>
        )}

        {/* Connect Cursor panel */}
        {!loading && onboarding && onboarding.owner_exists && (
          <div className="rounded-lg border border-slate-800 bg-slate-900 p-5">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h2 className="text-sm font-semibold text-slate-100">Connect Cursor</h2>
                <p className="mt-0.5 text-xs text-slate-400">
                  Pick your setup type, copy the config, and paste into your{" "}
                  <code className="text-slate-300">~/.cursor/mcp.json</code>.
                </p>
              </div>
              <a
                href={onboarding.urls.connection_settings}
                className="text-xs text-brand-400 hover:text-brand-300"
              >
                Advanced settings →
              </a>
            </div>

            {/* Tab selector */}
            <div className="flex gap-1 mb-4 bg-slate-950 rounded-lg p-1 w-fit">
              <button
                onClick={() => setTab("local")}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                  tab === "local"
                    ? "bg-slate-700 text-slate-100"
                    : "text-slate-400 hover:text-slate-200"
                }`}
              >
                <Laptop size={12} />
                Local
              </button>
              <button
                onClick={() => setTab("ssh")}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                  tab === "ssh"
                    ? "bg-slate-700 text-slate-100"
                    : "text-slate-400 hover:text-slate-200"
                }`}
              >
                <Terminal size={12} />
                SSH / Remote
              </button>
            </div>

            {tab === "local" && onboarding.generated.oauth_local && (
              <McpConfigBlock
                label="Local — Cursor running on the same machine as Mimir"
                json={onboarding.generated.oauth_local}
                icon={Laptop}
              />
            )}
            {tab === "ssh" && onboarding.generated.api_key_remote && (
              <McpConfigBlock
                label="SSH / Remote — Cursor on your laptop, Mimir on a server"
                json={onboarding.generated.api_key_remote}
                icon={Terminal}
              />
            )}

            {onboarding.warnings.length > 0 && (
              <div className="mt-3 rounded-md border border-amber-700/40 bg-amber-950/20 p-3 text-xs text-amber-300">
                {onboarding.warnings[0]?.message}
              </div>
            )}
          </div>
        )}

        {/* Show a simpler card while loading or when onboarding unavailable */}
        {!loading && !onboarding && (
          <div className="rounded-lg border border-slate-800 bg-slate-900 p-5">
            <h2 className="text-sm font-semibold text-slate-100 mb-1">Connect Cursor</h2>
            <p className="text-xs text-slate-500">
              Could not load connection info.{" "}
              <a href="/settings/connection" className="text-brand-400 hover:underline">Open connection settings</a>
              {" "}or check the API is running.
            </p>
          </div>
        )}

        {/* Project memory profiles */}
        <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-slate-100">Repo Memory Profiles</h2>
            <Link to="/projects" className="text-xs text-brand-400 hover:text-brand-300">View all →</Link>
          </div>
          {projects.length === 0 ? (
            <p className="text-sm text-slate-500">
              No projects yet.{" "}
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
