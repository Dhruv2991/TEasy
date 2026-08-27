import { useEffect, useState, useCallback } from "react";
import { api } from "./api.js";
import { StatCard, formatMoney } from "./ui.jsx";
import { Icon } from "./icons.jsx";

const DOC_TYPES = [
  { value: "", label: "All types" },
  { value: "SALES", label: "Sales" },
  { value: "PURCHASE", label: "Purchase" },
  { value: "GSTR2B", label: "GSTR-2B (Discount Notes)" },
  { value: "BANK", label: "Bank" },
];

const money = (n) => "₹" + formatMoney(n);

export default function ReportsPage() {
  const [docType, setDocType] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [state, setState] = useState("");
  const [availableStates, setAvailableStates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [summary, setSummary] = useState(null);
  const [byMonth, setByMonth] = useState([]);
  const [byParty, setByParty] = useState([]);
  const [byRate, setByRate] = useState([]);
  const [byState, setByState] = useState([]);
  const [error, setError] = useState("");

  // States are independent of the current filter selection — always show
  // every state that has ever had a transaction, so switching the state
  // filter itself doesn't make other options disappear from the dropdown.
  useEffect(() => {
    api.getReportStates().then(setAvailableStates).catch(() => {});
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const params = {
        doc_type: docType || undefined,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
        state: state || undefined,
      };
      const [s, m, p, r, st] = await Promise.all([
        api.getReportSummary(params),
        api.getReportByMonth(params),
        api.getReportByParty({ ...params, limit: 10 }),
        api.getReportByGstRate(params),
        api.getReportByState({ doc_type: params.doc_type, date_from: params.date_from, date_to: params.date_to }),
      ]);
      setSummary(s);
      setByMonth(m);
      setByParty(p);
      setByRate(r);
      setByState(st);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [docType, dateFrom, dateTo, state]);

  useEffect(() => {
    load();
  }, [load]);

  const hasActiveFilters = docType || dateFrom || dateTo || state;
  const clearFilters = () => {
    setDocType("");
    setDateFrom("");
    setDateTo("");
    setState("");
  };

  return (
    <div className="p-6 space-y-6">
      <div className="bg-white border border-slate-200 rounded-xl p-4 flex flex-wrap items-end gap-3">
        <div>
          <label className="block text-xs font-medium text-slate-500 mb-1">Type</label>
          <select
            value={docType}
            onChange={(e) => setDocType(e.target.value)}
            className="border border-slate-300 rounded-lg px-3 py-2 text-sm bg-white"
          >
            {DOC_TYPES.map((t) => (
              <option key={t.value} value={t.value}>{t.label}</option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-xs font-medium text-slate-500 mb-1">From date</label>
          <input
            type="date"
            value={dateFrom}
            onChange={(e) => setDateFrom(e.target.value)}
            className="border border-slate-300 rounded-lg px-3 py-2 text-sm bg-white"
          />
        </div>

        <div>
          <label className="block text-xs font-medium text-slate-500 mb-1">To date</label>
          <input
            type="date"
            value={dateTo}
            onChange={(e) => setDateTo(e.target.value)}
            className="border border-slate-300 rounded-lg px-3 py-2 text-sm bg-white"
          />
        </div>

        <div>
          <label className="block text-xs font-medium text-slate-500 mb-1">
            State
            <span className="text-slate-400 font-normal ml-1">(GSTR-2B/purchase only)</span>
          </label>
          <select
            value={state}
            onChange={(e) => setState(e.target.value)}
            className="border border-slate-300 rounded-lg px-3 py-2 text-sm bg-white min-w-[160px]"
          >
            <option value="">All states</option>
            {availableStates.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>

        {hasActiveFilters && (
          <button
            onClick={clearFilters}
            className="text-xs font-medium text-indigo-600 hover:text-indigo-800 px-2 py-2"
          >
            Clear filters
          </button>
        )}

        <p className="text-xs text-slate-400 ml-auto self-center">
          Approved &amp; pending-review transactions only — rejected rows are excluded.
        </p>
      </div>

      {error && (
        <div className="bg-rose-50 border border-rose-200 rounded-xl p-4 text-sm text-rose-700">
          Couldn't load reports: {error}
        </div>
      )}

      {loading && !summary ? (
        <div className="text-slate-400 text-sm">Loading…</div>
      ) : summary && (
        <>
          <div className="flex flex-wrap gap-4">
            <StatCard icon={Icon.Reports} color="purple" label="Total value" value={money(summary.total_value)} sublabel={`${summary.count} transaction(s)`} />
            <StatCard icon={Icon.Percent} color="blue" label="Taxable value" value={money(summary.taxable_value)} />
            <StatCard icon={Icon.Alert} color="amber" label="Needs review" value={summary.needs_review} />
            <StatCard icon={Icon.Check} color="green" label="Approved" value={summary.approved} />
          </div>

          <div className="bg-white rounded-xl border border-slate-200 p-5">
            <h2 className="font-semibold text-slate-900 mb-1">Tax breakup</h2>
            <p className="text-sm text-slate-500 mb-4">Sum of CGST, SGST, IGST and cess across the selected transactions.</p>
            <div className="grid grid-cols-4 gap-4 text-sm">
              <div><div className="text-slate-400">CGST</div><div className="font-semibold text-slate-900">{money(summary.cgst)}</div></div>
              <div><div className="text-slate-400">SGST</div><div className="font-semibold text-slate-900">{money(summary.sgst)}</div></div>
              <div><div className="text-slate-400">IGST</div><div className="font-semibold text-slate-900">{money(summary.igst)}</div></div>
              <div><div className="text-slate-400">Cess</div><div className="font-semibold text-slate-900">{money(summary.cess)}</div></div>
            </div>
          </div>

          <div className="bg-white rounded-xl border border-slate-200 p-5">
            <h2 className="font-semibold text-slate-900 mb-3">By month</h2>
            {byMonth.length === 0 ? (
              <p className="text-sm text-slate-400">No dated transactions in the selected filters.</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-slate-400 border-b border-slate-100">
                    <th className="py-2 font-medium">Month</th>
                    <th className="py-2 font-medium text-right">Count</th>
                    <th className="py-2 font-medium text-right">Taxable value</th>
                    <th className="py-2 font-medium text-right">Total value</th>
                  </tr>
                </thead>
                <tbody>
                  {byMonth.map((row) => (
                    <tr key={row.month} className="border-b border-slate-50 last:border-0">
                      <td className="py-2 text-slate-700">{row.month}</td>
                      <td className="py-2 text-right text-slate-500">{row.count}</td>
                      <td className="py-2 text-right text-slate-700">{money(row.taxable_value)}</td>
                      <td className="py-2 text-right font-medium text-slate-900">{money(row.total_value)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          <div className="grid grid-cols-2 gap-6">
            <div className="bg-white rounded-xl border border-slate-200 p-5">
              <h2 className="font-semibold text-slate-900 mb-3">Top parties</h2>
              {byParty.length === 0 ? (
                <p className="text-sm text-slate-400">No transactions in the selected filters.</p>
              ) : (
                <ul className="space-y-2">
                  {byParty.map((row) => (
                    <li key={row.party} className="flex items-center justify-between text-sm">
                      <span className="text-slate-700 truncate max-w-[60%]">{row.party}</span>
                      <span className="font-medium text-slate-900">{money(row.total_value)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className="bg-white rounded-xl border border-slate-200 p-5">
              <h2 className="font-semibold text-slate-900 mb-3">By GST rate</h2>
              {byRate.length === 0 ? (
                <p className="text-sm text-slate-400">No transactions in the selected filters.</p>
              ) : (
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-slate-400 border-b border-slate-100">
                      <th className="py-2 font-medium">Rate</th>
                      <th className="py-2 font-medium text-right">Taxable value</th>
                      <th className="py-2 font-medium text-right">Count</th>
                    </tr>
                  </thead>
                  <tbody>
                    {byRate.map((row) => (
                      <tr key={row.gst_rate} className="border-b border-slate-50 last:border-0">
                        <td className="py-2 text-slate-700">{row.gst_rate}%</td>
                        <td className="py-2 text-right text-slate-700">{money(row.taxable_value)}</td>
                        <td className="py-2 text-right text-slate-500">{row.count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>

          <div className="bg-white rounded-xl border border-slate-200 p-5">
            <h2 className="font-semibold text-slate-900 mb-1">By state</h2>
            <p className="text-sm text-slate-500 mb-4">
              Only meaningful for GSTR-2B purchases and credit/debit notes, since those are the only
              rows with a GSTIN to derive a state from — plain OCR'd sales/purchases fall under "Unknown".
            </p>
            {byState.length === 0 ? (
              <p className="text-sm text-slate-400">No transactions in the selected filters.</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-slate-400 border-b border-slate-100">
                    <th className="py-2 font-medium">State</th>
                    <th className="py-2 font-medium text-right">Count</th>
                    <th className="py-2 font-medium text-right">Taxable value</th>
                    <th className="py-2 font-medium text-right">Total value</th>
                  </tr>
                </thead>
                <tbody>
                  {byState.map((row) => (
                    <tr key={row.state} className="border-b border-slate-50 last:border-0">
                      <td className="py-2 text-slate-700">
                        {row.state}
                        {row.state === "Unknown" && (
                          <span className="ml-1.5 text-[10px] text-slate-400">(no GSTIN on file)</span>
                        )}
                      </td>
                      <td className="py-2 text-right text-slate-500">{row.count}</td>
                      <td className="py-2 text-right text-slate-700">{money(row.taxable_value)}</td>
                      <td className="py-2 text-right font-medium text-slate-900">{money(row.total_value)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </div>
  );
}
