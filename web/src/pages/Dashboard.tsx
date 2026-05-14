import { useEffect, useState } from "react";
import { getDashboard } from "../lib/api";
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

export default function Dashboard() {
  const [data, setData] = useState<DashData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>("");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const r = await getDashboard();
      setData(r.data);
    } catch (err: any) {
      const detail = err?.response?.status === 401
        ? "Authentication failed while loading the dashboard."
        : "Dashboard data was unavailable or returned an unexpected shape.";
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
