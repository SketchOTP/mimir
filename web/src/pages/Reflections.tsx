import { useEffect, useState } from "react";
import { listReflections, generateReflection } from "../lib/api";
import PageHeader from "../components/PageHeader";
import { RefreshCw } from "lucide-react";

export default function Reflections() {
  const [refs, setRefs] = useState<any[]>([]);
  const [generating, setGenerating] = useState(false);

  const load = async () => {
    const r = await listReflections({ limit: 20 });
    setRefs(r.data.reflections);
  };

  useEffect(() => { load(); }, []);

  const handleGenerate = async () => {
    setGenerating(true);
    try { await generateReflection(); await load(); }
    finally { setGenerating(false); }
  };

  return (
    <div>
      <PageHeader
        title="Reflections"
        subtitle="Lessons learned and system observations"
        action={
          <button onClick={handleGenerate} disabled={generating}
            className="flex items-center gap-1.5 bg-brand-600 hover:bg-brand-700 disabled:opacity-50 text-white text-sm px-3 py-1.5 rounded-md">
            <RefreshCw size={14} className={generating ? "animate-spin" : ""} />
            Generate
          </button>
        }
      />
      <div className="p-6 space-y-4">
        {refs.map(ref => (
          <div key={ref.id} className="bg-slate-900 border border-slate-800 rounded-lg p-4">
            <div className="flex items-center gap-2 mb-3">
              <span className="text-xs bg-slate-800 text-slate-400 px-2 py-0.5 rounded">{ref.trigger}</span>
              <span className="text-xs text-slate-500">{ref.created_at?.slice(0, 16)}</span>
            </div>
            <div className="space-y-3">
              {ref.observations?.length > 0 && (
                <div>
                  <p className="text-xs text-slate-500 uppercase tracking-wider mb-1">Observations</p>
                  <ul className="space-y-1">
                    {ref.observations.map((o: string, i: number) => (
                      <li key={i} className="text-sm text-slate-300 flex gap-2">
                        <span className="text-slate-500">·</span>{o}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {ref.lessons?.length > 0 && (
                <div>
                  <p className="text-xs text-slate-500 uppercase tracking-wider mb-1">Lessons</p>
                  <ul className="space-y-1">
                    {ref.lessons.map((l: string, i: number) => (
                      <li key={i} className="text-sm text-emerald-300 flex gap-2">
                        <span className="text-emerald-600">✓</span>{l}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {ref.proposed_improvements?.length > 0 && (
                <div>
                  <p className="text-xs text-slate-500 uppercase tracking-wider mb-1">Proposed Actions</p>
                  {ref.proposed_improvements.map((p: any, i: number) => (
                    <div key={i} className="text-xs text-amber-300 bg-amber-900/20 rounded px-2 py-1 mt-1">
                      {p.type}: {p.reason}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
        {refs.length === 0 && <p className="text-sm text-slate-500 text-center py-8">No reflections yet. Click Generate to create one.</p>}
      </div>
    </div>
  );
}
