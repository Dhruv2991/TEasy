import { useEffect, useState } from "react";
import { api } from "./api.js";

export default function GeneralSettingsPage() {
  const [loaded, setLoaded] = useState(false);
  const [aiProvider, setAiProvider] = useState("offline");
  const [aiModel, setAiModel] = useState("");
  const [groqApiKey, setGroqApiKey] = useState("");
  const [groqKeySet, setGroqKeySet] = useState(false);
  const [groqKeyPreview, setGroqKeyPreview] = useState("");
  const [groqVisionModel, setGroqVisionModel] = useState("llama-3.2-90b-vision-preview");

  const [geminiApiKey, setGeminiApiKey] = useState("");
  const [geminiKeySet, setGeminiKeySet] = useState(false);
  const [geminiKeyPreview, setGeminiKeyPreview] = useState("");
  const [geminiVisionModel, setGeminiVisionModel] = useState("gemini-1.5-flash");

  const [ollamaEndpoint, setOllamaEndpoint] = useState("http://localhost:11434");
  const [ollamaVisionModel, setOllamaVisionModel] = useState("qwen2-vl");

  const [tallyHost, setTallyHost] = useState("localhost");
  const [tallyPort, setTallyPort] = useState(9000);
  const [saving, setSaving] = useState(false);
  const [savedMsg, setSavedMsg] = useState("");
  const [tallyTestResult, setTallyTestResult] = useState(null);
  const [testing, setTesting] = useState(false);

  useEffect(() => {
    api.getSettings().then((s) => {
      setAiProvider(s.ai_provider || "offline");
      setAiModel(s.ai_model || "");
      setGroqKeySet(Boolean(s.groq_api_key_set));
      setGroqKeyPreview(s.groq_api_key_preview || "");
      setGroqVisionModel(s.groq_vision_model || "llama-3.2-90b-vision-preview");

      setGeminiKeySet(Boolean(s.gemini_api_key_set));
      setGeminiKeyPreview(s.gemini_api_key_preview || "");
      setGeminiVisionModel(s.gemini_vision_model || "gemini-1.5-flash");

      setOllamaEndpoint(s.ollama_endpoint || "http://localhost:11434");
      setOllamaVisionModel(s.ollama_vision_model || "qwen2-vl");

      setTallyHost(s.tally_host || "localhost");
      setTallyPort(s.tally_port || 9000);
      setLoaded(true);
    }).catch(() => setLoaded(true));
  }, []);

  const save = async () => {
    setSaving(true);
    setSavedMsg("");
    try {
      const payload = {
        ai_provider: aiProvider,
        ai_model: aiModel.trim(),
        groq_vision_model: groqVisionModel.trim() || "llama-3.2-90b-vision-preview",
        gemini_vision_model: geminiVisionModel.trim() || "gemini-1.5-flash",
        ollama_endpoint: ollamaEndpoint.trim() || "http://localhost:11434",
        ollama_vision_model: ollamaVisionModel.trim() || "qwen2-vl",
        tally_host: tallyHost.trim() || "localhost",
        tally_port: Number(tallyPort) || 9000,
      };

      if (groqApiKey.trim()) {
        payload.groq_api_key = groqApiKey.trim();
      }
      if (geminiApiKey.trim()) {
        payload.gemini_api_key = geminiApiKey.trim();
      }

      const res = await api.saveSettings(payload);
      if (res.groq_api_key_set !== undefined) setGroqKeySet(res.groq_api_key_set);
      if (res.gemini_api_key_set !== undefined) setGeminiKeySet(res.gemini_api_key_set);
      setGroqApiKey("");
      setGeminiApiKey("");

      setSavedMsg("Settings saved successfully.");
      setTimeout(() => setSavedMsg(""), 3000);
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
      await api.saveSettings({ tally_host: tallyHost, tally_port: Number(tallyPort) });
      const status = await api.getTallyStatus();
      setTallyTestResult(status.connected ? "connected" : "unreachable");
    } catch {
      setTallyTestResult("unreachable");
    } finally {
      setTesting(false);
    }
  };

  if (!loaded) return <div className="p-6 text-slate-400">Loading settings…</div>;

  return (
    <div className="p-6 max-w-3xl space-y-6">
      {/* 1. Offline Deterministic Foundation Banner */}
      <section className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-sm font-semibold text-slate-900">Deterministic Ingestion Core (Always Offline)</h2>
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-emerald-50 text-emerald-700 border border-emerald-200">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-600"></span>
            100% Deterministic &amp; Local
          </span>
        </div>
        <p className="text-sm text-slate-500 mb-4">
          All structured documents (Excel spreadsheets, native PDF vector invoices, and B2B e-Invoice QR codes)
          are processed 100% deterministically and offline with zero AI/LLM calls, zero cost, and zero data leakage.
        </p>

        <div className="grid grid-cols-2 gap-3 text-xs">
          <div className="p-3 bg-slate-50 border border-slate-200 rounded-lg">
            <div className="font-semibold text-slate-800 flex items-center gap-1.5 mb-1">
              <span className="text-emerald-600">✓</span> B2B e-Invoice QR Scanner
            </div>
            <p className="text-slate-500">Decodes official NIC/IRP digital signatures with 100% exact math.</p>
          </div>

          <div className="p-3 bg-slate-50 border border-slate-200 rounded-lg">
            <div className="font-semibold text-slate-800 flex items-center gap-1.5 mb-1">
              <span className="text-emerald-600">✓</span> Digital PDF Vector Parser
            </div>
            <p className="text-slate-500">Extracts direct text streams and item tables without optical error.</p>
          </div>

          <div className="p-3 bg-slate-50 border border-slate-200 rounded-lg">
            <div className="font-semibold text-slate-800 flex items-center gap-1.5 mb-1">
              <span className="text-emerald-600">✓</span> Fuzzy Header Profiler
            </div>
            <p className="text-slate-500">Automatic column mapping for custom spreadsheets via token distance.</p>
          </div>

          <div className="p-3 bg-slate-50 border border-slate-200 rounded-lg">
            <div className="font-semibold text-slate-800 flex items-center gap-1.5 mb-1">
              <span className="text-emerald-600">✓</span> Local TrOCR &amp; Tesseract
            </div>
            <p className="text-slate-500">Offline transformer &amp; OCR for physical bill-book digit reading.</p>
          </div>
        </div>
      </section>

      {/* 2. Configurable AI Vision Provider (Exclusively for Handwritten Bills) */}
      <section className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm space-y-4">
        <div>
          <div className="flex items-center justify-between mb-1">
            <h2 className="text-sm font-semibold text-slate-900">Handwritten Bill Extraction (AI Vision Engine)</h2>
            <span className="text-xs px-2 py-0.5 rounded bg-violet-50 text-violet-700 font-medium border border-violet-200">
              Handwritten Crops Only
            </span>
          </div>
          <p className="text-xs text-slate-500">
            Configure an optional AI Vision model to boost accuracy when interpreting faint cursive handwriting,
            unconventional bill-book layouts, or low-contrast handwritten pen strokes. Human accountant verification
            (NEEDS_REVIEW) is always preserved before pushing to Tally.
          </p>
        </div>

        {/* Provider Selector */}
        <div>
          <label className="block text-xs font-semibold text-slate-700 mb-2">Select Vision Provider</label>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            {[
              { id: "offline", label: "100% Offline", badge: "Local TrOCR" },
              { id: "groq", label: "Groq Cloud", badge: "Fast Vision API" },
              { id: "gemini", label: "Google Gemini", badge: "Flash / Pro" },
              { id: "ollama", label: "Local Ollama", badge: "Private On-Prem" },
            ].map((p) => {
              const active = aiProvider === p.id;
              return (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => setAiProvider(p.id)}
                  className={`p-3 rounded-lg border text-left transition flex flex-col justify-between ${
                    active
                      ? "border-violet-600 bg-violet-50/50 ring-1 ring-violet-500"
                      : "border-slate-200 bg-white hover:bg-slate-50"
                  }`}
                >
                  <span className={`text-xs font-semibold ${active ? "text-violet-900" : "text-slate-800"}`}>
                    {p.label}
                  </span>
                  <span className={`text-[11px] mt-1 ${active ? "text-violet-600 font-medium" : "text-slate-400"}`}>
                    {p.badge}
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        {/* Dynamic Model Name Input */}
        <div className="p-4 rounded-lg bg-slate-50 border border-slate-200 space-y-3">
          <div>
            <label className="block text-xs font-semibold text-slate-700 mb-1">
              Active Vision Model Name / Tag (Customizable)
            </label>
            <p className="text-[11px] text-slate-500 mb-2">
              Type any exact model identifier or tag. Leave empty to use the provider's default recommended vision model.
            </p>
            <input
              type="text"
              value={aiModel}
              onChange={(e) => setAiModel(e.target.value)}
              placeholder={
                aiProvider === "groq"
                  ? "e.g. llama-3.2-90b-vision-preview or llama-3.2-11b-vision-preview"
                  : aiProvider === "gemini"
                  ? "e.g. gemini-1.5-flash or gemini-1.5-pro"
                  : aiProvider === "ollama"
                  ? "e.g. qwen2-vl, llava:13b, minicpm-v"
                  : "Using local Microsoft TrOCR small handwritten transformer"
              }
              disabled={aiProvider === "offline"}
              className="w-full border border-slate-300 rounded-lg px-3 py-2 text-xs font-mono bg-white text-slate-800 focus:outline-none focus:ring-1 focus:ring-violet-500 disabled:bg-slate-100 disabled:text-slate-400"
            />
          </div>

          {/* Quick preset tags */}
          {aiProvider !== "offline" && (
            <div className="flex flex-wrap items-center gap-1.5 pt-1 text-[11px]">
              <span className="text-slate-400 mr-1">Quick Select:</span>
              {aiProvider === "groq" && (
                <>
                  {["llama-3.2-90b-vision-preview", "llama-3.2-11b-vision-preview"].map((tag) => (
                    <button
                      key={tag}
                      type="button"
                      onClick={() => setAiModel(tag)}
                      className="px-2 py-0.5 rounded border border-slate-300 bg-white hover:border-violet-400 text-slate-600 hover:text-violet-700 font-mono transition"
                    >
                      {tag}
                    </button>
                  ))}
                </>
              )}
              {aiProvider === "gemini" && (
                <>
                  {["gemini-1.5-flash", "gemini-1.5-pro", "gemini-2.0-flash-exp"].map((tag) => (
                    <button
                      key={tag}
                      type="button"
                      onClick={() => setAiModel(tag)}
                      className="px-2 py-0.5 rounded border border-slate-300 bg-white hover:border-violet-400 text-slate-600 hover:text-violet-700 font-mono transition"
                    >
                      {tag}
                    </button>
                  ))}
                </>
              )}
              {aiProvider === "ollama" && (
                <>
                  {["qwen2-vl", "llava:latest", "llava:13b", "minicpm-v"].map((tag) => (
                    <button
                      key={tag}
                      type="button"
                      onClick={() => setAiModel(tag)}
                      className="px-2 py-0.5 rounded border border-slate-300 bg-white hover:border-violet-400 text-slate-600 hover:text-violet-700 font-mono transition"
                    >
                      {tag}
                    </button>
                  ))}
                </>
              )}
            </div>
          )}

          {/* Provider Specific Configuration Fields */}
          {aiProvider === "groq" && (
            <div className="pt-2 border-t border-slate-200">
              <label className="block text-xs font-semibold text-slate-700 mb-1">Groq API Key</label>
              <div className="flex items-center gap-2">
                <input
                  type="password"
                  value={groqApiKey}
                  onChange={(e) => setGroqApiKey(e.target.value)}
                  placeholder={groqKeySet ? `Configured (${groqKeyPreview}) — type to replace` : "gsk_..."}
                  className="w-full border border-slate-300 rounded-lg px-3 py-2 text-xs bg-white text-slate-800 focus:outline-none focus:ring-1 focus:ring-violet-500 font-mono"
                />
                {groqKeySet && (
                  <span className="shrink-0 text-[11px] text-emerald-600 font-medium px-2 py-1 bg-emerald-50 rounded border border-emerald-200">
                    ✓ Key Saved
                  </span>
                )}
              </div>
            </div>
          )}

          {aiProvider === "gemini" && (
            <div className="pt-2 border-t border-slate-200">
              <label className="block text-xs font-semibold text-slate-700 mb-1">Google Gemini API Key</label>
              <div className="flex items-center gap-2">
                <input
                  type="password"
                  value={geminiApiKey}
                  onChange={(e) => setGeminiApiKey(e.target.value)}
                  placeholder={geminiKeySet ? `Configured (${geminiKeyPreview}) — type to replace` : "AIzaSy..."}
                  className="w-full border border-slate-300 rounded-lg px-3 py-2 text-xs bg-white text-slate-800 focus:outline-none focus:ring-1 focus:ring-violet-500 font-mono"
                />
                {geminiKeySet && (
                  <span className="shrink-0 text-[11px] text-emerald-600 font-medium px-2 py-1 bg-emerald-50 rounded border border-emerald-200">
                    ✓ Key Saved
                  </span>
                )}
              </div>
            </div>
          )}

          {aiProvider === "ollama" && (
            <div className="pt-2 border-t border-slate-200">
              <label className="block text-xs font-semibold text-slate-700 mb-1">Local Ollama Endpoint</label>
              <input
                type="text"
                value={ollamaEndpoint}
                onChange={(e) => setOllamaEndpoint(e.target.value)}
                placeholder="http://localhost:11434"
                className="w-full border border-slate-300 rounded-lg px-3 py-2 text-xs bg-white text-slate-800 focus:outline-none focus:ring-1 focus:ring-violet-500 font-mono"
              />
              <p className="text-[11px] text-slate-400 mt-1">
                Ensure Ollama is running locally with vision support (e.g. <code>ollama run qwen2-vl</code>).
              </p>
            </div>
          )}
        </div>
      </section>

      {/* 3. Tally Prime Integration */}
      <section className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
        <h2 className="text-sm font-semibold text-slate-900 mb-1">Tally Prime XML Server Connection</h2>
        <p className="text-sm text-slate-500 mb-4">
          In Tally: F1 (Help) → Settings → Connectivity → Client/Server configuration → set
          "TallyPrime acts as" to <strong>Server</strong>, then match the port below (default 9000).
        </p>
        <div className="grid grid-cols-3 gap-3 mb-3">
          <div className="col-span-2">
            <label className="block text-xs font-medium text-slate-500 mb-1">Host</label>
            <input
              value={tallyHost}
              onChange={(e) => setTallyHost(e.target.value)}
              className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-violet-500"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-500 mb-1">Port</label>
            <input
              type="number"
              value={tallyPort}
              onChange={(e) => setTallyPort(e.target.value)}
              className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-violet-500"
            />
          </div>
        </div>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={testTally}
            disabled={testing}
            className="text-sm px-3 py-1.5 rounded-lg border border-slate-300 hover:bg-slate-50 disabled:opacity-50 transition"
          >
            {testing ? "Testing…" : "Test connection"}
          </button>
          {tallyTestResult === "connected" && (
            <span className="text-xs text-emerald-600 font-medium">✓ Connected to Tally XML Server</span>
          )}
          {tallyTestResult === "unreachable" && (
            <span className="text-xs text-rose-600">Couldn't reach Tally — make sure it's open and the server is enabled</span>
          )}
        </div>
      </section>

      {/* Save Action */}
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={save}
          disabled={saving}
          className="text-sm px-5 py-2.5 rounded-lg bg-violet-600 text-white font-medium hover:bg-violet-700 disabled:opacity-50 transition shadow-sm"
        >
          {saving ? "Saving…" : "Save Settings"}
        </button>
        {savedMsg && (
          <span className={`text-sm font-medium ${savedMsg.startsWith("Error") ? "text-rose-600" : "text-emerald-600"}`}>
            {savedMsg}
          </span>
        )}
      </div>
    </div>
  );
}
