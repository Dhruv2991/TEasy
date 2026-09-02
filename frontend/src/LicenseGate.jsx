import { useState } from "react";
import { api } from "./api.js";

function ExpiryNote({ license }) {
  if (!license?.expires_at) return null;
  const date = new Date(license.expires_at).toLocaleDateString();
  if (license.status === "trial") {
    return <p className="text-xs text-slate-400 mt-4">Your trial runs until {date}.</p>;
  }
  if (license.status === "cancelled") {
    return <p className="text-xs text-slate-400 mt-4">Access stays on until {date}.</p>;
  }
  return null;
}

export default function LicenseGate({ license, onRecheck }) {
  const [tab, setTab] = useState("trial"); // "trial" | "activate"
  const [email, setEmail] = useState("");
  const [licenseKey, setLicenseKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [rechecking, setRechecking] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [stillPending, setStillPending] = useState(false);

  const expiredTrial = license?.status === "trial" || license?.status === "expired" || license?.status === "cancelled";
  const isUnknownKey = license?.status === "unknown_key";

  const startTrial = async () => {
    if (!email.trim()) return setError("Enter your email to start the trial.");
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api.startTrial(email.trim());
      if (result?.is_new === false) {
        // This email already has a trial or license on file — don't
        // silently activate it here. We've emailed them the key instead.
        setNotice(
          "You already started a trial with this email. We've sent your license key to your inbox — activate it from the \"Activate key\" tab."
        );
        setBusy(false);
        return;
      }
      onRecheck();
    } catch (e) {
      setError(e.message || "Couldn't start the trial.");
    } finally {
      setBusy(false);
    }
  };

  const activate = async () => {
    if (!licenseKey.trim()) return setError("Paste your license key.");
    setBusy(true);
    setError("");
    try {
      await api.activateLicense(licenseKey.trim());
      onRecheck();
    } catch (e) {
      setError(e.message || "That license key isn't active.");
    } finally {
      setBusy(false);
    }
  };

  const subscribe = async () => {
    setBusy(true);
    setError("");
    // Open the tab synchronously, in direct response to the click — a
    // popup opened after an `await` (i.e. once the checkout URL comes
    // back) is silently blocked by most browsers' popup blockers, with no
    // error and no visible sign anything happened. Opening a blank tab
    // now and pointing it at the real URL once we have it keeps this a
    // "real" user-triggered popup the whole way through.
    const checkoutTab = window.open("", "_blank");
    try {
      const { short_url } = await api.createSubscription();
      if (checkoutTab) {
        checkoutTab.location.href = short_url;
      } else {
        // The blank-tab open itself got blocked (rare, but some browsers
        // block even that) — fall back to a same-tab link so the person
        // isn't left with no way forward.
        window.location.href = short_url;
      }
    } catch (e) {
      checkoutTab?.close();
      setError(e.message || "Couldn't start checkout.");
    } finally {
      setBusy(false);
    }
  };

  const recheck = async () => {
    setRechecking(true);
    setStillPending(false);
    setError("");
    try {
      await onRecheck();
    } finally {
      setRechecking(false);
      // onRecheck() updates `license` asynchronously via the parent, so we
      // can't know right here whether the status actually changed — give
      // a moment, then if we're still looking at the same gated screen,
      // say so explicitly instead of leaving the click looking like a
      // no-op.
      setTimeout(() => setStillPending(true), 600);
    }
  };

  const resetLicense = async () => {
    setBusy(true);
    setError("");
    try {
      await api.resetLicense();
      onRecheck();
    } catch (e) {
      setError(e.message || "Couldn't reset the license.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-50 flex items-center justify-center p-6">
      <div className="w-full max-w-md bg-white rounded-2xl border border-slate-200 shadow-sm p-8">
        <div className="w-12 h-12 rounded-xl bg-violet-600 text-white flex items-center justify-center text-lg font-bold mb-5">
          T
        </div>

        {license?.activated ? (
          <>
            <h1 className="text-xl font-semibold text-slate-900 mb-1">
              {license.status === "trial"
                ? "Your trial has ended"
                : isUnknownKey
                ? "This device's license key isn't recognized"
                : "Your subscription needs attention"}
            </h1>
            <p className="text-sm text-slate-500 mb-6">
              {license.status === "trial"
                ? "Subscribe to keep using TEasy — your data stays right where it is."
                : isUnknownKey
                ? "The license key saved on this device isn't on file with our billing system anymore (it may have been deleted or belongs to an old setup). Start a new trial or activate a different key to continue."
                : `Current status: ${license.status}. Subscribe or renew to keep going.`}
            </p>
            {error && <p className="text-xs text-rose-600 mb-3">{error}</p>}
            {isUnknownKey ? (
              <button
                onClick={resetLicense}
                disabled={busy}
                className="w-full text-sm px-4 py-2.5 rounded-lg bg-violet-600 text-white hover:bg-violet-700 disabled:opacity-50"
              >
                {busy ? "Resetting…" : "Start a new trial / activate a key"}
              </button>
            ) : (
              <>
                <button
                  onClick={subscribe}
                  disabled={busy}
                  className="w-full text-sm px-4 py-2.5 rounded-lg bg-violet-600 text-white hover:bg-violet-700 disabled:opacity-50"
                >
                  {busy ? "Opening checkout…" : "Subscribe with Razorpay"}
                </button>
                <button
                  onClick={recheck}
                  disabled={rechecking}
                  className="w-full text-sm px-4 py-2.5 rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-50 mt-2 disabled:opacity-50"
                >
                  {rechecking ? "Checking…" : "I already paid — recheck"}
                </button>
                {stillPending && !rechecking && (
                  <p className="text-xs text-slate-400 mt-2">
                    Still showing as {license.status} — Razorpay payments can take a minute or two to
                    reach us. If you paid more than a few minutes ago, contact support.
                  </p>
                )}
              </>
            )}
            <ExpiryNote license={license} />
          </>
        ) : (
          <>
            <h1 className="text-xl font-semibold text-slate-900 mb-1">Welcome to TEasy</h1>
            <p className="text-sm text-slate-500 mb-5">
              Start a free 7-day trial, or activate a license key you already have.
            </p>

            <div className="flex gap-2 mb-5 text-sm">
              <button
                onClick={() => setTab("trial")}
                className={`flex-1 py-2 rounded-lg border ${tab === "trial" ? "border-violet-600 text-violet-700 bg-violet-50" : "border-slate-200 text-slate-500"}`}
              >
                Start trial
              </button>
              <button
                onClick={() => setTab("activate")}
                className={`flex-1 py-2 rounded-lg border ${tab === "activate" ? "border-violet-600 text-violet-700 bg-violet-50" : "border-slate-200 text-slate-500"}`}
              >
                Activate key
              </button>
            </div>

            {tab === "trial" ? (
              <>
                <label className="block text-xs font-medium text-slate-500 mb-1">Email</label>
                <input
                  type="email"
                  autoFocus
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@company.com"
                  className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm mb-1"
                />
                {error && <p className="text-xs text-rose-600 mb-2">{error}</p>}
                {notice && (
                  <div className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 mb-2">
                    {notice}{" "}
                    <button
                      onClick={() => {
                        setNotice("");
                        setTab("activate");
                      }}
                      className="underline font-medium"
                    >
                      Go to Activate key
                    </button>
                  </div>
                )}
                <button
                  onClick={startTrial}
                  disabled={busy}
                  className="w-full text-sm px-4 py-2.5 rounded-lg bg-violet-600 text-white hover:bg-violet-700 disabled:opacity-50 mt-4"
                >
                  {busy ? "Starting…" : "Start 7-day free trial"}
                </button>
              </>
            ) : (
              <>
                <label className="block text-xs font-medium text-slate-500 mb-1">License key</label>
                <input
                  type="text"
                  autoFocus
                  value={licenseKey}
                  onChange={(e) => setLicenseKey(e.target.value)}
                  placeholder="Paste the key from your welcome email"
                  className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm mb-1"
                />
                {error && <p className="text-xs text-rose-600 mb-2">{error}</p>}
                <button
                  onClick={activate}
                  disabled={busy}
                  className="w-full text-sm px-4 py-2.5 rounded-lg bg-violet-600 text-white hover:bg-violet-700 disabled:opacity-50 mt-4"
                >
                  {busy ? "Activating…" : "Activate"}
                </button>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
