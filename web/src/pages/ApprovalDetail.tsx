import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { getApproval, approveRequest, rejectRequest } from "../lib/api";
import Badge from "../components/Badge";
import { Check, X, ArrowLeft, AlertCircle, Loader2 } from "lucide-react";

type Approval = {
  id: string;
  title: string;
  status: string;
  summary: Record<string, string>;
  reviewer_note?: string;
  decided_at?: string;
  created_at: string;
  expires_at?: string;
};

export default function ApprovalDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [approval, setApproval] = useState<Approval | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState<"approve" | "reject" | null>(null);
  const [note, setNote] = useState("");
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (!id) return;
    getApproval(id)
      .then(r => setApproval(r.data))
      .catch(e => { if (e?.response?.status === 404) setNotFound(true); })
      .finally(() => setLoading(false));
  }, [id]);

  const handle = async (action: "approve" | "reject") => {
    if (!approval || !id) return;
    setActing(action);
    try {
      const fn = action === "approve" ? approveRequest : rejectRequest;
      const r = await fn(id, note || undefined);
      setApproval(r.data);
      setDone(true);
    } finally {
      setActing(null);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-slate-950">
        <Loader2 className="animate-spin text-brand-400" size={32} />
      </div>
    );
  }

  if (notFound || !approval) {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen bg-slate-950 gap-4 p-6">
        <AlertCircle className="text-red-400" size={40} />
        <p className="text-lg font-medium text-slate-200">Approval not found</p>
        <p className="text-sm text-slate-500">This approval may have expired or the link is invalid.</p>
        <button onClick={() => navigate("/approvals")}
          className="mt-2 text-sm text-brand-400 hover:underline flex items-center gap-1.5">
          <ArrowLeft size={14} /> Back to approvals
        </button>
      </div>
    );
  }

  const s = approval.summary || {};
  const isPending = approval.status === "pending" && !done;

  return (
    <div className="min-h-screen bg-slate-950 p-4 md:p-8">
      <button onClick={() => navigate("/approvals")}
        className="flex items-center gap-1.5 text-sm text-slate-400 hover:text-slate-200 mb-6">
        <ArrowLeft size={14} /> Back to approvals
      </button>

      <div className="max-w-xl mx-auto space-y-5">
        {/* Header */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
          <div className="flex items-center gap-2 mb-2 flex-wrap">
            <span className="font-semibold text-base text-slate-100">{approval.title}</span>
            <Badge value={approval.status} />
            {s.risk && <Badge value={s.risk} />}
          </div>
          {s.reason && <p className="text-sm text-slate-400">{s.reason}</p>}
        </div>

        {/* Behavior comparison */}
        {(s.current_behavior || s.proposed_behavior) && (
          <div className="grid grid-cols-1 gap-3">
            {s.current_behavior && (
              <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
                <p className="text-xs text-slate-500 mb-1">Current behavior</p>
                <p className="text-sm text-slate-300">{s.current_behavior}</p>
              </div>
            )}
            {s.proposed_behavior && (
              <div className="bg-slate-900 border border-emerald-800/40 rounded-lg p-4">
                <p className="text-xs text-slate-500 mb-1">Proposed behavior</p>
                <p className="text-sm text-emerald-300">{s.proposed_behavior}</p>
              </div>
            )}
          </div>
        )}

        {/* Expected benefit */}
        {s.expected_benefit && (
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
            <p className="text-xs text-slate-500 mb-1">Expected benefit</p>
            <p className="text-sm text-slate-300">{s.expected_benefit}</p>
          </div>
        )}

        {/* Decided state */}
        {!isPending && (
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 text-center">
            <p className="text-sm font-medium capitalize" data-testid="final-status">
              {approval.status === "approved"
                ? <span className="text-emerald-400">Approved</span>
                : approval.status === "rejected"
                ? <span className="text-red-400">Rejected</span>
                : <span className="text-slate-400">{approval.status}</span>}
            </p>
            {approval.reviewer_note && (
              <p className="text-xs text-slate-500 mt-1">Note: {approval.reviewer_note}</p>
            )}
          </div>
        )}

        {/* Action panel */}
        {isPending && (
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-4">
            <input
              placeholder="Optional note..."
              value={note}
              onChange={e => setNote(e.target.value)}
              className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-brand-500 placeholder:text-slate-600"
            />
            <div className="grid grid-cols-2 gap-3">
              <button
                onClick={() => handle("approve")}
                disabled={!!acting}
                className="flex items-center justify-center gap-2 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white font-medium py-3 rounded-xl transition-colors"
              >
                {acting === "approve"
                  ? <Loader2 className="animate-spin" size={16} />
                  : <Check size={16} />}
                Approve
              </button>
              <button
                onClick={() => handle("reject")}
                disabled={!!acting}
                className="flex items-center justify-center gap-2 bg-red-600 hover:bg-red-700 disabled:opacity-50 text-white font-medium py-3 rounded-xl transition-colors"
              >
                {acting === "reject"
                  ? <Loader2 className="animate-spin" size={16} />
                  : <X size={16} />}
                Reject
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
