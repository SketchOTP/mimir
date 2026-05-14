import { useEffect, useState } from "react";
import axios from "axios";
import PageHeader from "../components/PageHeader";
import { Bell, Slack, BellOff, CheckCircle2, Copy, Check } from "lucide-react";

function CopySnippet({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex items-center gap-2 mt-2 bg-slate-950 rounded-md px-3 py-2">
      <code className="flex-1 text-xs text-slate-300 break-all">{text}</code>
      <button
        onClick={() => {
          navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 2000);
        }}
        className="text-slate-500 hover:text-slate-200 transition-colors shrink-0"
      >
        {copied ? <Check size={13} className="text-emerald-400" /> : <Copy size={13} />}
      </button>
    </div>
  );
}

function StatusDot({ ok }: { ok: boolean }) {
  return (
    <span className={`inline-block w-2 h-2 rounded-full ${ok ? "bg-emerald-400" : "bg-slate-600"}`} />
  );
}

export default function NotificationsPage() {
  const [pushKey, setPushKey] = useState<string | null | undefined>(undefined);
  const [subscribed, setSubscribed] = useState(false);
  const [subscribing, setSubscribing] = useState(false);
  const [pushError, setPushError] = useState("");

  useEffect(() => {
    axios.get("/api/push/vapid-key")
      .then(r => setPushKey(r.data.public_key ?? null))
      .catch(() => setPushKey(null));
  }, []);

  const vapidConfigured = Boolean(pushKey);
  const pushSupported = typeof window !== "undefined" && "serviceWorker" in navigator && "PushManager" in window;

  const handleSubscribe = async () => {
    setPushError("");
    setSubscribing(true);
    try {
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: pushKey!,
      });
      const j = sub.toJSON();
      await axios.post("/api/push/subscribe", {
        endpoint: sub.endpoint,
        keys: j.keys,
        user_agent: navigator.userAgent,
      });
      setSubscribed(true);
    } catch (e: any) {
      setPushError(e?.message ?? "Subscription failed.");
    } finally {
      setSubscribing(false);
    }
  };

  return (
    <div>
      <PageHeader title="Notifications" subtitle="Push and messaging channels" />
      <div className="p-6 space-y-4">

        {/* PWA Push */}
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-5">
          <div className="flex items-center gap-2 mb-1">
            <StatusDot ok={vapidConfigured && pushSupported} />
            <h2 className="text-sm font-semibold text-slate-100">Browser Push Notifications</h2>
          </div>
          <p className="text-xs text-slate-400 mb-4">
            Receive approval requests and alerts directly in your browser, even when Mimir is in the background.
          </p>

          {pushKey === undefined ? (
            <p className="text-xs text-slate-500">Checking configuration…</p>
          ) : vapidConfigured ? (
            <div className="space-y-3">
              <div className="flex items-center gap-2 text-xs text-emerald-400">
                <CheckCircle2 size={13} />
                VAPID keys configured — push is ready
              </div>
              {!pushSupported && (
                <p className="text-xs text-amber-400">
                  This browser doesn't support push notifications. Try Chrome or Edge.
                </p>
              )}
              {pushSupported && !subscribed && (
                <button
                  onClick={handleSubscribe}
                  disabled={subscribing}
                  className="bg-brand-600 hover:bg-brand-500 disabled:opacity-60 text-white text-sm px-4 py-2 rounded-md transition-colors"
                >
                  {subscribing ? "Subscribing…" : "Subscribe this device"}
                </button>
              )}
              {subscribed && (
                <div className="flex items-center gap-2 text-xs text-emerald-400">
                  <CheckCircle2 size={13} />
                  This device is subscribed
                </div>
              )}
              {pushError && <p className="text-xs text-red-400">{pushError}</p>}
            </div>
          ) : (
            <div className="space-y-3">
              <div className="flex items-center gap-2 text-xs text-slate-500">
                <BellOff size={13} />
                VAPID keys not configured
              </div>
              <p className="text-xs text-slate-400">
                Generate a VAPID key pair and add them to your environment:
              </p>
              <CopySnippet text="npx web-push generate-vapid-keys" />
              <p className="text-xs text-slate-500 mt-2">Then add to your <code className="text-slate-300">.env</code>:</p>
              <CopySnippet text={"VAPID_PRIVATE_KEY=<your-private-key>\nVAPID_PUBLIC_KEY=<your-public-key>\nVAPID_SUBJECT=mailto:you@example.com"} />
              <p className="text-xs text-slate-500">Restart Mimir after adding the keys.</p>
            </div>
          )}
        </div>

        {/* Slack */}
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-5">
          <div className="flex items-center gap-2 mb-1">
            <StatusDot ok={false} />
            <h2 className="text-sm font-semibold text-slate-100">Slack</h2>
          </div>
          <p className="text-xs text-slate-400 mb-4">
            Post approval requests to a Slack channel. Useful for team setups where multiple people review AI changes.
          </p>
          <p className="text-xs text-slate-400">
            Create a Slack app with <code className="text-slate-300">chat:write</code> scope and add to your environment:
          </p>
          <CopySnippet text={"SLACK_BOT_TOKEN=xoxb-...\nSLACK_APPROVAL_CHANNEL=#mimir-approvals"} />
          <p className="text-xs text-slate-500 mt-2">Restart Mimir after adding the keys.</p>
        </div>

        {/* Dashboard queue */}
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-5">
          <div className="flex items-center gap-2 mb-1">
            <StatusDot ok={true} />
            <h2 className="text-sm font-semibold text-slate-100">Dashboard Approval Queue</h2>
          </div>
          <p className="text-xs text-slate-400">
            Always available — no setup needed.{" "}
            <a href="/approvals" className="text-brand-400 hover:underline">Open Approvals →</a>
          </p>
        </div>

      </div>
    </div>
  );
}
