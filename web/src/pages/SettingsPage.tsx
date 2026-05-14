import PageHeader from "../components/PageHeader";

export default function SettingsPage() {
  return (
    <div>
      <PageHeader
        title="Settings"
        subtitle="Connection profile, MCP setup, and API key management"
      />
      <div className="p-6 space-y-6">
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-5">
          <h2 className="text-lg font-semibold text-slate-100">Connection Setup</h2>
          <p className="text-sm text-slate-400 mt-2 max-w-2xl">
            Open the dedicated browser connection page to choose your use case, save SSH or hosted settings,
            generate the matching Cursor MCP JSON, create a new API key, and revoke older keys.
          </p>
          <div className="mt-4 flex flex-wrap gap-3">
            <a
              href="/settings/connection"
              className="inline-flex items-center rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500"
            >
              Open Connection Settings
            </a>
            <a
              href="/setup"
              className="inline-flex items-center rounded-md border border-slate-700 px-4 py-2 text-sm font-medium text-slate-200 hover:border-slate-500 hover:text-white"
            >
              Open First-Run Setup
            </a>
          </div>
        </div>

        <div className="grid gap-6 lg:grid-cols-2">
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
            <h3 className="text-sm font-medium mb-3">Recommended Paths</h3>
            <div className="space-y-2 text-sm text-slate-300">
              <p><span className="text-brand-400">Local browser:</span> OAuth is fine when Cursor can open the Mimir browser page directly.</p>
              <p><span className="text-brand-400">SSH / remote dev / headless:</span> Use an API key. Device-code auth is not implemented yet.</p>
              <p><span className="text-brand-400">LAN / hosted:</span> Set a reachable <code className="text-slate-100">MIMIR_PUBLIC_URL</code> before copying MCP config.</p>
            </div>
          </div>

          <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
            <h3 className="text-sm font-medium mb-3">What The Browser Page Covers</h3>
            <div className="space-y-2 text-sm text-slate-300">
              <p>Connection type: local, SSH, remote dev, headless, LAN, hosted HTTPS, and RPi5.</p>
              <p>Saved fields: public URL, SSH alias, remote paths, Cursor MCP path, Python path, and notes.</p>
              <p>MCP output: generated JSON blocks for local Cursor, SSH, LAN, and hosted deployments.</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
