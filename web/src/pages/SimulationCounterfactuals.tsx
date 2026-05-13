import { useEffect, useState } from "react";
import { Shuffle, ChevronDown, ChevronUp } from "lucide-react";
import { listPlans, listCounterfactuals, runCounterfactual } from "../lib/api";

interface Plan {
  id: string;
  goal: string;
}

interface CF {
  id: string;
  plan_id: string;
  counterfactual_description: string;
  success_probability: number;
  risk_score: number;
  confidence_score: number;
  paths: any[];
  created_at: string | null;
}

function pct(v: number) { return `${Math.round(v * 100)}%`; }

function DeltaBadge({ delta }: { delta: number }) {
  const abs = Math.abs(delta);
  const color = delta > 0 ? "text-emerald-400" : delta < 0 ? "text-red-400" : "text-slate-400";
  const sign = delta > 0 ? "+" : "";
  return <span className={`font-mono text-xs ${color}`}>{sign}{pct(delta)}</span>;
}

export default function SimulationCounterfactuals() {
  const [plans, setPlans] = useState<Plan[]>([]);
  const [selectedPlan, setSelectedPlan] = useState<string>("");
  const [cfs, setCfs] = useState<CF[]>([]);
  const [loading, setLoading] = useState(false);
  const [scenario, setScenario] = useState("");
  const [overrideRisk, setOverrideRisk] = useState("");
  const [addRollback, setAddRollback] = useState("");
  const [running, setRunning] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);

  useEffect(() => {
    listPlans({ limit: 50 })
      .then((r) => {
        setPlans(r.data);
        if (r.data.length > 0) setSelectedPlan(r.data[0].id);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!selectedPlan) return;
    setLoading(true);
    listCounterfactuals(selectedPlan)
      .then((r) => setCfs(r.data))
      .catch(() => setCfs([]))
      .finally(() => setLoading(false));
  }, [selectedPlan]);

  const handleRun = async () => {
    if (!selectedPlan || !scenario.trim()) return;
    setRunning(true);
    try {
      const body: Record<string, any> = { scenario: scenario.trim() };
      if (overrideRisk) body.override_risk = parseFloat(overrideRisk);
      if (addRollback) body.add_rollback_option = addRollback.trim();
      await runCounterfactual(selectedPlan, body);
      setScenario("");
      setOverrideRisk("");
      setAddRollback("");
      // Reload
      const r = await listCounterfactuals(selectedPlan);
      setCfs(r.data);
    } finally {
      setRunning(false);
    }
  };

  const selectedPlanObj = plans.find((p) => p.id === selectedPlan);

  return (
    <div className="p-6 space-y-5">
      <div className="flex items-center gap-2">
        <Shuffle className="text-brand-400" size={20} />
        <h1 className="text-lg font-semibold">Counterfactual Explorer</h1>
      </div>

      {/* Plan selector */}
      <div className="flex gap-3 items-center">
        <label className="text-sm text-slate-400 shrink-0">Plan:</label>
        <select
          className="bg-slate-800 border border-slate-700 rounded px-3 py-1.5 text-sm text-slate-200 flex-1 max-w-md"
          value={selectedPlan}
          onChange={(e) => setSelectedPlan(e.target.value)}
        >
          {plans.map((p) => (
            <option key={p.id} value={p.id}>{p.goal}</option>
          ))}
        </select>
      </div>

      {/* Run form */}
      <div className="bg-slate-900 border border-slate-800 rounded-lg p-4 space-y-3">
        <h2 className="text-sm font-medium text-slate-300">Run new counterfactual</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div className="md:col-span-3">
            <input
              className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-2 text-sm text-slate-100 placeholder-slate-500"
              placeholder='Scenario description, e.g. "What if we add a validation step?"'
              value={scenario}
              onChange={(e) => setScenario(e.target.value)}
            />
          </div>
          <input
            className="bg-slate-800 border border-slate-700 rounded px-3 py-2 text-sm text-slate-100 placeholder-slate-500"
            placeholder="Override risk (0.0–1.0)"
            value={overrideRisk}
            onChange={(e) => setOverrideRisk(e.target.value)}
          />
          <input
            className="bg-slate-800 border border-slate-700 rounded px-3 py-2 text-sm text-slate-100 placeholder-slate-500"
            placeholder="Add rollback option (optional)"
            value={addRollback}
            onChange={(e) => setAddRollback(e.target.value)}
          />
          <button
            onClick={handleRun}
            disabled={running || !scenario.trim() || !selectedPlan}
            className="px-4 py-2 bg-brand-600 hover:bg-brand-700 text-white text-sm rounded disabled:opacity-50"
          >
            {running ? "Running…" : "Run counterfactual"}
          </button>
        </div>
      </div>

      {/* Results */}
      {loading ? (
        <p className="text-slate-500 text-sm">Loading…</p>
      ) : cfs.length === 0 ? (
        <p className="text-slate-500 text-sm">No counterfactuals for this plan yet.</p>
      ) : (
        <div className="space-y-3">
          <h2 className="text-sm font-medium text-slate-300">
            Counterfactual history ({cfs.length})
          </h2>
          {cfs.map((cf) => {
            const baseRun = null; // would need the base sim to compute delta
            const isOpen = expanded === cf.id;
            const paths = cf.paths || [];
            const best = paths.find((p: any) => p.path_id?.includes("base")) ?? paths[0];
            return (
              <div key={cf.id} className="bg-slate-900 border border-slate-800 rounded-lg">
                <div
                  className="flex items-center justify-between p-4 cursor-pointer"
                  onClick={() => setExpanded(isOpen ? null : cf.id)}
                >
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-slate-200 truncate">
                      {cf.counterfactual_description || "Counterfactual"}
                    </p>
                    <div className="mt-1 flex gap-4 text-xs text-slate-400">
                      <span>success {pct(cf.success_probability)}</span>
                      <span>risk {pct(cf.risk_score)}</span>
                      <span>confidence {pct(cf.confidence_score)}</span>
                    </div>
                  </div>
                  <div className="ml-4 flex items-center gap-2 text-slate-500">
                    <span className="text-xs">
                      {cf.created_at ? new Date(cf.created_at).toLocaleDateString() : ""}
                    </span>
                    {isOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                  </div>
                </div>
                {isOpen && paths.length > 0 && (
                  <div className="border-t border-slate-800 p-4 space-y-2">
                    {paths.map((p: any) => (
                      <div key={p.path_id} className="bg-slate-800 rounded p-3 text-xs text-slate-300">
                        <div className="font-medium mb-1">{p.description}</div>
                        <div className="flex gap-4 text-slate-400">
                          <span>✓ {pct(p.success_probability)}</span>
                          <span>risk {pct(p.risk_score)}</span>
                          <span>rollback risk {pct(p.rollback_risk)}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
