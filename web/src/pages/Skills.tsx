import { useEffect, useState } from "react";
import { listSkills, runSkill, testSkill } from "../lib/api";
import PageHeader from "../components/PageHeader";
import Badge from "../components/Badge";
import { Play, FlaskConical } from "lucide-react";

export default function Skills() {
  const [skills, setSkills] = useState<any[]>([]);
  const [status, setStatus] = useState("");
  const [runResult, setRunResult] = useState<Record<string, any>>({});

  const load = async () => {
    const r = await listSkills({ status: status || undefined });
    setSkills(r.data.skills);
  };

  useEffect(() => { load(); }, [status]);

  const handleRun = async (id: string) => {
    const r = await runSkill(id);
    setRunResult(prev => ({ ...prev, [id]: r.data }));
  };

  const handleTest = async (id: string) => {
    const r = await testSkill(id);
    setRunResult(prev => ({ ...prev, [id]: r.data }));
  };

  return (
    <div>
      <PageHeader title="Skills" subtitle={`${skills.length} skills in catalog`} />
      <div className="p-6 space-y-4">
        <select value={status} onChange={e => setStatus(e.target.value)}
          className="bg-slate-800 border border-slate-700 rounded-md px-3 py-1.5 text-sm">
          <option value="">All statuses</option>
          <option value="draft">Draft</option>
          <option value="active">Active</option>
          <option value="deprecated">Deprecated</option>
        </select>

        <div className="space-y-3">
          {skills.map(s => (
            <div key={s.id} className="bg-slate-900 border border-slate-800 rounded-lg p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-medium text-sm">{s.name}</span>
                    <Badge value={s.status} />
                    <span className="text-xs text-slate-500">v{s.version}</span>
                  </div>
                  <p className="text-xs text-slate-400">{s.purpose}</p>
                  <div className="flex gap-4 mt-2 text-xs text-slate-500">
                    <span>✓ {s.success_count}</span>
                    <span>✗ {s.failure_count}</span>
                    <span className="text-slate-600">{s.id}</span>
                  </div>
                  {runResult[s.id] && (
                    <pre className="mt-2 text-xs bg-slate-950 rounded p-2 text-slate-300 overflow-x-auto">
                      {JSON.stringify(runResult[s.id], null, 2)}
                    </pre>
                  )}
                </div>
                <div className="flex gap-2">
                  <button onClick={() => handleTest(s.id)}
                    className="flex items-center gap-1 text-xs bg-slate-800 hover:bg-slate-700 px-2.5 py-1.5 rounded-md">
                    <FlaskConical size={12} /> Test
                  </button>
                  <button onClick={() => handleRun(s.id)}
                    className="flex items-center gap-1 text-xs bg-brand-600 hover:bg-brand-700 px-2.5 py-1.5 rounded-md">
                    <Play size={12} /> Run
                  </button>
                </div>
              </div>
            </div>
          ))}
          {skills.length === 0 && <p className="text-sm text-slate-500 text-center py-8">No skills found.</p>}
        </div>
      </div>
    </div>
  );
}
