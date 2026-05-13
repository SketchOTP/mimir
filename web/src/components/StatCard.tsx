import clsx from "clsx";

interface Props {
  label: string;
  value: string | number;
  sub?: string;
  color?: "default" | "green" | "yellow" | "red";
}

const COLORS = {
  default: "text-slate-100",
  green: "text-emerald-400",
  yellow: "text-amber-400",
  red: "text-red-400",
};

export default function StatCard({ label, value, sub, color = "default" }: Props) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <p className="text-xs text-slate-500 uppercase tracking-wider">{label}</p>
      <p className={clsx("text-2xl font-bold mt-1", COLORS[color])}>{value}</p>
      {sub && <p className="text-xs text-slate-500 mt-0.5">{sub}</p>}
    </div>
  );
}
