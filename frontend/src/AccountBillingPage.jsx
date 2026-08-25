import { useEffect, useState } from "react";
import { api } from "./api.js";

function formatDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
  } catch {
    return iso;
  }
}

const STATUS_LABEL = {
  trial: "Free trial",
  active: "Active subscription",
  past_due: "Payment issue",
  cancelled: "Cancelled",
  expired: "Expired",
  device_limit_reached: "Device limit reached",
};

const STATUS_COLOR = {
  trial: "bg-blue-50 text-blue-700 border-blue-200",
  active: "bg-emerald-50 text-emerald-700 border-emerald-200",
  past_due: "bg-amber-50 text-amber-700 border-amber-200",
  cancelled: "bg-slate-100 text-slate-600 border-slate-200",
  expired: "bg-rose-50 text-rose-700 border-rose-200",
  device_limit_reached: "bg-rose-50 text-rose-700 border-rose-200",
};

// Named after Tally's own Silver/Gold editions so the split is instantly
// familiar: Silver is a single-device seat (one shop counter machine),
// Gold allows the same key to run on a handful of devices at once (the
// shop counter + home + the accountant's laptop, say) — closing the "just
// hand the exe and key to someone else" gap without punishing the common
// legitimate case of one owner, two machines.
const TIERS = [
  {
    key: "silver",
    label: "Silver",
    tagline: "One device. The right fit if TEasy only runs on your shop's billing counter.",
    deviceLimit: 1,
    monthly: 499,
    yearly: 4999,
    accent: "border-slate-300",
    badge: "bg-slate-100 text-slate-700",
  },
  {
    key: "gold",
    label: "Gold",
    tagline: "Up to 3 devices on the same key — counter, home, and your accountant, all covered.",
    deviceLimit: 3,
    monthly: 899,
    yearly: 8999,
    accent: "border-amber-300",
    badge: "bg-amber-100 text-amber-800",
  },
];

function TierBadge({ tierKey }) {
  const tier = TIERS.find((t) => t.key === tierKey);
  if (!tier) return null;
  return (
    <span className={`inline-block text-xs font-semibold px-2 py-0.5 rounded-full ${tier.badge}`}>
      {tier.label}
    </span>
  );
}

