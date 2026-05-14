import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import PageHeader from "../components/PageHeader";
import { getProjects, getProject } from "../lib/api";

interface BootstrapInfo {
  health: "healthy" | "partial" | "missing";
  present_capsule_types: string[];
  missing_capsule_types: string[];
  capsule_count: number;
  last_bootstrap_at: string | null;
}

interface ProjectData {
  project: string;
  memory_count: number;
  counts_by_layer: Record<string, number>;
  bootstrap: BootstrapInfo;
}

function healthColor(h: string) {
  if (h === "healthy") return "text-emerald-400";
  if (h === "partial") return "text-amber-400";
  return "text-red-400";
}

function healthBadge(h: string) {
  const cls = "rounded-full px-2 py-0.5 text-xs font-medium border ";
  if (h === "healthy") return cls + "border-emerald-700/50 bg-emerald-950/40 text-emerald-400";
  if (h === "partial") return cls + "border-amber-700/50 bg-amber-950/40 text-amber-400";
  return cls + "border-red-700/50 bg-red-950/40 text-red-400";
}

function ProjectCard({ p }: { p: ProjectData }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900 p-5">
      <div className="flex items-start justify-between gap-3 mb-3">
        <div>
          <h3 className="font-mono text-base font-semibold text-slate-100">{p.project}</h3>
          <p className="text-xs text-slate-500 mt-0.5">
            {p.memory_count} memories
            {Object.entries(p.counts_by_layer).map(([layer, count]) => (
              <span key={layer} className="ml-2 text-slate-600">
                {layer}: {count}
              </span>
            ))}
          </p>
        </div>
        <span className={healthBadge(p.bootstrap.health)}>
          bootstrap: {p.bootstrap.health}
        </span>
      </div>

      <div className="text-xs text-slate-400 space-y-1">
        <div>
          <span className="text-slate-500">Capsules present:</span>{" "}
          <span className="text-slate-300">
            {p.bootstrap.present_capsule_types.length > 0
              ? p.bootstrap.present_capsule_types.join(", ")
              : "none"}
          </span>
        </div>
        {p.bootstrap.missing_capsule_types.length > 0 && (
          <div>
            <span className="text-amber-500">Missing:</span>{" "}
            <span className="text-amber-300">{p.bootstrap.missing_capsule_types.join(", ")}</span>
          </div>
        )}
        {p.bootstrap.last_bootstrap_at && (
          <div>
            <span className="text-slate-500">Last bootstrap:</span>{" "}
            <span className="text-slate-300">
              {new Date(p.bootstrap.last_bootstrap_at).toLocaleString()}
            </span>
          </div>
        )}
        {p.bootstrap.health !== "healthy" && (
          <div className="mt-2 rounded bg-slate-950 border border-slate-800 px-3 py-2 text-slate-400">
            Run from Cursor:{" "}
            <code className="text-brand-400">
              project_bootstrap(project=&quot;{p.project}&quot;)
            </code>
          </div>
        )}
      </div>
    </div>
  );
}

export function ProjectsPage() {
  const [projects, setProjects] = useState<ProjectData[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    getProjects()
      .then((r) => setProjects(r.data?.projects ?? []))
      .catch(() => setError("Could not load projects. Check auth."))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <PageHeader
        title="Projects"
        subtitle="Per-repo memory profiles — each project's memories are isolated by slug"
      />
      <div className="p-6 space-y-4">
        <div className="rounded-lg border border-slate-800 bg-slate-900/50 px-4 py-3 text-sm text-slate-400">
          Memories are scoped by <code className="text-brand-400">user + project</code>. Each
          repo gets its own slug (e.g. <code className="text-brand-400">auto</code>,{" "}
          <code className="text-brand-400">mimir</code>). Queries without a project
          only search across all your projects when{" "}
          <code className="text-brand-400">global=true</code>.
        </div>

        {error && (
          <div className="rounded-lg border border-amber-700/50 bg-amber-950/30 px-4 py-3 text-sm text-amber-200">
            {error}
          </div>
        )}

        {loading ? (
          <div className="text-sm text-slate-500 px-1">Loading projects…</div>
        ) : projects.length === 0 ? (
          <div className="rounded-lg border border-slate-800 bg-slate-900 p-6 text-center">
            <p className="text-slate-400 mb-2">No projects bootstrapped yet.</p>
            <p className="text-sm text-slate-500">
              From Cursor, call:{" "}
              <code className="text-brand-400">
                project_bootstrap(project=&quot;myproject&quot;, repo_path=&quot;/path/to/repo&quot;)
              </code>
            </p>
          </div>
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            {projects.map((p) => (
              <ProjectCard key={p.project} p={p} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export function ProjectDetailPage() {
  const { slug } = useParams<{ slug: string }>();
  const [project, setProject] = useState<ProjectData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!slug) return;
    getProject(slug)
      .then((r) => setProject(r.data))
      .catch(() => setError(`Could not load project: ${slug}`))
      .finally(() => setLoading(false));
  }, [slug]);

  return (
    <div>
      <PageHeader title={`Project: ${slug}`} subtitle="Memory profile and bootstrap health" />
      <div className="p-6">
        {error && (
          <div className="rounded-lg border border-amber-700/50 bg-amber-950/30 px-4 py-3 text-sm text-amber-200">
            {error}
          </div>
        )}
        {loading && <div className="text-sm text-slate-500">Loading…</div>}
        {project && <ProjectCard p={project} />}
      </div>
    </div>
  );
}
