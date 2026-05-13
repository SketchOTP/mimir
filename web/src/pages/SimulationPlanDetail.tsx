import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  GitBranch, Play, CheckCircle, XCircle, AlertTriangle,
  ArrowLeft, ChevronDown, ChevronUp,
} from "lucide-react";
import {
  getPlan, approvePlan, rejectPlan,
  runSimulation, listSimulations,
  listCounterfactuals,
} from "../lib/api";

interface Step {
  id: string;
  description: string;
  dependencies: string[];
  risk_estimate: number;
  rollback_option: string | null;
}

interface SimPath {
  path_id: string;
  description: string;
  steps: string[];
  success_probability: number;
  risk_score: number;
  rollback_risk: number;
}

interface SimRun {
  id: string;
  simulation_type: string;
  status: string;
  paths: SimPath[];
  best_path_id: string | null;
  success_probability: number;
  risk_score: number;
  confidence_score: number;
  expected_failure_modes: string[];
  actual_outcome: string | null;
  created_at: string | null;
}

interface CF {
  id: string;
  counterfactual_description: string;
  success_probability: number;
  risk_score: number;
  created_at: string | null;
}

function pct(v: number) { return `${Math.round(v * 100)}%`; }

function RiskChip({ value }: { value: number }) {
  const color = value > 0.7 ? "text-red-400" : value > 0.4 ? "text-amber-400" : "text-emerald-400";
  return <span className={`font-mono text-xs ${color}`}>{pct(value)}</span>;
}

