import { useEffect, useState } from "react";
import {
  getTelemetrySnapshot, computeTelemetrySnapshot, getRetrievalStats,
  getRetrievalHeatmap, getProceduralEffectiveness, getDriftCandidates,
  applyDriftDecay, getProviderStats, aggregateProviderStats, getProviderDrift,
} from "../lib/api";
import PageHeader from "../components/PageHeader";
import StatCard from "../components/StatCard";
import { RefreshCw, AlertTriangle, Zap, Activity, BarChart2 } from "lucide-react";

interface Metrics { [key: string]: number }
interface RetrievalStats {
  total_sessions: number;
  sessions_with_outcome: number;
  outcome_distribution: Record<string, number>;
  avg_token_cost: number;
  avg_usefulness_score: number | null;
  sessions_with_rollback: number;
  sessions_with_harmful: number;
  window_hours: number;
}
interface HeatmapEntry {
  id: string; layer: string; content_snippet: string; trust_score: number;
  times_retrieved: number; success_rate: number | null;
}
interface ProceduralEntry {
  memory_id: string; content_snippet: string; trust_score: number;
  success_rate: number | null; failure_rate: number | null;
  evidence_count: number; times_retrieved: number; memory_state: string;
}
interface DriftCandidate {
  memory_id: string; content_snippet: string; trust_score: number;
  failure_rate: number; recent_failure_rate: number; recommended_action: string;
}
interface ProviderStat {
  provider_name: string; task_category: string | null;
  total_sessions: number; useful_sessions: number; harmful_sessions: number;
  usefulness_rate: number; harmful_rate: number;
  avg_token_efficiency: number; weight_current: number;
  drift_flagged: boolean; drift_reason: string | null;
}
interface ProviderDrift {
  provider_name: string; task_category: string | null;
  usefulness_rate: number; harmful_rate: number;
  weight_current: number; drift_reason: string | null;
}

const pct = (v?: number | null) => v !== undefined && v !== null ? `${(v * 100).toFixed(1)}%` : "—";
const num = (v?: number | null) => v !== undefined && v !== null ? v.toFixed(3) : "—";

