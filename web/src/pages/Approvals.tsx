import { useEffect, useState } from "react";
import { listApprovals, approveRequest, rejectRequest } from "../lib/api";
import PageHeader from "../components/PageHeader";
import Badge from "../components/Badge";
import { Check, X } from "lucide-react";

export default function Approvals() {
  const [approvals, setApprovals] = useState<any[]>([]);
  const [filter, setFilter] = useState("pending");
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [acting, setActing] = useState<string | null>(null);

  const load = async () => {
    const r = await listApprovals({ status: filter || undefined });
    setApprovals(r.data.approvals);
  };

  useEffect(() => { load(); }, [filter]);

  const handleApprove = async (id: string) => {
    setActing(id);
    try { await approveRequest(id, notes[id]); await load(); }
    finally { setActing(null); }
  };

  const handleReject = async (id: string) => {
    setActing(id);
    try { await rejectRequest(id, notes[id]); await load(); }
    finally { setActing(null); }
  };

  return (
    <div>
      <PageHeader title="Approvals" subtitle="Pending human decisions" />
      <div className="p-6 space-y-4">
        <select value={filter} onChange={e => setFilter(e.target.value)}
          className="bg-slate-800 border border-slate-700 rounded-md px-3 py-1.5 text-sm">
          <option value="pending">Pending</option>
          <option value="approved">Approved</option>
          <option value="rejected">Rejected</option>
          <option value="">All</option>
        </select>

        <div className="space-y-4">
          {approvals.map(a => {
            const s = a.summary || {};
            return (
              <div key={a.id} className="bg-slate-900 border border-slate-800 rounded-lg p-5">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-2">
                      <span className="font-medium">{a.title}</span>
                      <Badge value={a.status} />
                      <Badge value={s.risk ?? "low"} />
                    </div>
                    <p className="text-sm text-slate-400 mb-3">{s.reason}</p>
                    <div className="grid md:grid-cols-2 gap-3 text-sm mb-3">
                      <div className="bg-slate-950 rounded p-2">
                        <p className="text-xs text-slate-500 mb-1">Current behavior</p>
                        <p className="text-slate-300">{s.current_behavior}</p>
                      </div>
                      <div className="bg-slate-950 rounded p-2">
                        <p className="text-xs text-slate-500 mb-1">Proposed behavior</p>
                        <p className="text-emerald-300">{s.proposed_behavior}</p>
                      </div>
                    </div>
                    {s.expected_benefit && (
                      <p className="text-xs text-slate-500">
                        <span className="text-slate-400">Expected benefit: </span>{s.expected_benefit}
                      </p>
                    )}
                  </div>
                </div>

                {a.status === "pending" && (
                  <div className="mt-4 flex items-center gap-3 border-t border-slate-800 pt-4">
                    <input
                      placeholder="Optional note..."
                      value={notes[a.id] || ""}
                      onChange={e => setNotes(n => ({ ...n, [a.id]: e.target.value }))}
                      className="flex-1 bg-slate-800 border border-slate-700 rounded px-3 py-1.5 text-sm focus:outline-none"
                    />
                    <button onClick={() => handleApprove(a.id)} disabled={acting === a.id}
                      className="flex items-center gap-1.5 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white text-sm px-3 py-1.5 rounded-md">
                      <Check size={14} /> Approve
                    </button>
                    <button onClick={() => handleReject(a.id)} disabled={acting === a.id}
                      className="flex items-center gap-1.5 bg-red-600 hover:bg-red-700 disabled:opacity-50 text-white text-sm px-3 py-1.5 rounded-md">
                      <X size={14} /> Reject
                    </button>
                  </div>
                )}
              </div>
            );
          })}
          {approvals.length === 0 && (
            <p className="text-sm text-slate-500 text-center py-8">
              {filter === "pending" ? "No pending approvals. System is up to date." : "No approvals found."}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
