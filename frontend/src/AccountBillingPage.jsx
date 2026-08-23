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
};

const STATUS_COLOR = {
  trial: "bg-blue-50 text-blue-700 border-blue-200",
  active: "bg-emerald-50 text-emerald-700 border-emerald-200",
  past_due: "bg-amber-50 text-amber-700 border-amber-200",
  cancelled: "bg-slate-100 text-slate-600 border-slate-200",
  expired: "bg-rose-50 text-rose-700 border-rose-200",
};

export default function AccountBillingPage() {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busyPlan, setBusyPlan] = useState(null); // "monthly" | "yearly" | null
  const [cancelling, setCancelling] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [error, setError] = useState("");

  const refresh = () => {
    setLoading(true);
    api
      .getLicenseStatus()
      .then(setStatus)
      .catch(() => setError("Couldn't reach the license server."))
      .finally(() => setLoading(false));
  };

  useEffect(refresh, []);

  const subscribe = async (plan) => {
    setBusyPlan(plan);
    setError("");
    try {
      const res = await api.createSubscription(plan);
      if (res.short_url) {
        window.open(res.short_url, "_blank");
      }
      refresh();
    } catch (e) {
      setError(e.message || "Couldn't start checkout.");
    } finally {
      setBusyPlan(null);
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

  if (loading && !status) return <div className="p-6 text-slate-400">Loading…</div>;

  const s = status || {};
  const statusKey = s.status || "trial";
  const canCancel = statusKey === "active" || statusKey === "past_due";
  const canSubscribe = statusKey === "trial" || statusKey === "expired" || statusKey === "cancelled";

  return (
    <div className="p-6 max-w-2xl space-y-6">
      <section className="bg-white rounded-xl border border-slate-200 p-5">
        <h2 className="text-sm font-semibold text-slate-900 mb-4">Account & billing</h2>

        <div className="flex items-center justify-between mb-4">
          <div>
            <span
              className={`inline-block text-xs font-medium px-2.5 py-1 rounded-full border ${STATUS_COLOR[statusKey] || STATUS_COLOR.expired}`}
            >
              {STATUS_LABEL[statusKey] || statusKey}
            </span>
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

        {error && <p className="text-xs text-rose-600 mb-3">{error}</p>}

        {canSubscribe && (
          <div className="flex gap-2">
            <button
              onClick={() => subscribe("monthly")}
              disabled={busyPlan !== null}
              className="flex-1 text-sm px-3 py-2 rounded-lg bg-violet-600 text-white hover:bg-violet-700 disabled:opacity-50"
            >
              {busyPlan === "monthly" ? "Opening checkout…" : "Subscribe — ₹499/mo"}
            </button>
            <button
              onClick={() => subscribe("yearly")}
              disabled={busyPlan !== null}
              className="flex-1 text-sm px-3 py-2 rounded-lg border border-violet-300 text-violet-700 hover:bg-violet-50 disabled:opacity-50"
            >
              {busyPlan === "yearly" ? "Opening checkout…" : "Subscribe — ₹4,999/yr"}
            </button>
          </div>
        )}

        {canCancel && !confirmCancel && (
          <button
            onClick={() => setConfirmCancel(true)}
            className="text-sm px-3 py-2 rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-50"
          >
            Cancel subscription
          </button>
        )}

        {canCancel && confirmCancel && (
          <div className="border border-rose-200 bg-rose-50 rounded-lg p-3">
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
    </div>
  );
}