export default function Telemetry() {
  const [metrics, setMetrics] = useState<Metrics>({});
  const [stats, setStats] = useState<RetrievalStats | null>(null);
  const [heatmap, setHeatmap] = useState<{ most_used: HeatmapEntry[]; rarely_used: HeatmapEntry[] } | null>(null);
  const [procedural, setProcedural] = useState<ProceduralEntry[]>([]);
  const [drift, setDrift] = useState<DriftCandidate[]>([]);
  const [providerStats, setProviderStats] = useState<ProviderStat[]>([]);
  const [providerDrift, setProviderDrift] = useState<ProviderDrift[]>([]);
  const [loading, setLoading] = useState(true);
  const [computing, setComputing] = useState(false);
  const [decaying, setDecaying] = useState(false);
  const [aggregating, setAggregating] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const [snap, st, hm, proc, dr, ps, pd] = await Promise.all([
        getTelemetrySnapshot(),
        getRetrievalStats(),
        getRetrievalHeatmap(),
        getProceduralEffectiveness(),
        getDriftCandidates(),
        getProviderStats(),
        getProviderDrift(),
      ]);
      setMetrics(snap.data.metrics || {});
      setStats(st.data.stats || null);
      setHeatmap(hm.data.heatmap || null);
      setProcedural(proc.data.procedural_memories || []);
      setDrift(dr.data.drift_candidates || []);
      setProviderStats(ps.data.provider_stats || []);
      setProviderDrift(pd.data.drifting_providers || []);
    } catch (e) {
      console.error("Telemetry load failed", e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const handleCompute = async () => {
    setComputing(true);
    try { await computeTelemetrySnapshot(); await load(); } finally { setComputing(false); }
  };

  const handleDecay = async () => {
    if (!window.confirm(`Apply drift decay to ${drift.length} candidates?`)) return;
    setDecaying(true);
    try { await applyDriftDecay(); await load(); } finally { setDecaying(false); }
  };

  const handleAggregate = async () => {
    setAggregating(true);
    try { await aggregateProviderStats(); await load(); } finally { setAggregating(false); }
  };

  return (
    <div>
      <PageHeader
        title="Cognitive Telemetry"
        subtitle="Retrieval effectiveness, provider analytics, procedural metrics, drift detection"
        action={
          <div className="flex gap-2">
            <button
              onClick={handleAggregate}
              disabled={aggregating}
              className="flex items-center gap-1.5 text-sm bg-slate-700 hover:bg-slate-600 text-white px-3 py-1.5 rounded disabled:opacity-50"
            >
              <BarChart2 size={14} />
              {aggregating ? "Aggregating…" : "Aggregate Providers"}
            </button>
            <button
              onClick={handleCompute}
              disabled={computing}
              className="flex items-center gap-1.5 text-sm bg-brand-600 hover:bg-brand-500 text-white px-3 py-1.5 rounded disabled:opacity-50"
            >
              <RefreshCw size={14} className={computing ? "animate-spin" : ""} />
              {computing ? "Computing…" : "Recompute"}
            </button>
          </div>
        }
      />

      {loading ? (
        <div className="p-8 text-slate-400 text-center">Loading telemetry…</div>
      ) : (
        <div className="p-6 space-y-8">

          {/* ── Cognitive Metrics ── */}
          <section>
            <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">Cognitive Metrics</h2>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <StatCard label="Retrieval Usefulness" value={pct(metrics.retrieval_usefulness_rate)} />
              <StatCard label="Harmful Retrieval" value={pct(metrics.harmful_retrieval_rate)} />
              <StatCard label="Procedural Success" value={pct(metrics.procedural_success_rate)} />
              <StatCard label="Retrieval→Success" value={pct(metrics.retrieval_to_success_rate)} />
              <StatCard label="Avg Trust Score" value={num(metrics.avg_trust_score)} />
              <StatCard label="High Trust %" value={pct(metrics.high_trust_pct)} />
              <StatCard label="Rollback Count" value={metrics.rollback_count?.toString() ?? "0"} />
              <StatCard label="Avg Token Efficiency" value={pct(metrics.avg_token_efficiency)} />
            </div>
          </section>

          {/* ── Memory State Distribution ── */}
          <section>
            <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">Memory State Distribution</h2>
            <div className="grid grid-cols-3 md:grid-cols-6 gap-3">
              {["active", "aging", "stale", "archived", "quarantined", "contradicted"].map(state => (
                <div key={state} className="bg-slate-900 border border-slate-800 rounded-lg p-3 text-center">
                  <div className="text-lg font-bold text-slate-100">
                    {pct(metrics[`memory_state_${state}_pct`])}
                  </div>
                  <div className="text-xs text-slate-500 mt-0.5 capitalize">{state}</div>
                </div>
              ))}
            </div>
          </section>

          {/* ── Retrieval Session Stats ── */}
          {stats && (
            <section>
              <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">
                Retrieval Sessions (last {stats.window_hours}h)
              </h2>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <StatCard label="Total Sessions" value={stats.total_sessions.toString()} />
                <StatCard label="With Outcome" value={stats.sessions_with_outcome.toString()} />
                <StatCard label="With Rollback" value={stats.sessions_with_rollback.toString()} />
                <StatCard label="Avg Token Cost" value={stats.avg_token_cost.toFixed(0)} />
              </div>
              {stats.outcome_distribution && Object.keys(stats.outcome_distribution).length > 0 && (
                <div className="mt-3 flex gap-2 flex-wrap">
                  {Object.entries(stats.outcome_distribution).map(([outcome, count]) => (
                    <span key={outcome} className="bg-slate-800 text-slate-300 text-xs px-2 py-1 rounded">
                      {outcome}: {count as number}
                    </span>
                  ))}
                </div>
              )}
            </section>
          )}

          {/* ── P10: Provider Effectiveness ── */}
          <section>
            <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3 flex items-center gap-1.5">
              <BarChart2 size={14} /> Provider Effectiveness
            </h2>
            {providerStats.length === 0 ? (
              <p className="text-sm text-slate-500">
                No provider stats yet — click "Aggregate Providers" after some retrievals.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-left text-slate-500 border-b border-slate-800">
                      <th className="pb-2 pr-4">Provider</th>
                      <th className="pb-2 pr-3">Category</th>
                      <th className="pb-2 pr-3">Sessions</th>
                      <th className="pb-2 pr-3">Usefulness</th>
                      <th className="pb-2 pr-3">Harmful</th>
                      <th className="pb-2 pr-3">Token Eff.</th>
                      <th className="pb-2 pr-3">Weight</th>
                      <th className="pb-2">Drift</th>
                    </tr>
                  </thead>
                  <tbody>
                    {providerStats.map((p, i) => (
                      <tr key={i} className={`border-b border-slate-900 ${p.drift_flagged ? "bg-red-950/20" : ""}`}>
                        <td className="py-1.5 pr-4 text-slate-200 font-mono">{p.provider_name}</td>
                        <td className="py-1.5 pr-3 text-slate-500">{p.task_category ?? "all"}</td>
                        <td className="py-1.5 pr-3 text-slate-400">{p.total_sessions}</td>
                        <td className="py-1.5 pr-3 text-green-400">{pct(p.usefulness_rate)}</td>
                        <td className="py-1.5 pr-3 text-red-400">{pct(p.harmful_rate)}</td>
                        <td className="py-1.5 pr-3 text-slate-400">{pct(p.avg_token_efficiency)}</td>
                        <td className="py-1.5 pr-3 text-slate-300">{p.weight_current?.toFixed(3)}</td>
                        <td className="py-1.5">
                          {p.drift_flagged ? (
                            <span className="text-red-400 flex items-center gap-1">
                              <AlertTriangle size={10} /> drift
                            </span>
                          ) : (
                            <span className="text-slate-600">ok</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {/* ── P10: Provider Drift Alerts ── */}
          {providerDrift.length > 0 && (
            <section>
              <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3 flex items-center gap-1.5">
                <AlertTriangle size={14} className="text-red-400" /> Provider Drift ({providerDrift.length})
              </h2>
              <div className="space-y-2">
                {providerDrift.map((d, i) => (
                  <div key={i} className="bg-slate-900 border border-red-900/40 rounded-lg p-3">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-mono text-slate-200">{d.provider_name}</span>
                      {d.task_category && (
                        <span className="text-xs bg-slate-800 text-slate-400 px-1.5 py-0.5 rounded">
                          {d.task_category}
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-red-300 mt-1">{d.drift_reason}</p>
                    <div className="mt-1.5 flex gap-4 text-xs text-slate-500">
                      <span>Useful: {pct(d.usefulness_rate)}</span>
                      <span>Harmful: {pct(d.harmful_rate)}</span>
                      <span>Weight: {d.weight_current?.toFixed(3)}</span>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* ── Memory Heatmap ── */}
          {heatmap && (
            <section>
              <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">Memory Heatmap</h2>
              <div className="grid md:grid-cols-2 gap-6">
                <div>
                  <h3 className="text-xs text-slate-500 mb-2 flex items-center gap-1">
                    <Activity size={12} /> Most Retrieved
                  </h3>
                  <div className="space-y-1">
                    {heatmap.most_used.slice(0, 5).map(m => (
                      <div key={m.id} className="bg-slate-900 border border-slate-800 rounded p-2 text-xs">
                        <span className="text-slate-400 mr-2">[{m.layer}]</span>
                        <span className="text-slate-200">{m.content_snippet}</span>
                        <span className="text-slate-500 ml-2">× {m.times_retrieved} | success: {pct(m.success_rate)}</span>
                      </div>
                    ))}
                    {heatmap.most_used.length === 0 && <p className="text-xs text-slate-500">No data yet</p>}
                  </div>
                </div>
                <div>
                  <h3 className="text-xs text-slate-500 mb-2">Rarely Retrieved</h3>
                  <div className="space-y-1">
                    {heatmap.rarely_used.slice(0, 5).map(m => (
                      <div key={m.id} className="bg-slate-900 border border-slate-800 rounded p-2 text-xs">
                        <span className="text-slate-400 mr-2">[{m.layer}]</span>
                        <span className="text-slate-200">{m.content_snippet}</span>
                        <span className="text-slate-500 ml-2">× {m.times_retrieved}</span>
                      </div>
                    ))}
                    {heatmap.rarely_used.length === 0 && <p className="text-xs text-slate-500">No data yet</p>}
                  </div>
                </div>
              </div>
            </section>
          )}

          {/* ── Procedural Effectiveness ── */}
          <section>
            <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">
              <span className="flex items-center gap-1.5"><Zap size={14} /> Procedural Effectiveness</span>
            </h2>
            {procedural.length === 0 ? (
              <p className="text-sm text-slate-500">No procedural memories with retrievals yet.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-left text-slate-500 border-b border-slate-800">
                      <th className="pb-2 pr-4">Content</th>
                      <th className="pb-2 pr-3">Trust</th>
                      <th className="pb-2 pr-3">Success</th>
                      <th className="pb-2 pr-3">Failure</th>
                      <th className="pb-2 pr-3">Retrieved</th>
                      <th className="pb-2 pr-3">Evidence</th>
                      <th className="pb-2">State</th>
                    </tr>
                  </thead>
                  <tbody>
                    {procedural.map(p => (
                      <tr key={p.memory_id} className="border-b border-slate-900">
                        <td className="py-1.5 pr-4 text-slate-300 max-w-xs truncate">{p.content_snippet}</td>
                        <td className="py-1.5 pr-3 text-slate-400">{num(p.trust_score)}</td>
                        <td className="py-1.5 pr-3 text-green-400">{pct(p.success_rate)}</td>
                        <td className="py-1.5 pr-3 text-red-400">{pct(p.failure_rate)}</td>
                        <td className="py-1.5 pr-3 text-slate-400">{p.times_retrieved}</td>
                        <td className="py-1.5 pr-3 text-slate-400">{p.evidence_count}</td>
                        <td className="py-1.5 text-slate-500">{p.memory_state}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {/* ── Confidence Drift ── */}
          <section>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
                <AlertTriangle size={14} className="text-amber-400" /> Confidence Drift ({drift.length})
              </h2>
              {drift.length > 0 && (
                <button
                  onClick={handleDecay}
                  disabled={decaying}
                  className="text-xs bg-amber-700 hover:bg-amber-600 text-white px-2 py-1 rounded disabled:opacity-50"
                >
                  {decaying ? "Applying…" : "Apply Decay"}
                </button>
              )}
            </div>
            {drift.length === 0 ? (
              <p className="text-sm text-slate-500">No drift candidates detected.</p>
            ) : (
              <div className="space-y-2">
                {drift.map(d => (
                  <div key={d.memory_id} className="bg-slate-900 border border-amber-900/40 rounded-lg p-3">
                    <div className="flex items-start justify-between gap-4">
                      <p className="text-xs text-slate-300">{d.content_snippet}</p>
                      <span className="text-xs bg-amber-900/50 text-amber-300 px-1.5 py-0.5 rounded shrink-0">
                        {d.recommended_action}
                      </span>
                    </div>
                    <div className="mt-1.5 flex gap-4 text-xs text-slate-500">
                      <span>Trust: {num(d.trust_score)}</span>
                      <span>Failure: {pct(d.failure_rate)}</span>
                      <span>Recent failure: {pct(d.recent_failure_rate)}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

        </div>
      )}
    </div>
  );
}
