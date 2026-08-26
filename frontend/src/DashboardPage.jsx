import { useEffect, useState, useCallback } from "react";
import { api } from "./api.js";
import { StatCard, StatusBadge, formatMoney } from "./ui.jsx";
import { Icon } from "./icons.jsx";
import UploadBox from "./UploadBox.jsx";

function DonutSummary({ approved, review, failed }) {
  const total = approved + review + failed || 1;
  const a = (approved / total) * 100;
  const r = (review / total) * 100;
  const f = (failed / total) * 100;
  const gradient = `conic-gradient(#10b981 0 ${a}%, #f59e0b ${a}% ${a + r}%, #f43f5e ${a + r}% ${a + r + f}%)`;

  return (
    <div className="flex items-center gap-6">
      <div
        className="w-32 h-32 rounded-full flex items-center justify-center shrink-0"
        style={{ background: gradient }}
      >
        <div className="w-20 h-20 rounded-full bg-white flex flex-col items-center justify-center">
          <div className="text-xl font-bold text-slate-900">{total}</div>
          <div className="text-[10px] text-slate-400">Total</div>
        </div>
      </div>
      <div className="space-y-2 text-sm">
        <div className="flex items-center gap-2"><span className="w-2.5 h-2.5 rounded-full bg-emerald-500" /> Success <span className="font-semibold ml-auto">{approved}</span></div>
        <div className="flex items-center gap-2"><span className="w-2.5 h-2.5 rounded-full bg-amber-500" /> Review <span className="font-semibold ml-auto">{review}</span></div>
        <div className="flex items-center gap-2"><span className="w-2.5 h-2.5 rounded-full bg-rose-500" /> Failed <span className="font-semibold ml-auto">{failed}</span></div>
      </div>
    </div>
  );
}

