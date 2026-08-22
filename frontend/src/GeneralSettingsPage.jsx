import { useEffect, useState } from "react";
import { api } from "./api.js";

export default function GeneralSettingsPage() {
  const [loaded, setLoaded] = useState(false);
  const [aiProvider, setAiProvider] = useState("groq");
  const [groqKey, setGroqKey] = useState("");
  const [groqKeySet, setGroqKeySet] = useState(false);
  const [groqPreview, setGroqPreview] = useState("");
  const [geminiKey, setGeminiKey] = useState("");
  const [geminiKeySet, setGeminiKeySet] = useState(false);
  const [geminiPreview, setGeminiPreview] = useState("");
  const [tallyHost, setTallyHost] = useState("localhost");
  const [tallyPort, setTallyPort] = useState(9000);
  const [saving, setSaving] = useState(false);
  const [savedMsg, setSavedMsg] = useState("");
  const [tallyTestResult, setTallyTestResult] = useState(null);
  const [testing, setTesting] = useState(false);

  useEffect(() => {
    api.getSettings().then((s) => {
      setAiProvider(s.ai_provider || "groq");
      setGroqKeySet(s.groq_api_key_set);
      setGroqPreview(s.groq_api_key_preview);
      setGeminiKeySet(s.gemini_api_key_set);
      setGeminiPreview(s.gemini_api_key_preview);
      setTallyHost(s.tally_host || "localhost");
      setTallyPort(s.tally_port || 9000);
      setLoaded(true);
    }).catch(() => setLoaded(true));
  }, []);

  const save = async () => {
    setSaving(true);
    setSavedMsg("");
    try {
      const payload = { ai_provider: aiProvider, tally_host: tallyHost, tally_port: Number(tallyPort) };
      if (groqKey.trim()) payload.groq_api_key = groqKey.trim();
      if (geminiKey.trim()) payload.gemini_api_key = geminiKey.trim();
      const res = await api.saveSettings(payload);
      setGroqKeySet(res.groq_api_key_set);
      setGeminiKeySet(res.gemini_api_key_set);
      setGroqKey("");
      setGeminiKey("");
      setSavedMsg("Saved.");
      setTimeout(() => setSavedMsg(""), 2500);
    } catch (e) {
      setSavedMsg(`Error: ${e.message}`);
    } finally {
      setSaving(false);
    }
  };

  const testTally = async () => {
    setTesting(true);
    setTallyTestResult(null);
    try {
      // save first so the test uses whatever host/port is currently typed
      await api.saveSettings({ tally_host: tallyHost, tally_port: Number(tallyPort) });
      const status = await api.getTallyStatus();
      setTallyTestResult(status.connected ? "connected" : "unreachable");
    } catch {
      setTallyTestResult("unreachable");
    } finally {
      setTesting(false);
    }
  };

  if (!loaded) return <div className="p-6 text-slate-400">Loading…</div>;

  return (
    <div className="p-6 max-w-2xl space-y-6">
      <section className="bg-white rounded-xl border border-slate-200 p-5">
        <h2 className="text-sm font-semibold text-slate-900 mb-1">AI bill reading</h2>
        <p className="text-sm text-slate-500 mb-4">
          Choose which AI reads your bill photos, and add its free API key. Without a key,
          TEasy falls back to a less accurate local OCR path.
        </p>

        <label className="block text-xs font-medium text-slate-500 mb-2">Provider</label>
        <div className="flex gap-2 mb-4">
          <button
            type="button"
            onClick={() => setAiProvider("groq")}
            className={`flex-1 text-sm px-3 py-2 rounded-lg border ${
              aiProvider === "groq" ? "border-violet-600 bg-violet-50 text-violet-700" : "border-slate-300 text-slate-600 hover:bg-slate-50"
            }`}
          >
            Groq {groqKeySet && <span className="text-emerald-600">●</span>}
          </button>
          <button
            type="button"
            onClick={() => setAiProvider("gemini")}
            className={`flex-1 text-sm px-3 py-2 rounded-lg border ${
              aiProvider === "gemini" ? "border-violet-600 bg-violet-50 text-violet-700" : "border-slate-300 text-slate-600 hover:bg-slate-50"
            }`}
          >
            Gemini {geminiKeySet && <span className="text-emerald-600">●</span>}
          </button>
        </div>

        {aiProvider === "groq" ? (
          <div>
            <p className="text-xs text-slate-500 mb-2">
              Get a free key at{" "}
              <a className="text-violet-600 underline" href="https://console.groq.com/keys" target="_blank" rel="noreferrer">
                console.groq.com/keys
              </a>
            </p>
            {groqKeySet && (
              <div className="text-xs text-emerald-600 bg-emerald-50 rounded-lg px-3 py-2 mb-3">
                Key set ({groqPreview})
              </div>
            )}
            <label className="block text-xs font-medium text-slate-500 mb-1">
              {groqKeySet ? "Replace key" : "API key"}
            </label>
            <input
              type="password"
              value={groqKey}
              onChange={(e) => setGroqKey(e.target.value)}
              placeholder="gsk_..."
              className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm"
            />
          </div>
        ) : (
          <div>
            <p className="text-xs text-slate-500 mb-2">
              Get a free key at{" "}
              <a className="text-violet-600 underline" href="https://aistudio.google.com/apikey" target="_blank" rel="noreferrer">
                aistudio.google.com/apikey
              </a>
              . No credit card needed. Note: free-tier prompts may be used by Google to improve their models.
            </p>
            {geminiKeySet && (
              <div className="text-xs text-emerald-600 bg-emerald-50 rounded-lg px-3 py-2 mb-3">
                Key set ({geminiPreview})
              </div>
            )}
            <label className="block text-xs font-medium text-slate-500 mb-1">
              {geminiKeySet ? "Replace key" : "API key"}
            </label>
            <input
              type="password"
              value={geminiKey}
              onChange={(e) => setGeminiKey(e.target.value)}
              placeholder="AIza..."
              className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm"
            />
          </div>
        )}
      </section>

      <section className="bg-white rounded-xl border border-slate-200 p-5">
        <h2 className="text-sm font-semibold text-slate-900 mb-1">Tally Prime connection</h2>
        <p className="text-sm text-slate-500 mb-4">
          In Tally: F1 (Help) → Settings → Connectivity → Client/Server configuration → set
          "TallyPrime acts as" to Server, then match the port below (default 9000).
        </p>
        <div className="grid grid-cols-3 gap-3 mb-3">
          <div className="col-span-2">
            <label className="block text-xs font-medium text-slate-500 mb-1">Host</label>
            <input
              value={tallyHost}
              onChange={(e) => setTallyHost(e.target.value)}
              className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-500 mb-1">Port</label>
            <input
              type="number"
              value={tallyPort}
              onChange={(e) => setTallyPort(e.target.value)}
              className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm"
            />
          </div>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={testTally}
            disabled={testing}
            className="text-sm px-3 py-1.5 rounded-lg border border-slate-300 hover:bg-slate-50 disabled:opacity-50"
          >
            {testing ? "Testing…" : "Test connection"}
          </button>
          {tallyTestResult === "connected" && (
            <span className="text-xs text-emerald-600">Connected</span>
          )}
          {tallyTestResult === "unreachable" && (
            <span className="text-xs text-rose-600">Couldn't reach Tally — make sure it's open and the server is enabled</span>
          )}
        </div>
      </section>

      <div className="flex items-center gap-3">
        <button
          onClick={save}
          disabled={saving}
          className="text-sm px-4 py-2 rounded-lg bg-violet-600 text-white hover:bg-violet-700 disabled:opacity-50"
        >
          {saving ? "Saving…" : "Save settings"}
        </button>
        {savedMsg && <span className="text-sm text-slate-500">{savedMsg}</span>}
      </div>
    </div>
  );
}
