import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import { Icon } from "./icons.jsx";

/**
 * Dropdown in the top bar showing the currently active company, with a
 * list to switch to another and a "Manage companies" link. Switching
 * calls onSwitched() so the parent can force the current page to refetch
 * — every list/report/reconciliation endpoint now scopes to whichever
 * company is active, so a stale page after switching would show the
 * WRONG company's data sitting on screen until the user manually
 * refreshed, which defeats the point of switching at all.
 */
export default function CompanySwitcher({ onNavigate, onSwitched }) {
  const [open, setOpen] = useState(false);
  const [companies, setCompanies] = useState([]);
  const [active, setActive] = useState(null);
  const [switching, setSwitching] = useState(false);
  const [error, setError] = useState("");
  const ref = useRef(null);

  const load = () => {
    Promise.all([api.listCompanies(), api.getActiveCompany()])
      .then(([list, activeCompany]) => {
        setCompanies(list);
        setActive(activeCompany);
      })
      .catch(() => {});
  };

  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    if (!open) return;
    const handler = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const switchTo = async (companyId) => {
    if (switching || companyId === active?.id) {
      setOpen(false);
      return;
    }
    setSwitching(true);
    setError("");
    try {
      const updated = await api.activateCompany(companyId);
      setActive(updated);
      setOpen(false);
      onSwitched?.();
    } catch (e) {
      setError(e.message);
    } finally {
      setSwitching(false);
    }
  };

  // No companies at all yet (shouldn't normally happen — database.py
  // bootstraps a default on first run — but don't render a broken/empty
  // switcher if it somehow does).
  if (companies.length === 0 && !active) return null;

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 text-sm text-slate-700 border border-slate-200 rounded-lg px-3 py-1.5 hover:bg-slate-50 transition-colors"
      >
        <span className="w-2 h-2 rounded-full bg-indigo-500 shrink-0" />
        <span className="max-w-[160px] truncate">{active?.name || "Select company"}</span>
        <Icon.Chevron width={14} height={14} className={`transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <div className="absolute right-0 mt-1 w-64 bg-white border border-slate-200 rounded-xl shadow-lg z-50 py-1">
          <div className="px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
            Switch company
          </div>
          {companies.map((c) => (
            <button
              key={c.id}
              onClick={() => switchTo(c.id)}
              disabled={switching}
              className={`w-full text-left px-3 py-2 text-sm flex items-center justify-between gap-2 hover:bg-slate-50 disabled:opacity-50 ${c.id === active?.id ? "bg-indigo-50" : ""}`}
            >
              <span className="truncate">{c.name}</span>
              {c.id === active?.id && <Icon.Check width={14} height={14} className="text-indigo-600 shrink-0" />}
            </button>
          ))}
          {error && <div className="px-3 py-1.5 text-xs text-rose-600">{error}</div>}
          <div className="border-t border-slate-100 mt-1 pt-1">
            <button
              onClick={() => {
                setOpen(false);
                onNavigate?.("companies");
              }}
              className="w-full text-left px-3 py-2 text-sm text-indigo-600 hover:bg-slate-50"
            >
              Manage companies
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