export default function DashboardPage({ onNavigate }) {
  const [documents, setDocuments] = useState([]);
  const [transactions, setTransactions] = useState([]);
  const [activity, setActivity] = useState([]);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    const [docs, txs, acts] = await Promise.all([
      api.listDocuments(),
      api.listTransactions(),
      api.getRecentActivity(8).catch(() => []),
    ]);
    setDocuments(docs);
    setTransactions(txs);
    setActivity(acts);
    setLoading(false);
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 6000);
    return () => clearInterval(t);
  }, [refresh]);

  const byType = (type) => transactions.filter((t) => t.type === type && t.status !== "REJECTED");
  const sumTotal = (txs) => txs.reduce((s, t) => s + (t.total_value || 0), 0);

  const sales = byType("SALES");
  const purchase = byType("PURCHASE");
  const debitNotes = transactions.filter((t) => t.type === "DEBIT_NOTE" || t.type === "CREDIT_NOTE");
  const pendingReview = transactions.filter((t) => t.status === "NEEDS_REVIEW").length;

  const approvedCount = transactions.filter((t) => t.status === "APPROVED").length;
  const reviewCount = transactions.filter((t) => t.status === "NEEDS_REVIEW").length;
  const rejectedCount = transactions.filter((t) => t.status === "REJECTED").length;

  // Bank rows whose reconciliation_status shows they don't yet line up with
  // a recorded sales/purchase invoice — see backend/app/reconciliation.py.
  // REJECTED bank rows are excluded, same as everywhere else on this page.
  const bankRows = transactions.filter((t) => t.type === "BANK" && t.status !== "REJECTED");
  const needsReconciliation = bankRows.filter(
    (t) => t.reconciliation_status === "UNMATCHED" || t.reconciliation_status === "AMBIGUOUS"
  );
  const needsReconciliationAmount = needsReconciliation.reduce(
    (s, t) => s + (t.credit > 0 ? t.credit : t.debit || 0),
    0
  );

  const handleUpload = async (file, type) => {
    await api.uploadDocument(file, type);
    refresh();
  };

  return (
    <div className="p-6 space-y-6">
      <div className="flex flex-wrap gap-4">
        <StatCard icon={Icon.Sales} color="green" label="Sales Vouchers" value={sales.length} sublabel={`₹${formatMoney(sumTotal(sales))}`} />
        <StatCard icon={Icon.Purchase} color="orange" label="Purchase Vouchers" value={purchase.length} sublabel={`₹${formatMoney(sumTotal(purchase))}`} />
        <StatCard icon={Icon.Gstr2b} color="purple" label="Debit / Credit Notes" value={debitNotes.length} sublabel={`₹${formatMoney(sumTotal(debitNotes))}`} />
        <StatCard icon={Icon.Transactions} color="blue" label="Total Transactions" value={transactions.length} sublabel={`₹${formatMoney(sumTotal(transactions))}`} />
        <StatCard icon={Icon.Alert} color="amber" label="Pending Review" value={pendingReview} sublabel="Requires your action" />
        {bankRows.length > 0 && (
          <StatCard
            icon={Icon.Alert}
            color="amber"
            label="Needs Reconciliation"
            value={needsReconciliation.length}
            sublabel={needsReconciliation.length ? `₹${formatMoney(needsReconciliationAmount)} unmatched` : "All bank entries reconciled"}
            onClick={() => onNavigate("bank")}
          />
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 bg-white rounded-xl border border-slate-200 p-5">
          <h2 className="font-semibold text-slate-900 mb-1">Upload & Process Documents</h2>
          <p className="text-sm text-slate-500 mb-4">Add your documents to extract and create Tally vouchers automatically</p>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div className="border border-slate-200 rounded-lg p-4">
              <div className="w-9 h-9 rounded-lg bg-emerald-50 text-emerald-600 flex items-center justify-center mb-2"><Icon.Sales width={18} height={18} /></div>
              <div className="text-sm font-medium text-slate-900">Sales Bills</div>
              <p className="text-xs text-slate-500 mb-3">Photos of handwritten sales bills (multiple bills in one image supported)</p>
              <UploadBox label="Upload / Scan" onUpload={(f) => handleUpload(f, "SALES")} />
            </div>
            <div className="border border-slate-200 rounded-lg p-4">
              <div className="w-9 h-9 rounded-lg bg-orange-50 text-orange-600 flex items-center justify-center mb-2"><Icon.Purchase width={18} height={18} /></div>
              <div className="text-sm font-medium text-slate-900">Purchase Bills</div>
              <p className="text-xs text-slate-500 mb-3">Purchase invoices from suppliers / companies</p>
              <UploadBox label="Upload / Scan" onUpload={(f) => handleUpload(f, "PURCHASE")} />
            </div>
            <div className="border border-slate-200 rounded-lg p-4">
              <div className="w-9 h-9 rounded-lg bg-violet-50 text-violet-600 flex items-center justify-center mb-2"><Icon.Gstr2b width={18} height={18} /></div>
              <div className="text-sm font-medium text-slate-900">GSTR-2B (Discount)</div>
              <p className="text-xs text-slate-500 mb-3">Import GSTR-2B Excel file to fetch credit/debit notes</p>
              <UploadBox
                label="Import Excel"
                accept=".xlsx,.xls"
                onUpload={async (f) => {
                  await api.uploadGstr2b(f);
                  refresh();
                }}
              />
            </div>
          </div>
          <div className="mt-4 text-xs bg-blue-50 text-blue-700 rounded-lg px-3 py-2">
            Tip: Ensure images are clear and well-lit for better accuracy
          </div>
        </div>

        <div className="bg-white rounded-xl border border-slate-200 p-5">
          <h2 className="font-semibold text-slate-900 mb-4">Processing Summary</h2>
          <DonutSummary approved={approvedCount} review={reviewCount} failed={rejectedCount} />
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 bg-white rounded-xl border border-slate-200 p-5">
          <div className="flex items-center justify-between mb-3">
            <h2 className="font-semibold text-slate-900">Recent Documents</h2>
            <button onClick={() => onNavigate("documents")} className="text-sm text-indigo-600 hover:underline">View All Documents →</button>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-slate-400 uppercase border-b border-slate-100">
                  <th className="py-2">Document</th>
                  <th className="py-2">Type</th>
                  <th className="py-2">Uploaded</th>
                  <th className="py-2">Status</th>
                  <th className="py-2">Extracted</th>
                </tr>
              </thead>
              <tbody>
                {documents.slice(0, 6).map((d) => (
                  <tr key={d.id} className="border-b border-slate-50">
                    <td className="py-2 font-medium text-slate-800">{d.file_name}</td>
                    <td className="py-2 text-slate-500">{d.document_type}</td>
                    <td className="py-2 text-slate-500">{new Date(d.uploaded_at).toLocaleString()}</td>
                    <td className="py-2"><StatusBadge status={d.status} /></td>
                    <td className="py-2 text-slate-500">{d.bills.filter((b) => b.transaction).length} / {d.bills.length}</td>
                  </tr>
                ))}
                {!documents.length && !loading && (
                  <tr><td colSpan={5} className="py-6 text-center text-slate-400">No documents uploaded yet.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="bg-white rounded-xl border border-slate-200 p-5">
          <h2 className="font-semibold text-slate-900 mb-3">Recent Activity</h2>
          <div className="space-y-3 max-h-80 overflow-y-auto">
            {activity.map((a, i) => (
              <div key={i} className="text-sm">
                <p className="text-slate-700 leading-snug">{a.message}</p>
                <p className="text-xs text-slate-400">{new Date(a.time).toLocaleTimeString()}</p>
              </div>
            ))}
            {!activity.length && <p className="text-sm text-slate-400">No activity yet.</p>}
          </div>
        </div>
      </div>
    </div>
  );
}
