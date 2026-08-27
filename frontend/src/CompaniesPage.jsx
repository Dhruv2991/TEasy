import { useEffect, useState } from "react";
import { api } from "./api.js";
import { Icon } from "./icons.jsx";

const EMPTY_FORM = { name: "", gstin: "", state_code: "", default_gst_rate: 18.0, tally_company_name: "" };

function CompanyForm({ initial, onCancel, onSave, saving }) {
  const [form, setForm] = useState(initial || EMPTY_FORM);
  const set = (field) => (e) => setForm((f) => ({ ...f, [field]: e.target.value }));

  return (
    <div className="bg-white border border-slate-200 rounded-xl p-5 space-y-4">
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-xs font-medium text-slate-500 mb-1">Company name *</label>
          <input
            value={form.name}
            onChange={set("name")}
            placeholder="e.g. Ramesh Traders"
            className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-slate-500 mb-1">
            Tally company name
            <span className="text-slate-400 font-normal ml-1">(exact name as it exists in Tally)</span>
          </label>
          <input
            value={form.tally_company_name || ""}
            onChange={set("tally_company_name")}
            placeholder="Leave blank if same as above"
            className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-slate-500 mb-1">GSTIN</label>
          <input
            value={form.gstin || ""}
            onChange={set("gstin")}
            placeholder="29AAAAA0000A1Z5"
            className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-slate-500 mb-1">State code</label>
          <input
            value={form.state_code || ""}
            onChange={set("state_code")}
            placeholder="e.g. 29"
            className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-slate-500 mb-1">Default GST rate (%)</label>
          <input
            type="number"
            step="0.01"
            value={form.default_gst_rate}
            onChange={(e) => setForm((f) => ({ ...f, default_gst_rate: parseFloat(e.target.value) || 0 }))}
            className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm"
          />
        </div>
      </div>
      <p className="text-xs text-slate-400">
        "Tally company name" is what gets stamped into every voucher pushed while this company is
        active — it must match the company's exact name inside Tally, or the push will fail.
      </p>
      <div className="flex justify-end gap-2">
        <button
          onClick={onCancel}
          disabled={saving}
          className="text-sm font-medium border border-slate-300 px-4 py-2 rounded-lg hover:bg-slate-50 disabled:opacity-50"
        >
          Cancel
        </button>
        <button
          onClick={() => onSave(form)}
          disabled={saving || !form.name.trim()}
          className="text-sm font-medium bg-indigo-600 text-white px-4 py-2 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
        >
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}

export default function CompaniesPage({ onCompanyChange }) {
  const [companies, setCompanies] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [saving, setSaving] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const [busyId, setBusyId] = useState(null);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const [list, active] = await Promise.all([
        api.listCompanies(showArchived),
        api.getActiveCompany(),
      ]);
      setCompanies(list);
      setActiveId(active?.id || null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showArchived]);

  const handleCreate = async (form) => {
    setSaving(true);
    try {
      await api.createCompany(form);
      setCreating(false);
      await load();
    } catch (e) {
      alert(`Couldn't create company: ${e.message}`);
    } finally {
      setSaving(false);
    }
  };

  const handleUpdate = async (id, form) => {
    setSaving(true);
    try {
      await api.updateCompany(id, form);
      setEditingId(null);
      await load();
      // If we just edited the ACTIVE company (e.g. changed its Tally
      // name), downstream pages showing that name should reflect it.
      if (id === activeId) onCompanyChange?.();
    } catch (e) {
      alert(`Couldn't save changes: ${e.message}`);
    } finally {
      setSaving(false);
    }
  };

  const handleActivate = async (id) => {
    setBusyId(id);
    try {
      await api.activateCompany(id);
      await load();
      onCompanyChange?.();
    } catch (e) {
      alert(`Couldn't switch company: ${e.message}`);
    } finally {
      setBusyId(null);
    }
  };

  const handleArchive = async (id) => {
    if (!confirm("Archive this company? Its documents and transactions stay exactly as they are — this just hides it from the switcher.")) return;
    setBusyId(id);
    try {
      await api.archiveCompany(id);
      await load();
      onCompanyChange?.();
    } catch (e) {
      alert(`Couldn't archive: ${e.message}`);
    } finally {
      setBusyId(null);
    }
  };

  const handleUnarchive = async (id) => {
    setBusyId(id);
    try {
      await api.unarchiveCompany(id);
      await load();
    } catch (e) {
      alert(`Couldn't unarchive: ${e.message}`);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-slate-500 max-w-2xl">
          One install can keep books for several businesses — a bookkeeper managing multiple
          clients, or one owner with multiple firms. Exactly one company is "active" at a time;
          every upload, transaction, and Tally push is scoped to whichever one is open.
        </p>
        {!creating && (
          <button
            onClick={() => setCreating(true)}
            className="shrink-0 text-sm font-medium bg-indigo-600 text-white px-4 py-2 rounded-lg hover:bg-indigo-700"
          >
            + New company
          </button>
        )}
      </div>

      {creating && (
        <CompanyForm
          onCancel={() => setCreating(false)}
          onSave={handleCreate}
          saving={saving}
        />
      )}

      {error && (
        <div className="bg-rose-50 border border-rose-200 rounded-xl p-4 text-sm text-rose-700">
          Couldn't load companies: {error}
        </div>
      )}

      {loading ? (
        <div className="text-slate-400 text-sm">Loading…</div>
      ) : (
        <div className="space-y-3">
          {companies.map((c) =>
            editingId === c.id ? (
              <CompanyForm
                key={c.id}
                initial={{
                  name: c.name,
                  gstin: c.gstin || "",
                  state_code: c.state_code || "",
                  default_gst_rate: c.default_gst_rate,
                  tally_company_name: c.tally_company_name || "",
                }}
                onCancel={() => setEditingId(null)}
                onSave={(form) => handleUpdate(c.id, form)}
                saving={saving}
              />
            ) : (
              <div
                key={c.id}
                className={`bg-white border rounded-xl p-4 flex items-center justify-between gap-4 ${c.id === activeId ? "border-indigo-300 ring-1 ring-indigo-100" : "border-slate-200"}`}
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-slate-900 truncate">{c.name}</span>
                    {c.id === activeId && (
                      <span className="text-[10px] font-semibold bg-indigo-100 text-indigo-700 px-1.5 py-0.5 rounded-full shrink-0">
                        ACTIVE
                      </span>
                    )}
                    {c.archived && (
                      <span className="text-[10px] font-semibold bg-slate-100 text-slate-500 px-1.5 py-0.5 rounded-full shrink-0">
                        ARCHIVED
                      </span>
                    )}
                  </div>
                  <div className="text-xs text-slate-400 mt-0.5 truncate">
                    {c.gstin || "No GSTIN set"} · Tally name: {c.tally_company_name || c.name}
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  {!c.archived && c.id !== activeId && (
                    <button
                      onClick={() => handleActivate(c.id)}
                      disabled={busyId === c.id}
                      className="text-xs font-medium border border-slate-300 px-3 py-1.5 rounded-lg hover:bg-slate-50 disabled:opacity-50"
                    >
                      Switch to this
                    </button>
                  )}
                  <button
                    onClick={() => setEditingId(c.id)}
                    disabled={busyId === c.id}
                    className="text-xs font-medium border border-slate-300 px-3 py-1.5 rounded-lg hover:bg-slate-50 disabled:opacity-50"
                  >
                    Edit
                  </button>
                  {c.archived ? (
                    <button
                      onClick={() => handleUnarchive(c.id)}
                      disabled={busyId === c.id}
                      className="text-xs font-medium text-indigo-600 hover:text-indigo-800 disabled:opacity-50"
                    >
                      Unarchive
                    </button>
                  ) : (
                    <button
                      onClick={() => handleArchive(c.id)}
                      disabled={busyId === c.id}
                      className="text-xs font-medium text-rose-600 hover:text-rose-800 disabled:opacity-50"
                    >
                      Archive
                    </button>
                  )}
                </div>
              </div>
            )
          )}
        </div>
      )}

      <button
        onClick={() => setShowArchived((v) => !v)}
        className="text-xs font-medium text-slate-400 hover:text-slate-600"
      >
        {showArchived ? "Hide archived companies" : "Show archived companies"}
      </button>
    </div>
  );
}
