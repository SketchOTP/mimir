import { useEffect, useState } from "react";
import { listMemories, deleteMemory, createMemory, recall } from "../lib/api";
import PageHeader from "../components/PageHeader";
import Badge from "../components/Badge";
import { Trash2, Plus, Search } from "lucide-react";

interface Memory {
  id: string; layer: string; content: string; importance: number;
  access_count: number; created_at: string; project?: string;
  memory_state?: string; verification_status?: string; trust_score?: number;
  poisoning_flags?: string[]; quarantine_reason?: string;
}

export default function Memories() {
  const [mems, setMems] = useState<Memory[]>([]);
  const [layer, setLayer] = useState("");
  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState<Memory[] | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({ content: "", layer: "semantic", importance: 0.7 });

  const load = async () => {
    const r = await listMemories({ layer: layer || undefined, limit: 100 });
    setMems(r.data.memories);
  };

  useEffect(() => { load(); }, [layer]);

  const handleDelete = async (id: string) => {
    if (!confirm("Delete this memory?")) return;
    await deleteMemory(id);
    load();
  };

  const handleCreate = async () => {
    await createMemory(form);
    setShowCreate(false);
    load();
  };

  const handleSearch = async () => {
    if (!query) { setSearchResults(null); return; }
    const r = await recall({ query, limit: 20 });
    setSearchResults(r.data.hits?.map((h: any) => ({ ...h })) ?? []);
  };

  const display = searchResults ?? mems;

  return (
    <div>
      <PageHeader
        title="Memories"
        subtitle={`${mems.length} memories stored`}
        action={
          <button onClick={() => setShowCreate(true)}
            className="flex items-center gap-1.5 bg-brand-600 hover:bg-brand-700 text-white text-sm px-3 py-1.5 rounded-md transition-colors">
            <Plus size={14} /> Add Memory
          </button>
        }
      />
      <div className="p-6 space-y-4">
        {/* Filters */}
        <div className="flex gap-3">
          <div className="flex gap-2 flex-1">
            <input
              placeholder="Search memories..."
              value={query}
              onChange={e => setQuery(e.target.value)}
              onKeyDown={e => e.key === "Enter" && handleSearch()}
              className="flex-1 bg-slate-800 border border-slate-700 rounded-md px-3 py-1.5 text-sm focus:outline-none focus:border-brand-500"
            />
            <button onClick={handleSearch} className="bg-slate-800 border border-slate-700 px-3 rounded-md hover:border-slate-500">
              <Search size={14} />
            </button>
          </div>
          <select value={layer} onChange={e => setLayer(e.target.value)}
            className="bg-slate-800 border border-slate-700 rounded-md px-3 py-1.5 text-sm focus:outline-none">
            <option value="">All layers</option>
            <option value="episodic">Episodic</option>
            <option value="semantic">Semantic</option>
            <option value="procedural">Procedural</option>
          </select>
        </div>

        {/* Create form */}
        {showCreate && (
          <div className="bg-slate-900 border border-slate-700 rounded-lg p-4 space-y-3">
            <h3 className="text-sm font-medium">New Memory</h3>
            <textarea value={form.content} onChange={e => setForm({ ...form, content: e.target.value })}
              placeholder="Memory content..." rows={3}
              className="w-full bg-slate-800 border border-slate-700 rounded-md px-3 py-2 text-sm focus:outline-none resize-none" />
            <div className="flex gap-3">
              <select value={form.layer} onChange={e => setForm({ ...form, layer: e.target.value })}
                className="bg-slate-800 border border-slate-700 rounded-md px-2 py-1 text-sm">
                <option value="semantic">Semantic</option>
                <option value="episodic">Episodic</option>
                <option value="procedural">Procedural</option>
              </select>
              <input type="number" min={0} max={1} step={0.1} value={form.importance}
                onChange={e => setForm({ ...form, importance: parseFloat(e.target.value) })}
                className="w-24 bg-slate-800 border border-slate-700 rounded-md px-2 py-1 text-sm" />
              <button onClick={handleCreate} className="bg-brand-600 hover:bg-brand-700 px-3 py-1 rounded text-sm ml-auto">Save</button>
              <button onClick={() => setShowCreate(false)} className="text-slate-400 hover:text-slate-100 px-2 py-1 rounded text-sm">Cancel</button>
            </div>
          </div>
        )}

        {/* List */}
        <div className="space-y-2">
          {display.map((m: any) => (
            <div key={m.id} className="bg-slate-900 border border-slate-800 rounded-lg px-4 py-3 flex gap-3 items-start group">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1">
                  <Badge value={m.layer} />
                  <span className="text-xs text-slate-500">imp: {m.importance?.toFixed(1)}</span>
                  {m.memory_state && <span className="text-xs text-amber-300">state: {m.memory_state}</span>}
                  {typeof m.trust_score === "number" && <span className="text-xs text-slate-500">trust: {m.trust_score.toFixed(2)}</span>}
                  {m.verification_status && <span className="text-xs text-slate-500">{m.verification_status}</span>}
                  {m.score && <span className="text-xs text-brand-400">score: {m.score.toFixed(2)}</span>}
                </div>
                <p className="text-sm text-slate-200">{m.content}</p>
                {Array.isArray(m.poisoning_flags) && m.poisoning_flags.length > 0 && (
                  <p className="text-xs text-amber-200 mt-2">
                    flags: {m.poisoning_flags.join(", ")}
                  </p>
                )}
                {m.quarantine_reason && (
                  <p className="text-xs text-amber-100/90 mt-1">
                    reason: {m.quarantine_reason}
                  </p>
                )}
                <p className="text-xs text-slate-600 mt-1">{m.id} · {m.created_at?.slice(0, 10)}</p>
              </div>
              <button onClick={() => handleDelete(m.id)}
                className="opacity-0 group-hover:opacity-100 text-slate-600 hover:text-red-400 transition-all">
                <Trash2 size={14} />
              </button>
            </div>
          ))}
          {display.length === 0 && <p className="text-sm text-slate-500 text-center py-8">No memories found.</p>}
        </div>
      </div>
    </div>
  );
}
