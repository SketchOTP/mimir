import { Link } from "react-router-dom";
import { GitBranch, FlaskConical, Shuffle, BarChart2, ShieldAlert } from "lucide-react";

const sections = [
  {
    to: "/simulation/plans",
    icon: GitBranch,
    label: "Plans",
    description: "View and manage execution plans with DAG-validated steps, risk scores, and approval state.",
  },
  {
    to: "/simulation/counterfactuals",
    icon: Shuffle,
    label: "Counterfactuals",
    description: "Explore what-if scenarios — change risk, procedures, or rollback options and see predicted deltas.",
  },
  {
    to: "/simulation/forecasts",
    icon: BarChart2,
    label: "Forecast Accuracy",
    description: "Track forecast calibration: accuracy, overconfidence, underconfidence, and prediction error.",
  },
];

export default function Simulation() {
  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center gap-3">
        <FlaskConical className="text-brand-400" size={24} />
        <div>
          <h1 className="text-xl font-semibold">Simulation</h1>
          <p className="text-sm text-slate-400 mt-0.5">
            Predictive planning, multi-path simulation, and counterfactual reasoning.
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {sections.map(({ to, icon: Icon, label, description }) => (
          <Link
            key={to}
            to={to}
            className="block bg-slate-900 border border-slate-800 rounded-lg p-5 hover:border-brand-500 transition-colors"
          >
            <div className="flex items-center gap-2 mb-2">
              <Icon className="text-brand-400" size={18} />
              <span className="font-medium text-slate-100">{label}</span>
            </div>
            <p className="text-sm text-slate-400">{description}</p>
          </Link>
        ))}
      </div>

      <div className="bg-slate-900 border border-slate-800 rounded-lg p-5">
        <div className="flex items-center gap-2 mb-3">
          <ShieldAlert className="text-amber-400" size={16} />
          <span className="text-sm font-medium text-slate-200">How it works</span>
        </div>
        <ul className="text-sm text-slate-400 space-y-1.5 list-disc list-inside">
          <li>Create a plan with a goal and step graph — the engine validates the DAG for cycles.</li>
          <li>Run simulation to generate up to 3 execution paths (base, validation-first, rollback-safe).</li>
          <li>High-risk plans (risk ≥ 0.7 or destructive keywords) are automatically gated for approval.</li>
          <li>Run counterfactuals to measure the delta of changing risk, procedures, or rollback options.</li>
          <li>Record actual outcomes to improve forecast calibration over time.</li>
          <li>Historical simulation results are stored as retrievable memory evidence for future planning.</li>
        </ul>
      </div>
    </div>
  );
}
