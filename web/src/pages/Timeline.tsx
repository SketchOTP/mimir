import { useEffect, useState } from "react";
import { listMemories } from "../lib/api";
import PageHeader from "../components/PageHeader";
import Badge from "../components/Badge";
import { formatDistanceToNow } from "date-fns";

export default function Timeline() {
  const [events, setEvents] = useState<any[]>([]);

  useEffect(() => {
    listMemories({ layer: "episodic", limit: 100 }).then(r => setEvents(r.data.memories));
  }, []);

  return (
    <div>
      <PageHeader title="Timeline" subtitle="Event history (episodic memory)" />
      <div className="p-6">
        <div className="relative">
          <div className="absolute left-4 top-0 bottom-0 w-px bg-slate-800" />
          <div className="space-y-4">
            {events.map(e => (
              <div key={e.id} className="flex gap-4 pl-10 relative">
                <div className="absolute left-2.5 top-2 w-3 h-3 rounded-full bg-brand-600 border-2 border-slate-950" />
                <div className="flex-1 bg-slate-900 border border-slate-800 rounded-lg p-3">
                  <div className="flex items-center gap-2 mb-1">
                    <Badge value={e.layer} />
                    <span className="text-xs text-slate-500">
                      {e.created_at ? formatDistanceToNow(new Date(e.created_at), { addSuffix: true }) : ""}
                    </span>
                  </div>
                  <p className="text-sm text-slate-200">{e.content}</p>
                </div>
              </div>
            ))}
            {events.length === 0 && (
              <p className="text-sm text-slate-500 text-center py-8 pl-6">No episodic events recorded yet.</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
