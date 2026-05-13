import clsx from "clsx";

const VARIANTS: Record<string, string> = {
  episodic: "bg-blue-900/40 text-blue-300 border-blue-800",
  semantic: "bg-purple-900/40 text-purple-300 border-purple-800",
  procedural: "bg-orange-900/40 text-orange-300 border-orange-800",
  working: "bg-slate-800 text-slate-300 border-slate-700",
  active: "bg-emerald-900/40 text-emerald-300 border-emerald-800",
  draft: "bg-slate-800 text-slate-400 border-slate-700",
  pending: "bg-amber-900/40 text-amber-300 border-amber-800",
  approved: "bg-emerald-900/40 text-emerald-300 border-emerald-800",
  rejected: "bg-red-900/40 text-red-300 border-red-800",
  proposed: "bg-blue-900/40 text-blue-300 border-blue-800",
  promoted: "bg-emerald-900/40 text-emerald-300 border-emerald-800",
  rolled_back: "bg-red-900/40 text-red-300 border-red-800",
  low: "bg-emerald-900/30 text-emerald-400",
  medium: "bg-amber-900/30 text-amber-400",
  high: "bg-red-900/30 text-red-400",
};

export default function Badge({ value }: { value: string }) {
  return (
    <span className={clsx("text-xs px-2 py-0.5 rounded border", VARIANTS[value] ?? "bg-slate-800 text-slate-400 border-slate-700")}>
      {value}
    </span>
  );
}
