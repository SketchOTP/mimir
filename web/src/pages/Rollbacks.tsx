import { useEffect, useState } from "react";
import axios from "axios";
import PageHeader from "../components/PageHeader";

export default function Rollbacks() {
  const [rollbacks, setRollbacks] = useState<any[]>([]);

  useEffect(() => {
    axios.get("/api/dashboard").then(r => setRollbacks(r.data.recent_rollbacks || []));
  }, []);

  return (
    <div>
      <PageHeader title="Rollbacks" subtitle="Automatic rollback history" />
      <div className="p-6">
        {rollbacks.length === 0 ? (
          <p className="text-sm text-slate-500 text-center py-8">No rollbacks recorded. System is stable.</p>
        ) : (
          <div className="space-y-3">
            {rollbacks.map((r: any) => (
              <div key={r.id} className="bg-slate-900 border border-slate-800 rounded-lg p-4">
                <div className="flex items-start gap-3">
                  <span className="text-red-400 text-lg">⊘</span>
                  <div>
                    <p className="text-sm font-medium">{r.target_id}</p>
                    <p className="text-xs text-slate-400 mt-0.5">{r.reason}</p>
                    <p className="text-xs text-slate-600 mt-1">{r.created_at?.slice(0, 16)}</p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
