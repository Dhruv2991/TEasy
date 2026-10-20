import { useState } from "react";
import { api } from "./api.js";

export default function FirstRunSetup({ onDone }) {
  const [tallyHost, setTallyHost] = useState("localhost");
  const [tallyPort, setTallyPort] = useState(9000);
  const [saving, setSaving] = useState(false);

  const start = async () => {
    setSaving(true);
    try {
      await api.saveSettings({
        tally_host: tallyHost.trim() || "localhost",
        tally_port: Number(tallyPort) || 9000,
        ai_provider: "offline",
      });
      onDone();
    } catch {
      onDone();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-50 flex items-center justify-center p-6">
      <div className="w-full max-w-md bg-white rounded-2xl border border-slate-200 shadow-sm p-8">
        <div className="w-12 h-12 rounded-xl bg-violet-600 text-white flex items-center justify-center text-lg font-bold mb-5 shadow-sm">
          T
        </div>
        <h1 className="text-xl font-semibold text-slate-900 mb-1">Welcome to TEasy</h1>
        <p className="text-sm text-slate-500 mb-6">
          100% Local &amp; Offline Accounting Bridge. Your bills, PDFs, and spreadsheets
          are processed directly on your machine with zero cloud dependencies and maximum privacy.
        </p>

        <div className="bg-slate-50 rounded-xl p-4 border border-slate-200 mb-6 space-y-2 text-xs text-slate-600">
          <div className="flex items-center gap-2">
            <span className="text-emerald-600 font-bold">✓</span>
            <span><strong>B2B e-Invoice QR Scanner:</strong> Instant 100% verified data</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-emerald-600 font-bold">✓</span>
            <span><strong>Digital PDF Vector Parser:</strong> Exact line items &amp; totals</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-emerald-600 font-bold">✓</span>
            <span><strong>Fuzzy Header Profiler:</strong> Rapid automated spreadsheet imports</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-emerald-600 font-bold">✓</span>
            <span><strong>Local OCR Engine:</strong> Safe on-premise scan processing</span>
          </div>
        </div>

        <div className="space-y-3 mb-6">
          <label className="block text-xs font-medium text-slate-500">Tally Prime Connection</label>
          <div className="grid grid-cols-3 gap-2">
            <div className="col-span-2">
              <input
                type="text"
                value={tallyHost}
                onChange={(e) => setTallyHost(e.target.value)}
                placeholder="localhost"
                className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm"
              />
            </div>
            <div>
              <input
                type="number"
                value={tallyPort}
                onChange={(e) => setTallyPort(e.target.value)}
                placeholder="9000"
                className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm"
              />
            </div>
          </div>
          <p className="text-[11px] text-slate-400">Default: localhost:9000 (XML Server in Tally Prime)</p>
        </div>

        <button
          onClick={start}
          disabled={saving}
          className="w-full text-sm px-4 py-2.5 rounded-lg bg-violet-600 text-white font-medium hover:bg-violet-700 disabled:opacity-50 transition"
        >
          {saving ? "Configuring…" : "Get Started"}
        </button>
      </div>
    </div>
  );
}
