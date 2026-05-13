import { useEffect, useState } from "react";
import axios from "axios";
import PageHeader from "../components/PageHeader";
import Badge from "../components/Badge";

export default function NotificationsPage() {
  const [subs, setSubs] = useState<any[]>([]);
  const [pushKey, setPushKey] = useState<string | null>(null);
  const [subscribed, setSubscribed] = useState(false);

  useEffect(() => {
    axios.get("/api/push/vapid-key").then(r => setPushKey(r.data.public_key));
  }, []);

  const handleSubscribe = async () => {
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
      alert("PWA push not supported in this browser.");
      return;
    }
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
  };

  return (
    <div>
      <PageHeader title="Notifications" subtitle="Push and delivery channels" />
      <div className="p-6 space-y-6">
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
          <h2 className="text-sm font-medium mb-3">PWA Push Notifications</h2>
          {pushKey ? (
            <div className="space-y-2">
              <p className="text-xs text-slate-400">VAPID key configured. Subscribe this device to receive approval notifications.</p>
              <button
                onClick={handleSubscribe}
                disabled={subscribed}
                className="bg-brand-600 hover:bg-brand-700 disabled:opacity-60 text-white text-sm px-3 py-1.5 rounded-md">
                {subscribed ? "Subscribed" : "Subscribe this device"}
              </button>
            </div>
          ) : (
            <p className="text-xs text-slate-500">VAPID keys not configured. Set VAPID_PRIVATE_KEY and VAPID_PUBLIC_KEY in .env to enable push.</p>
          )}
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
          <h2 className="text-sm font-medium mb-3">Slack</h2>
          <p className="text-xs text-slate-400">
            Set <code className="text-brand-400">SLACK_BOT_TOKEN</code> and <code className="text-brand-400">SLACK_APPROVAL_CHANNEL</code> in .env to enable Slack approval notifications.
          </p>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
          <h2 className="text-sm font-medium mb-1">Dashboard Approval Queue</h2>
          <p className="text-xs text-slate-400">Always available. Visit the <a href="/approvals" className="text-brand-400 hover:underline">Approvals</a> page to review pending requests.</p>
        </div>
      </div>
    </div>
  );
}
