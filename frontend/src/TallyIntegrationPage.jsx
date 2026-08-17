import { useEffect, useState, useCallback } from "react";
import { api } from "./api.js";
import { Icon } from "./icons.jsx";

const LEDGER_FIELDS = [
  ["company_name", "Company Name (must match Tally exactly)"],
  ["sales_ledger", "Sales Ledger"],
  ["purchase_ledger", "Purchase Ledger"],
  ["output_cgst_ledger", "Output CGST (Sales)"],
  ["output_sgst_ledger", "Output SGST (Sales)"],
  ["output_igst_ledger", "Output IGST (Sales)"],
  ["input_cgst_ledger", "Input CGST (Purchase)"],
  ["input_sgst_ledger", "Input SGST (Purchase)"],
  ["input_igst_ledger", "Input IGST (Purchase)"],
  ["cash_ledger", "Cash Ledger"],
];

export default function TallyIntegrationPage() {
  const [connected, setConnected] = useState(null);
  const [config, setConfig] = useState(null);
  const [savingConfig, setSavingConfig] = useState(false);
  const [pushing, setPushing] = useState(false);
  const [pushResults, setPushResults] = useState([]);
  const [approvedTx, setApprovedTx] = useState([]);

  const refresh = useCallback(async () => {
    const [status, cfg, txs] = await Promise.all([
      api.getTallyStatus(),
      api.getTallyConfig(),
      api.listTransactions("APPROVED"),
    ]);
    setConnected(status.connected);
    setConfig(cfg);
    setApprovedTx(txs);
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const saveConfig = async () => {
    setSavingConfig(true);
    try {
      const saved = await api.updateTallyConfig(config);
      setConfig(saved);
    } catch (e) {
      alert(e.message);
    } finally {
      setSavingConfig(false);
    }
  };

  const push = async () => {
    setPushing(true);
    setPushResults([]);
    try {
      const results = await api.pushToTally();
      setPushResults(results);
      refresh();
    } catch (e) {
      alert(e.message);
    } finally {
      setPushing(false);
    }
  };

  const unsent = approvedTx.filter((t) => t.tally_status !== "SENT");

  return (
    <div className="p-6 space-y-6">
      <div className="bg-white rounded-xl border border-slate-200 p-5">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="font-semibold text-slate-900">Tally Connection</h2>
            <p className="text-sm text-slate-500">
              Tally Prime must be running with its HTTP/XML server enabled: F1 → Settings → Connectivity → act as Server (default port 9000).
            </p>
          </div>
          <div className="flex items-center gap-3">
            <span className={`inline-flex items-center gap-2 text-sm font-medium px-3 py-1.5 rounded-full ${connected ? "bg-emerald-100 text-emerald-700" : "bg-rose-100 text-rose-700"}`}>
              <span className={`w-2 h-2 rounded-full ${connected ? "bg-emerald-500" : "bg-rose-500"}`} />
              {connected === null ? "Checking…" : connected ? "Connected" : "Not connected"}
            </span>
            <button onClick={refresh} className="text-sm border border-slate-300 rounded-lg px-3 py-1.5 flex items-center gap-1.5 hover:bg-slate-50">
              <Icon.Refresh width={14} height={14} /> Retest
            </button>
          </div>
        </div>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-5">
        <div className="flex items-center justify-between mb-1">
          <h2 className="font-semibold text-slate-900">Push Approved Transactions</h2>
        </div>
        <p className="text-sm text-slate-500 mb-4">
          {unsent.length} approved transaction(s) waiting to be sent to Tally.
        </p>
        <button
          onClick={push}
          disabled={pushing || unsent.length === 0}
          className="bg-indigo-600 text-white text-sm font-medium px-4 py-2 rounded-lg disabled:opacity-40"
        >
          {pushing ? "Pushing…" : `Push ${unsent.length} to Tally`}
        </button>

        {pushResults.length > 0 && (
          <div className="mt-4 space-y-1.5">
            {pushResults.map((r) => (
              <div
                key={r.transaction_id}
                className={`text-sm px-3 py-2 rounded-lg ${r.status === "SENT" ? "bg-emerald-50 text-emerald-700" : "bg-rose-50 text-rose-700"}`}
              >
                Transaction #{r.transaction_id}: {r.status} — {r.message}
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-5">
        <h2 className="font-semibold text-slate-900 mb-1">Ledger Name Mapping</h2>
        <p className="text-sm text-slate-500 mb-4">
          These must match your Tally company's actual ledger names exactly, or Tally will reject the voucher with a "ledger does not exist" error.
        </p>
        {config ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {LEDGER_FIELDS.map(([key, label]) => (
              <div key={key}>
                <label className="block text-xs font-medium text-slate-600 mb-1">{label}</label>
                <input
                  className="w-full border border-slate-300 rounded-lg px-3 py-1.5 text-sm"
                  value={config[key] || ""}
                  onChange={(e) => setConfig({ ...config, [key]: e.target.value })}
                />
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-slate-400">Loading…</p>
        )}
        <button
          onClick={saveConfig}
          disabled={savingConfig || !config}
          className="mt-4 bg-slate-900 text-white text-sm font-medium px-4 py-2 rounded-lg disabled:opacity-40"
        >
          {savingConfig ? "Saving…" : "Save Ledger Mapping"}
        </button>
      </div>
    </div>
  );
}
