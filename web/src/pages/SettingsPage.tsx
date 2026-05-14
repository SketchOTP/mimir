import { useEffect, useState } from "react";
import PageHeader from "../components/PageHeader";
import { getConnectionOnboarding } from "../lib/api";
import { ExternalLink, Copy, Check } from "lucide-react";

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={() => {
        navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      }}
      className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-slate-100 transition-colors"
    >
      {copied ? <Check size={12} className="text-emerald-400" /> : <Copy size={12} />}
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

export default function SettingsPage() {
  const [onboarding, setOnboarding] = useState<any | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getConnectionOnboarding()
      .then(r => setOnboarding(r.data))
      .catch(() => setOnboarding(null))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <PageHeader
        title="Settings"
        subtitle="Connection, API keys, and MCP configuration"
      />
      <div className="p-6 space-y-5">

        {/* Connection overview */}
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-5">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-semibold text-slate-100">Connection</h2>
            {onboarding && (
              <span className="text-xs text-slate-500">
                Mode: <span className="text-slate-300">{onboarding.auth_mode}</span>
              </span>
            )}
          </div>

          {loading && <p className="text-xs text-slate-500">Loading…</p>}

          {!loading && onboarding && (
            <div className="space-y-4">
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="rounded-md border border-slate-800 bg-slate-950 p-3">
                  <p className="text-xs font-medium text-slate-400 mb-1">MCP endpoint</p>
                  <div className="flex items-center justify-between gap-2">
                    <code className="text-xs text-slate-300 break-all">{onboarding.urls.mcp_url}</code>
                    <CopyButton text={onboarding.urls.mcp_url} />
                  </div>
                </div>
                <div className="rounded-md border border-slate-800 bg-slate-950 p-3">
                  <p className="text-xs font-medium text-slate-400 mb-1">Auth mode</p>
                  <code className="text-xs text-slate-300">{onboarding.auth_mode}</code>
                </div>
              </div>

              {onboarding.generated.oauth_local && (
                <div>
                  <div className="flex items-center justify-between mb-1">
                    <p className="text-xs font-medium text-slate-400">Local MCP config</p>
                    <CopyButton text={onboarding.generated.oauth_local} />
                  </div>
                  <pre className="bg-slate-950 rounded-md p-3 text-xs text-slate-300 overflow-x-auto border border-slate-800">
                    {onboarding.generated.oauth_local}
                  </pre>
                </div>
              )}

              {onboarding.generated.api_key_remote && (
                <div>
                  <div className="flex items-center justify-between mb-1">
                    <p className="text-xs font-medium text-slate-400">SSH / Remote MCP config</p>
                    <CopyButton text={onboarding.generated.api_key_remote} />
                  </div>
                  <pre className="bg-slate-950 rounded-md p-3 text-xs text-slate-300 overflow-x-auto border border-slate-800">
                    {onboarding.generated.api_key_remote}
                  </pre>
                </div>
              )}

              <div className="flex gap-3 pt-1">
                <a
                  href={onboarding.urls.connection_settings}
                  className="inline-flex items-center gap-1.5 text-xs text-brand-400 hover:text-brand-300"
                >
                  <ExternalLink size={12} />
                  Advanced connection settings
                </a>
                {!onboarding.owner_exists && (
                  <a
                    href={onboarding.urls.first_run_setup}
                    className="inline-flex items-center gap-1.5 text-xs text-brand-400 hover:text-brand-300"
                  >
                    <ExternalLink size={12} />
                    First-run setup
                  </a>
                )}
              </div>
            </div>
          )}

          {!loading && !onboarding && (
            <p className="text-xs text-slate-500">
              Could not load connection info.{" "}
              <a href="/settings/connection" className="text-brand-400 hover:underline">Open connection settings</a>
            </p>
          )}
        </div>

        {/* Advanced features */}
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-5">
          <h2 className="text-sm font-semibold text-slate-100 mb-3">Advanced</h2>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3 text-xs">
            {[
              { label: "Telemetry", href: "/telemetry" },
              { label: "Reflections", href: "/reflections" },
              { label: "Improvements", href: "/improvements" },
              { label: "Rollbacks", href: "/rollbacks" },
              { label: "Timeline", href: "/timeline" },
              { label: "Simulation", href: "/simulation" },
            ].map(({ label, href }) => (
              <a
                key={href}
                href={href}
                className="flex items-center gap-1.5 text-slate-400 hover:text-slate-100 transition-colors"
              >
                <ExternalLink size={11} />
                {label}
              </a>
            ))}
          </div>
        </div>

      </div>
    </div>
  );
}
