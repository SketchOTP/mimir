import { useEffect, useState } from "react";
import { listImprovements, createApproval } from "../lib/api";
import PageHeader from "../components/PageHeader";
import Badge from "../components/Badge";
import { SendHorizontal } from "lucide-react";

export default function Improvements() {
  const [items, setItems] = useState<any[]>([]);
  const [status, setStatus] = useState("");
  const [sending, setSending] = useState<string | null>(null);

  const load = async () => {
    const r = await listImprovements({ status: status || undefined });
    setItems(r.data.improvements);
  };

  useEffect(() => { load(); }, [status]);

  const handleRequestApproval = async (id: string) => {
    setSending(id);
    try { await createApproval(id); await load(); }
    finally { setSending(null); }
  };

  return (
    <div>
      <PageHeader title="Improvements" subtitle="Proposed system changes" />
      <div className="p-6 space-y-4">
        <select value={status} onChange={e => setStatus(e.target.value)}
          className="bg-slate-800 border border-slate-700 rounded-md px-3 py-1.5 text-sm">
          <option value="">All statuses</option>
          <option value="proposed">Proposed</option>
          <option value="pending_approval">Pending Approval</option>
          <option value="approved">Approved</option>
          <option value="promoted">Promoted</option>
          <option value="rejected">Rejected</option>
        </select>

        <div className="space-y-3">
          {items.map(item => (
            <div key={item.id} className="bg-slate-900 border border-slate-800 rounded-lg p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-medium text-sm">{item.title}</span>
                    <Badge value={item.status} />
                    <Badge value={item.risk} />
                  </div>
                  <p className="text-xs text-slate-500 mb-2">{item.reason}</p>
                  <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                    <div>
                      <span className="text-slate-500">Current: </span>
                      <span className="text-slate-300">{item.current_behavior}</span>
                    </div>
                    <div>
                      <span className="text-slate-500">Proposed: </span>
                      <span className="text-emerald-300">{item.proposed_behavior}</span>
                    </div>
                  </div>
                </div>
                {item.status === "proposed" && (
                  <button
                    onClick={() => handleRequestApproval(item.id)}
                    disabled={sending === item.id}
                    className="flex items-center gap-1 text-xs bg-brand-600 hover:bg-brand-700 disabled:opacity-50 px-2.5 py-1.5 rounded-md shrink-0">
                    <SendHorizontal size={12} />
                    Request Approval
                  </button>
                )}
              </div>
            </div>
          ))}
          {items.length === 0 && <p className="text-sm text-slate-500 text-center py-8">No improvements found.</p>}
        </div>
      </div>
    </div>
  );
}
