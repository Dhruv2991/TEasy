import { useEffect, useState } from "react";
import { api } from "./api.js";

const GST_RATES = [0, 0.25, 3, 5, 12, 18, 28];

export default function GstSettingsPage() {
  const [loaded, setLoaded] = useState(false);
  const [companyName, setCompanyName] = useState("");
  const [gstin, setGstin] = useState("");
  const [stateCode, setStateCode] = useState("");
  const [defaultRate, setDefaultRate] = useState(18);
  const [saving, setSaving] = useState(false);
  const [savedMsg, setSavedMsg] = useState("");
  const [gstinError, setGstinError] = useState("");

  useEffect(() => {
    api.getSettings().then((s) => {
      setCompanyName(s.company_name || "");
      setGstin(s.gstin || "");
      setStateCode(s.state_code || "");
      setDefaultRate(s.default_gst_rate ?? 18);
      setLoaded(true);
    }).catch(() => setLoaded(true));
  }, []);

  const validateGstin = (value) => {
    if (!value) return "";
    // Standard 15-char GSTIN pattern: 2-digit state code + 10-char PAN + entity code + Z + checksum
    const pattern = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$/;
    return pattern.test(value.toUpperCase()) ? "" : "Doesn't look like a valid 15-character GSTIN";
  };

  const handleGstinChange = (value) => {
    const upper = value.toUpperCase();
    setGstin(upper);
    setGstinError(validateGstin(upper));
    // GSTIN's first 2 digits are the state code — auto-fill it as a convenience
    if (upper.length >= 2 && /^[0-9]{2}$/.test(upper.slice(0, 2))) {
      setStateCode(upper.slice(0, 2));
    }
  };

  const save = async () => {
    setSaving(true);
    setSavedMsg("");
    try {
      await api.saveSettings({
        company_name: companyName.trim(),
        gstin: gstin.trim(),
        state_code: stateCode.trim(),
        default_gst_rate: Number(defaultRate),
      });
      setSavedMsg("Saved.");
      setTimeout(() => setSavedMsg(""), 2500);
    } catch (e) {
      setSavedMsg(`Error: ${e.message}`);
    } finally {
      setSaving(false);
    }
  };

  if (!loaded) return <div className="p-6 text-slate-400">Loading…</div>;

  return (
    <div className="p-6 max-w-2xl space-y-6">
      <section className="bg-white rounded-xl border border-slate-200 p-5">
        <h2 className="text-sm font-semibold text-slate-900 mb-1">Company GST profile</h2>
        <p className="text-sm text-slate-500 mb-4">
          Informational for now — stored so it's ready for reports and future GST filing
          features. It doesn't change how bills are read or pushed to Tally today.
        </p>

        <div className="space-y-3">
          <div>
            <label className="block text-xs font-medium text-slate-500 mb-1">Company name</label>
            <input
              value={companyName}
              onChange={(e) => setCompanyName(e.target.value)}
              placeholder="e.g. Sharma Traders"
              className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-500 mb-1">GSTIN</label>
            <input
              value={gstin}
              onChange={(e) => handleGstinChange(e.target.value)}
              placeholder="e.g. 29ABCDE1234F1Z5"
              maxLength={15}
              className={`w-full border rounded-lg px-3 py-2 text-sm uppercase ${
                gstinError ? "border-rose-300" : "border-slate-300"
              }`}
            />
            {gstinError && <p className="text-xs text-rose-600 mt-1">{gstinError}</p>}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-500 mb-1">State code</label>
              <input
                value={stateCode}
                onChange={(e) => setStateCode(e.target.value)}
                placeholder="e.g. 29"
                maxLength={2}
                className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm"
              />
              <p className="text-xs text-slate-400 mt-1">Auto-filled from GSTIN if left blank.</p>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-500 mb-1">Default GST rate</label>
              <select
                value={defaultRate}
                onChange={(e) => setDefaultRate(e.target.value)}
                className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm bg-white"
              >
                {GST_RATES.map((r) => (
                  <option key={r} value={r}>{r}%</option>
                ))}
              </select>
              <p className="text-xs text-slate-400 mt-1">Used only as a suggested default where a rate can't be read.</p>
            </div>
          </div>
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