export default function AccountBillingPage() {
  const [status, setStatus] = useState(null);
  const [device, setDevice] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busyKey, setBusyKey] = useState(null); // e.g. "gold-yearly" | null
  const [cancelling, setCancelling] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  const refresh = () => {
    setLoading(true);
    Promise.all([api.getLicenseStatus(), api.getDeviceInfo().catch(() => null)])
      .then(([s, d]) => {
        setStatus(s);
        setDevice(d);
      })
      .catch(() => setError("Couldn't reach the license server."))
      .finally(() => setLoading(false));
  };

  useEffect(refresh, []);

  const subscribe = async (tierKey, cycle) => {
    const busy = `${tierKey}-${cycle}`;
    setBusyKey(busy);
    setError("");
    try {
      const res = await api.createSubscription(cycle, tierKey);
      if (res.short_url) {
        window.open(res.short_url, "_blank");
      }
      refresh();
    } catch (e) {
      setError(e.message || "Couldn't start checkout.");
    } finally {
      setBusyKey(null);
    }
  };

  const cancel = async () => {
    setCancelling(true);
    setError("");
    try {
      await api.cancelSubscription();
      setConfirmCancel(false);
      refresh();
    } catch (e) {
      setError(e.message || "Couldn't cancel the subscription.");
    } finally {
      setCancelling(false);
    }
  };

  const copyFingerprint = async () => {
    if (!device?.device_fingerprint) return;
    try {
      await navigator.clipboard.writeText(device.device_fingerprint);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard permission denied — silently ignore, the id is still shown on screen */
    }
  };

  if (loading && !status) return <div className="p-6 text-slate-400">Loading…</div>;

  const s = status || {};
  const statusKey = s.status || "trial";
  const currentTier = s.tier || device?.tier || "silver";
  const canCancel = statusKey === "active" || statusKey === "past_due";
  const canSubscribe = statusKey === "trial" || statusKey === "expired" || statusKey === "cancelled" || statusKey === "device_limit_reached";

  return (
    <div className="p-6 max-w-3xl space-y-6">
      <section className="bg-white rounded-xl border border-slate-200 p-5">
        <h2 className="text-sm font-semibold text-slate-900 mb-4">Account & billing</h2>

        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <span
              className={`inline-block text-xs font-medium px-2.5 py-1 rounded-full border ${STATUS_COLOR[statusKey] || STATUS_COLOR.expired}`}
            >
              {STATUS_LABEL[statusKey] || statusKey}
            </span>
            {(statusKey === "active" || statusKey === "past_due" || statusKey === "cancelled") && (
              <TierBadge tierKey={currentTier} />
            )}
          </div>
          <button onClick={refresh} className="text-xs text-slate-400 hover:text-slate-600">
            Refresh
          </button>
        </div>

        <dl className="text-sm space-y-2 mb-5">
          {statusKey === "trial" && (
            <div className="flex justify-between">
              <dt className="text-slate-500">Trial ends</dt>
              <dd className="text-slate-900">{formatDate(s.expires_at)}</dd>
            </div>
          )}
          {(statusKey === "active" || statusKey === "cancelled" || statusKey === "past_due") && (
            <div className="flex justify-between">
              <dt className="text-slate-500">{statusKey === "cancelled" ? "Access until" : "Renews on"}</dt>
              <dd className="text-slate-900">{formatDate(s.expires_at)}</dd>
            </div>
          )}
        </dl>

        {statusKey === "past_due" && (
          <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 mb-4">
            Your last payment didn't go through. Subscribe again below to update your payment method and keep access.
          </p>
        )}

        {statusKey === "device_limit_reached" && (
          <p className="text-xs text-rose-700 bg-rose-50 border border-rose-200 rounded-lg px-3 py-2 mb-4">
            This key is already active on as many devices as its plan allows. Deactivate one elsewhere, or move to
            Gold below for more devices on the same key.
          </p>
        )}

        {error && <p className="text-xs text-rose-600 mb-3">{error}</p>}

        {canSubscribe && (
          <div className="grid sm:grid-cols-2 gap-3 mb-2">
            {TIERS.map((tier) => (
              <div key={tier.key} className={`border ${tier.accent} rounded-xl p-4 flex flex-col`}>
                <div className="flex items-center gap-2 mb-1">
                  <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${tier.badge}`}>{tier.label}</span>
                  <span className="text-xs text-slate-400">
                    {tier.deviceLimit} device{tier.deviceLimit > 1 ? "s" : ""}
                  </span>
                </div>
                <p className="text-xs text-slate-500 mb-4 flex-1">{tier.tagline}</p>
                <div className="flex gap-2">
                  <button
                    onClick={() => subscribe(tier.key, "monthly")}
                    disabled={busyKey !== null}
                    className="flex-1 text-sm px-3 py-2 rounded-lg bg-violet-600 text-white hover:bg-violet-700 disabled:opacity-50"
                  >
                    {busyKey === `${tier.key}-monthly` ? "Opening…" : `₹${tier.monthly}/mo`}
                  </button>
                  <button
                    onClick={() => subscribe(tier.key, "yearly")}
                    disabled={busyKey !== null}
                    className="flex-1 text-sm px-3 py-2 rounded-lg border border-violet-300 text-violet-700 hover:bg-violet-50 disabled:opacity-50"
                  >
                    {busyKey === `${tier.key}-yearly` ? "Opening…" : `₹${tier.yearly}/yr`}
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        {canCancel && !confirmCancel && (
          <button
            onClick={() => setConfirmCancel(true)}
            className="text-sm px-3 py-2 rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-50 mt-2"
          >
            Cancel subscription
          </button>
        )}

        {canCancel && confirmCancel && (
          <div className="border border-rose-200 bg-rose-50 rounded-lg p-3 mt-2">
            <p className="text-xs text-rose-700 mb-3">
              You'll keep access until {formatDate(s.expires_at)} — the period you already paid for — then it will end. This can't be undone from here once confirmed.
            </p>
            <div className="flex gap-2">
              <button
                onClick={cancel}
                disabled={cancelling}
                className="text-sm px-3 py-1.5 rounded-lg bg-rose-600 text-white hover:bg-rose-700 disabled:opacity-50"
              >
                {cancelling ? "Cancelling…" : "Yes, cancel"}
              </button>
              <button
                onClick={() => setConfirmCancel(false)}
                disabled={cancelling}
                className="text-sm px-3 py-1.5 rounded-lg border border-slate-300 hover:bg-white"
              >
                Never mind
              </button>
            </div>
          </div>
        )}
      </section>

      <section className="bg-white rounded-xl border border-slate-200 p-5">
        <h2 className="text-sm font-semibold text-slate-900 mb-1">This device</h2>
        <p className="text-xs text-slate-500 mb-3">
          Your {TIERS.find((t) => t.key === currentTier)?.label || "Silver"} plan allows{" "}
          {device?.device_limit ?? TIERS.find((t) => t.key === currentTier)?.deviceLimit ?? 1} device
          {(device?.device_limit ?? 1) > 1 ? "s" : ""} on this key at once. If you ever need to move to a new
          machine, or run into a device-limit message, quote this id to support so they can free up a slot.
        </p>
        <div className="flex items-center gap-2">
          <code className="text-xs bg-slate-50 border border-slate-200 rounded px-2.5 py-1.5 flex-1 truncate text-slate-600">
            {device?.device_fingerprint || "—"}
          </code>
          <button
            onClick={copyFingerprint}
            disabled={!device?.device_fingerprint}
            className="text-xs font-medium border border-slate-300 px-3 py-1.5 rounded-lg hover:bg-slate-100 disabled:opacity-40"
          >
            {copied ? "Copied" : "Copy"}
          </button>
        </div>
      </section>
    </div>
  );
}
