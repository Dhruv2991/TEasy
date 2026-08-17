import { useState } from "react";
import { api } from "./api.js";

export default function FirstRunSetup({ onDone }) {
  const [groqKey, setGroqKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const save = async () => {
    if (!groqKey.trim()) {
      setError("Paste your Groq API key to continue.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await api.saveSettings({ groq_api_key: groqKey.trim() });
      onDone();
    } catch (e) {
      setError(e.message || "Something went wrong saving the key.");
    } finally {
      setSaving(false);
    }
  };

  const skip = () => onDone();

  return (
    <div className="min-h-screen bg-slate-50 flex items-center justify-center p-6">
      <div className="w-full max-w-md bg-white rounded-2xl border border-slate-200 shadow-sm p-8">
        <div className="w-12 h-12 rounded-xl bg-violet-600 text-white flex items-center justify-center text-lg font-bold mb-5">
          T
        </div>
        <h1 className="text-xl font-semibold text-slate-900 mb-1">Welcome to TEasy</h1>
        <p className="text-sm text-slate-500 mb-6">
          One quick step before you start — TEasy uses Groq's AI to read your bills
          accurately, including handwriting. It's free to get a key.
        </p>

        <label className="block text-xs font-medium text-slate-500 mb-1">Groq API key</label>
        <input
          type="password"
          autoFocus
          value={groqKey}
          onChange={(e) => setGroqKey(e.target.value)}
          placeholder="gsk_..."
          className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm mb-1"
        />
        {error && <p className="text-xs text-rose-600 mb-2">{error}</p>}
        <a
          href="https://console.groq.com/keys"
          target="_blank"
          rel="noreferrer"
          className="text-xs text-violet-600 underline"
        >
          Get a free key at console.groq.com/keys
        </a>

        <div className="flex items-center gap-3 mt-6">
          <button
            onClick={save}
            disabled={saving}
            className="flex-1 text-sm px-4 py-2.5 rounded-lg bg-violet-600 text-white hover:bg-violet-700 disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save and continue"}
          </button>
          <button
            onClick={skip}
            className="text-sm px-4 py-2.5 rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-50"
          >
            Skip for now
          </button>
        </div>
        <p className="text-xs text-slate-400 mt-4">
          You can add or change this anytime in General Settings. Without a key, TEasy
          still works using a less accurate built-in OCR fallback.
        </p>
      </div>
    </div>
  );
}
