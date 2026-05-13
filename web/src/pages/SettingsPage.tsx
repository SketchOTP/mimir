import PageHeader from "../components/PageHeader";

export default function SettingsPage() {
  return (
    <div>
      <PageHeader title="Settings" subtitle="Permissions, retention, token limits" />
      <div className="p-6 space-y-6">
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
          <h2 className="text-sm font-medium mb-3">API Configuration</h2>
          <div className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-slate-400">API URL</span>
              <span className="text-slate-200 font-mono">http://localhost:8787</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">Docs</span>
              <a href="/api/docs" target="_blank" className="text-brand-400 hover:underline">/api/docs</a>
            </div>
          </div>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
          <h2 className="text-sm font-medium mb-3">MCP Integration</h2>
          <pre className="text-xs bg-slate-950 rounded p-3 text-slate-300 overflow-x-auto">{`{
  "mcpServers": {
    "mimir": {
      "command": "python",
      "args": ["-m", "mcp.server"],
      "env": {
        "MIMIR_URL": "http://127.0.0.1:8787",
        "MIMIR_API_KEY": "local-dev-key"
      }
    }
  }
}`}</pre>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
          <h2 className="text-sm font-medium mb-3">Python SDK</h2>
          <pre className="text-xs bg-slate-950 rounded p-3 text-slate-300 overflow-x-auto">{`from sdk import MimirClient

mimir = MimirClient("http://127.0.0.1:8787")
mimir.memory.remember({"type": "user_correction", "correction": "Call me Tym"})
results = mimir.memory.recall("user name preferences")
`}</pre>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
          <h2 className="text-sm font-medium mb-3">Environment Variables</h2>
          <p className="text-xs text-slate-400 mb-2">Configure via <code className="text-brand-400">.env</code> file in the project root. See <code className="text-brand-400">.env.example</code> for all options.</p>
          <div className="space-y-1 text-xs font-mono">
            {[
              ["MIMIR_DEFAULT_TOKEN_BUDGET", "Max tokens per context build"],
              ["MIMIR_MAX_MEMORIES_PER_CONTEXT", "Max memories injected"],
              ["MIMIR_EMBEDDING_MODEL", "Sentence transformer model"],
              ["SLACK_BOT_TOKEN", "Slack integration"],
              ["VAPID_PRIVATE_KEY", "PWA push VAPID key"],
            ].map(([k, v]) => (
              <div key={k} className="flex gap-3">
                <span className="text-brand-400 w-56 shrink-0">{k}</span>
                <span className="text-slate-500">{v}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
