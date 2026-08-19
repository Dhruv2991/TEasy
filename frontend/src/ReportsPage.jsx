import { useEffect, useState, useCallback } from "react";
import { api } from "./api.js";
import { StatCard } from "./ui.jsx";
import { Icon } from "./icons.jsx";

const DOC_TYPES = [
  { value: "", label: "All types" },
  { value: "SALES", label: "Sales" },
  { value: "PURCHASE", label: "Purchase" },
  { value: "GSTR2B", label: "GSTR-2B" },
];

const money = (n) =>
  "₹" + (n ?? 0).toLocaleString("en-IN", { maximumFractionDigits: 0 });

export default function ReportsPage() {
  const [docType, setDocType] = useState("");
  const [loading, setLoading] = useState(true);
  const [summary, setSummary] = useState(null);
  const [byMonth, setByMonth] = useState([]);
  const [byParty, setByParty] = useState([]);
  const [byRate, setByRate] = useState([]);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const params = { doc_type: docType };
      const [s, m, p, r] = await Promise.all([
        api.getReportSummary(params),
        api.getReportByMonth(params),
        api.getReportByParty({ ...params, limit: 10 }),
        api.getReportByGstRate(params),
      ]);
      setSummary(s);
      setByMonth(m);
      setByParty(p);
      setByRate(r);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [docType]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center gap-3">
        <select
          value={docType}
          onChange={(e) => setDocType(e.target.value)}
          className="border border-slate-300 rounded-lg px-3 py-2 text-sm bg-white"
        >
          {DOC_TYPES.map((t) => (
            <option key={t.value} value={t.value}>{t.label}</option>
          ))}
        </select>
        <p className="text-xs text-slate-400">
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
              <p className="text-sm text-slate-400">No dated transactions yet.</p>
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
                <p className="text-sm text-slate-400">No transactions yet.</p>
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
                <p className="text-sm text-slate-400">No transactions yet.</p>
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
        </>
      )}
    </div>
  );
}
