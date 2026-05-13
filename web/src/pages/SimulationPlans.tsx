import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { GitBranch, Plus, CheckCircle, XCircle, Clock, AlertTriangle } from "lucide-react";
import { listPlans, createPlan } from "../lib/api";

interface Plan {
  id: string;
  goal: string;
  status: string;
  risk_estimate: number;
  confidence_estimate: number;
  approval_required: boolean;
  graph_valid: boolean;
  graph_errors: string[];
  steps: object[];
  project: string | null;
  created_at: string | null;
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    draft: "bg-slate-700 text-slate-300",
    pending_approval: "bg-amber-900/50 text-amber-300",
    approved: "bg-emerald-900/50 text-emerald-300",
    rejected: "bg-red-900/50 text-red-300",
    executing: "bg-blue-900/50 text-blue-300",
    complete: "bg-emerald-900/50 text-emerald-300",
    failed: "bg-red-900/50 text-red-300",
  };
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium ${map[status] ?? "bg-slate-700 text-slate-300"}`}>
      {status}
    </span>
  );
}

function RiskBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color = value > 0.7 ? "bg-red-500" : value > 0.4 ? "bg-amber-500" : "bg-emerald-500";
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 bg-slate-800 rounded-full h-1.5">
        <div className={`${color} h-1.5 rounded-full`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-slate-400 w-8 text-right">{pct}%</span>
    </div>
  );
}

export default function SimulationPlans() {
  const [plans, setPlans] = useState<Plan[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [goal, setGoal] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const load = () => {
    setLoading(true);
    listPlans({ limit: 50 })
      .then((r) => setPlans(r.data))
      .catch(() => setPlans([]))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  const handleCreate = async () => {
    if (!goal.trim()) return;
    setSubmitting(true);
    try {
      await createPlan({ goal: goal.trim(), steps: [], risk_estimate: 0.3, confidence_estimate: 0.5 });
      setGoal("");
      setShowCreate(false);
      load();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="p-6 space-y-5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <GitBranch className="text-brand-400" size={20} />
          <h1 className="text-lg font-semibold">Plans</h1>
          <span className="text-xs text-slate-500 ml-1">({plans.length})</span>
        </div>
        <button
          onClick={() => setShowCreate((v) => !v)}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-brand-600 hover:bg-brand-700 text-white text-sm rounded transition-colors"
        >
          <Plus size={14} />
          New plan
        </button>
      </div>

      {showCreate && (
        <div className="bg-slate-900 border border-slate-700 rounded-lg p-4 space-y-3">
          <input
            className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-2 text-sm text-slate-100 placeholder-slate-500"
            placeholder="Plan goal…"
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleCreate()}
          />
          <div className="flex gap-2">
            <button
              onClick={handleCreate}
              disabled={submitting}
              className="px-3 py-1.5 bg-brand-600 hover:bg-brand-700 text-white text-sm rounded disabled:opacity-50"
            >
              {submitting ? "Creating…" : "Create"}
            </button>
            <button
              onClick={() => setShowCreate(false)}
              className="px-3 py-1.5 text-slate-400 hover:text-slate-200 text-sm"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {loading ? (
        <p className="text-slate-500 text-sm">Loading…</p>
      ) : plans.length === 0 ? (
        <p className="text-slate-500 text-sm">No plans yet. Create one to start simulating.</p>
      ) : (
        <div className="space-y-2">
          {plans.map((p) => (
            <Link
              key={p.id}
              to={`/simulation/plans/${p.id}`}
              className="block bg-slate-900 border border-slate-800 rounded-lg p-4 hover:border-brand-500 transition-colors"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-medium text-slate-100 truncate">{p.goal}</span>
                    <StatusBadge status={p.status} />
                    {p.approval_required && (
                      <span className="flex items-center gap-1 text-xs text-amber-400">
                        <AlertTriangle size={11} /> approval required
                      </span>
                    )}
                    {!p.graph_valid && (
                      <span className="flex items-center gap-1 text-xs text-red-400">
                        <XCircle size={11} /> invalid graph
                      </span>
                    )}
                  </div>
                  <div className="mt-2 grid grid-cols-2 gap-x-6 gap-y-1 text-xs text-slate-400">
                    <span>{p.steps?.length ?? 0} steps</span>
                    <span>confidence {Math.round((p.confidence_estimate ?? 0) * 100)}%</span>
                  </div>
                  <div className="mt-2">
                    <RiskBar value={p.risk_estimate ?? 0} />
                  </div>
                </div>
                <div className="text-xs text-slate-500 shrink-0">
                  {p.created_at ? new Date(p.created_at).toLocaleDateString() : "—"}
                </div>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
