import { useEffect, useState } from "react";
import { BarChart2, RefreshCw } from "lucide-react";
import { computeCalibration, getCalibrationHistory } from "../lib/api";

interface CalibrationRow {
  id: string;
  period: string;
  project: string | null;
  forecast_accuracy: number;
  overconfidence_rate: number;
  underconfidence_rate: number;
  mean_prediction_error: number;
  sample_size: number;
  computed_at: string | null;
}

interface CalibrationResult {
  forecast_accuracy: number;
  overconfidence_rate: number;
  underconfidence_rate: number;
  mean_prediction_error: number;
  rollback_prediction_success: number | null;
  sample_size: number;
  period: string;
  project: string | null;
}

function Stat({ label, value, format = "pct" }: { label: string; value: number; format?: "pct" | "raw" }) {
  const display = format === "pct" ? `${Math.round(value * 100)}%` : value.toFixed(3);
  const good = format === "pct" ? value > 0.6 : value < 0.2;
  const bad = format === "pct" ? value < 0.4 : value > 0.4;
  const color = good ? "text-emerald-400" : bad ? "text-red-400" : "text-amber-400";
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4 text-center">
      <div className={`text-2xl font-mono font-semibold ${color}`}>{display}</div>
      <div className="text-xs text-slate-500 mt-1">{label}</div>
    </div>
  );
}

export default function SimulationForecasts() {
  const [latest, setLatest] = useState<CalibrationResult | null>(null);
  const [history, setHistory] = useState<CalibrationRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [computing, setComputing] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const [_cal, histRes] = await Promise.all([
        computeCalibration({ period: "daily", lookback_days: 30 }).catch(() => null),
        getCalibrationHistory({ limit: 20 }),
      ]);
      setHistory(histRes.data);
    } catch {
      setHistory([]);
    } finally {
      setLoading(false);
    }
  };

  const handleCompute = async () => {
    setComputing(true);
    try {
      const res = await computeCalibration({ period: "daily", lookback_days: 30 });
      setLatest(res.data);
      const histRes = await getCalibrationHistory({ limit: 20 });
      setHistory(histRes.data);
    } finally {
      setComputing(false);
    }
  };

  useEffect(() => {
    getCalibrationHistory({ limit: 20 })
      .then((r) => setHistory(r.data))
      .catch(() => setHistory([]))
      .finally(() => setLoading(false));
  }, []);

  const displayRow = latest ?? (history[0] as any ?? null);

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <BarChart2 className="text-brand-400" size={20} />
          <h1 className="text-lg font-semibold">Forecast Accuracy</h1>
        </div>
        <button
          onClick={handleCompute}
          disabled={computing}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 text-sm rounded border border-slate-700 disabled:opacity-50"
        >
          <RefreshCw size={13} className={computing ? "animate-spin" : ""} />
          {computing ? "Computing…" : "Recompute"}
        </button>
      </div>

      {loading ? (
        <p className="text-slate-500 text-sm">Loading…</p>
      ) : displayRow ? (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Stat label="Forecast accuracy" value={displayRow.forecast_accuracy ?? 0} />
            <Stat label="Overconfidence rate" value={displayRow.overconfidence_rate ?? 0} format="pct" />
            <Stat label="Underconfidence rate" value={displayRow.underconfidence_rate ?? 0} format="pct" />
            <Stat label="Mean prediction error" value={displayRow.mean_prediction_error ?? 0} format="raw" />
          </div>

          {(displayRow.rollback_prediction_success !== undefined && displayRow.rollback_prediction_success !== null) && (
            <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
              <span className="text-sm text-slate-400">Rollback prediction success: </span>
              <span className="font-mono text-sm text-slate-200">
                {Math.round(displayRow.rollback_prediction_success * 100)}%
              </span>
            </div>
          )}

          {displayRow.sample_size !== undefined && (
            <p className="text-xs text-slate-500">
              Based on {displayRow.sample_size} completed simulation(s) with recorded outcomes
              {displayRow.project ? ` · project: ${displayRow.project}` : ""}.
            </p>
          )}
        </>
      ) : (
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-6 text-center text-slate-500 text-sm">
          No calibration data yet. Run simulations and record actual outcomes, then click Recompute.
        </div>
      )}

      {/* History table */}
      {history.length > 0 && (
        <section>
          <h2 className="text-sm font-medium text-slate-300 mb-2">Calibration history</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-slate-500 border-b border-slate-800">
                  <th className="text-left py-1.5 pr-4">Computed at</th>
                  <th className="text-left py-1.5 pr-4">Period</th>
                  <th className="text-right py-1.5 pr-4">Accuracy</th>
                  <th className="text-right py-1.5 pr-4">Overconf.</th>
                  <th className="text-right py-1.5 pr-4">Underconf.</th>
                  <th className="text-right py-1.5 pr-4">Pred. error</th>
                  <th className="text-right py-1.5">n</th>
                </tr>
              </thead>
              <tbody>
                {history.map((row) => (
                  <tr key={row.id} className="border-b border-slate-800/50 text-slate-300">
                    <td className="py-1.5 pr-4 text-xs text-slate-400">
                      {row.computed_at ? new Date(row.computed_at).toLocaleString() : "—"}
                    </td>
                    <td className="py-1.5 pr-4 text-xs">{row.period}</td>
                    <td className="py-1.5 pr-4 text-right font-mono text-xs">
                      {Math.round(row.forecast_accuracy * 100)}%
                    </td>
                    <td className="py-1.5 pr-4 text-right font-mono text-xs">
                      {Math.round(row.overconfidence_rate * 100)}%
                    </td>
                    <td className="py-1.5 pr-4 text-right font-mono text-xs">
                      {Math.round(row.underconfidence_rate * 100)}%
                    </td>
                    <td className="py-1.5 pr-4 text-right font-mono text-xs">
                      {row.mean_prediction_error?.toFixed(3) ?? "—"}
                    </td>
                    <td className="py-1.5 text-right text-xs text-slate-500">{row.sample_size}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