function PathCard({ path, isBest }: { path: SimPath; isBest: boolean }) {
  const [open, setOpen] = useState(isBest);
  return (
    <div className={`border rounded-lg p-3 ${isBest ? "border-brand-500 bg-brand-600/10" : "border-slate-700 bg-slate-800"}`}>
      <div
        className="flex items-center justify-between cursor-pointer"
        onClick={() => setOpen((v) => !v)}
      >
        <div className="flex items-center gap-2">
          {isBest && <CheckCircle size={14} className="text-brand-400" />}
          <span className="text-sm font-medium text-slate-200">{path.description}</span>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-slate-400">
            ✓ {pct(path.success_probability)} · risk {pct(path.risk_score)}
          </span>
          {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </div>
      </div>
      {open && (
        <div className="mt-3 space-y-2">
          <div className="text-xs text-slate-400">
            <span className="text-slate-500">Steps:</span>{" "}
            {path.steps.join(" → ")}
          </div>
          <div className="text-xs text-slate-400">
            <span className="text-slate-500">Rollback risk:</span>{" "}
            <RiskChip value={path.rollback_risk} />
          </div>
        </div>
      )}
    </div>
  );
}

export default function SimulationPlanDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [plan, setPlan] = useState<any>(null);
  const [simRuns, setSimRuns] = useState<SimRun[]>([]);
  const [cfs, setCfs] = useState<CF[]>([]);
  const [loading, setLoading] = useState(true);
  const [simulating, setSimulating] = useState(false);
  const [approving, setApproving] = useState(false);

  const load = async () => {
    if (!id) return;
    setLoading(true);
    try {
      const [planRes, simRes, cfRes] = await Promise.all([
        getPlan(id),
        listSimulations(id),
        listCounterfactuals(id),
      ]);
      setPlan(planRes.data);
      setSimRuns(simRes.data);
      setCfs(cfRes.data);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [id]);

  const handleSimulate = async () => {
    if (!id) return;
    setSimulating(true);
    try {
      await runSimulation(id, { max_depth: 5, max_branches: 3, token_budget: 10000 });
      await load();
    } finally {
      setSimulating(false);
    }
  };

  const handleApprove = async () => {
    if (!id) return;
    setApproving(true);
    try {
      await approvePlan(id);
      await load();
    } finally {
      setApproving(false);
    }
  };

  const handleReject = async () => {
    if (!id) return;
    const reason = prompt("Rejection reason (optional):");
    if (reason === null) return;
    await rejectPlan(id, reason);
    await load();
  };

  if (loading) return <div className="p-6 text-slate-500 text-sm">Loading…</div>;
  if (!plan) return <div className="p-6 text-slate-500 text-sm">Plan not found.</div>;

  const steps: Step[] = plan.steps || [];
  const latestRun: SimRun | null = simRuns[0] ?? null;

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-start gap-3">
        <button onClick={() => navigate("/simulation/plans")} className="text-slate-500 hover:text-slate-200 mt-0.5">
          <ArrowLeft size={18} />
        </button>
        <div className="flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <h1 className="text-lg font-semibold text-slate-100">{plan.goal}</h1>
            <span className="px-2 py-0.5 rounded text-xs font-medium bg-slate-700 text-slate-300">
              {plan.status}
            </span>
            {plan.approval_required && (
              <span className="flex items-center gap-1 text-xs text-amber-400">
                <AlertTriangle size={11} /> approval required
              </span>
            )}
          </div>
          <div className="mt-1 flex gap-4 text-xs text-slate-400">
            <span>Risk: <span className="font-mono">{pct(plan.risk_estimate)}</span></span>
            <span>Confidence: <span className="font-mono">{pct(plan.confidence_estimate)}</span></span>
            <span>Steps: {steps.length}</span>
            {!plan.graph_valid && (
              <span className="text-red-400 flex items-center gap-1">
                <XCircle size={11} /> {plan.graph_errors?.join("; ")}
              </span>
            )}
          </div>
        </div>
        <div className="flex gap-2 shrink-0">
          {plan.approval_required && plan.status === "pending_approval" && (
            <>
              <button
                onClick={handleApprove}
                disabled={approving}
                className="px-3 py-1.5 bg-emerald-700 hover:bg-emerald-600 text-white text-sm rounded"
              >
                Approve
              </button>
              <button
                onClick={handleReject}
                className="px-3 py-1.5 bg-red-800 hover:bg-red-700 text-white text-sm rounded"
              >
                Reject
              </button>
            </>
          )}
          <button
            onClick={handleSimulate}
            disabled={simulating}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-brand-600 hover:bg-brand-700 text-white text-sm rounded disabled:opacity-50"
          >
            <Play size={13} />
            {simulating ? "Simulating…" : "Run simulation"}
          </button>
        </div>
      </div>

      {/* Steps */}
      {steps.length > 0 && (
        <section>
          <h2 className="text-sm font-medium text-slate-300 mb-2">Steps</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-slate-500 border-b border-slate-800">
                  <th className="text-left py-1.5 pr-4">ID</th>
                  <th className="text-left py-1.5 pr-4">Description</th>
                  <th className="text-left py-1.5 pr-4">Depends on</th>
                  <th className="text-left py-1.5 pr-4">Risk</th>
                  <th className="text-left py-1.5">Rollback</th>
                </tr>
              </thead>
              <tbody>
                {steps.map((s) => (
                  <tr key={s.id} className="border-b border-slate-800/50 text-slate-300">
                    <td className="py-1.5 pr-4 font-mono text-xs text-slate-400">{s.id}</td>
                    <td className="py-1.5 pr-4">{s.description}</td>
                    <td className="py-1.5 pr-4 text-xs text-slate-500">{s.dependencies?.join(", ") || "—"}</td>
                    <td className="py-1.5 pr-4"><RiskChip value={s.risk_estimate ?? 0} /></td>
                    <td className="py-1.5 text-xs text-slate-500">{s.rollback_option ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* Latest simulation */}
      {latestRun && (
        <section>
          <h2 className="text-sm font-medium text-slate-300 mb-2">
            Latest simulation
            <span className="ml-2 text-xs text-slate-500">
              {latestRun.created_at ? new Date(latestRun.created_at).toLocaleString() : ""}
            </span>
          </h2>
          <div className="grid grid-cols-3 gap-3 mb-3">
            {[
              { label: "Success prob", value: pct(latestRun.success_probability) },
              { label: "Risk score", value: pct(latestRun.risk_score) },
              { label: "Confidence", value: pct(latestRun.confidence_score) },
            ].map(({ label, value }) => (
              <div key={label} className="bg-slate-900 border border-slate-800 rounded p-3 text-center">
                <div className="text-lg font-mono text-slate-100">{value}</div>
                <div className="text-xs text-slate-500 mt-0.5">{label}</div>
              </div>
            ))}
          </div>
          {latestRun.expected_failure_modes?.length > 0 && (
            <div className="bg-red-950/30 border border-red-900/40 rounded p-3 mb-3 text-xs text-red-300">
              <span className="font-medium">Expected failure modes: </span>
              {latestRun.expected_failure_modes.join("; ")}
            </div>
          )}
          <div className="space-y-2">
            {(latestRun.paths || []).map((path) => (
              <PathCard
                key={path.path_id}
                path={path}
                isBest={path.path_id === latestRun.best_path_id}
              />
            ))}
          </div>
        </section>
      )}

      {/* Counterfactuals */}
      {cfs.length > 0 && (
        <section>
          <h2 className="text-sm font-medium text-slate-300 mb-2">Counterfactuals ({cfs.length})</h2>
          <div className="space-y-2">
            {cfs.map((cf) => (
              <div key={cf.id} className="bg-slate-900 border border-slate-800 rounded p-3">
                <div className="text-sm text-slate-200 mb-1">{cf.counterfactual_description || "—"}</div>
                <div className="text-xs text-slate-400">
                  success {pct(cf.success_probability)} · risk {pct(cf.risk_score)}
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Rollback options */}
      {(plan.rollback_options?.length > 0) && (
        <section>
          <h2 className="text-sm font-medium text-slate-300 mb-2">Rollback options</h2>
          <ul className="space-y-1">
            {plan.rollback_options.map((ro: string, i: number) => (
              <li key={i} className="text-sm text-slate-400 flex items-center gap-2">
                <span className="text-slate-600">↩</span> {ro}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
