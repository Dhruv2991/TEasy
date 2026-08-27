import { useEffect, useState, useCallback } from "react";
import { api } from "./api.js";
import UploadBox from "./UploadBox.jsx";
import ReviewTable from "./ReviewTable.jsx";

// Purchase invoices with items at more than one GST rate ("mixed-rate")
// can't be resolved from GSTR-2B alone — it only gives one taxable/tax
// total per invoice. This box compares the GSTR-2B import already on file
// against the shop's own purchase register (its billing software's export,
// one row per invoice with a per-rate breakup) and resolves every
// mixed-rate invoice it can match in one pass, instead of handling them
// one at a time.
function PurchaseRegisterCompare({ onResolved }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [showUnmatched, setShowUnmatched] = useState(false);

  const handleFile = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setBusy(true);
    setError("");
    setResult(null);
    setShowUnmatched(false);
    try {
      const res = await api.matchPurchaseRegister(file);
      setResult(res);
      await onResolved();
    } catch (err) {
      setError(err.message || "Compare failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="bg-white rounded-xl border border-slate-200 p-5">
      <h2 className="font-semibold text-slate-900 mb-1">Compare with purchase register</h2>
      <p className="text-sm text-slate-500 mb-4">
        Upload the shop's own purchase register (the GST rate-breakup export from your billing
        software — one row per invoice, split into Value@5%/CGST@2.5%/SGST@2.5%, Value@12%, etc.).
        Every purchase invoice already imported from GSTR-2B that's flagged{" "}
        <span className="font-semibold text-amber-700">RATE UNCERTAIN</span> (mixed GST rates on one
        bill) will be matched by invoice number, cross-checked on totals, and resolved into the
        correct per-rate split for Tally — all in one pass.
      </p>
      <label
        className={`inline-flex items-center gap-2 text-sm font-medium border border-slate-300 rounded-lg px-3 py-2 cursor-pointer hover:bg-slate-50 ${
          busy ? "opacity-50 cursor-not-allowed" : ""
        }`}
      >
        <input type="file" accept=".xlsx,.xls" className="hidden" onChange={handleFile} disabled={busy} />
        {busy ? "Comparing…" : "Upload purchase register (.xlsx / .xls)"}
      </label>

      {error && <p className="mt-3 text-sm text-rose-600">⚠ {error}</p>}

      {result && (
        <div className="mt-4 text-sm bg-slate-50 border border-slate-200 rounded-lg p-3 space-y-1">
          <p>
            <span className="font-semibold text-emerald-700">{result.resolved}</span> mixed-rate invoice(s)
            resolved out of <span className="font-semibold">{result.uncertain_before}</span> that were
            uncertain.
          </p>
          {result.still_uncertain > 0 && (
            <p className="text-amber-700">
              {result.still_uncertain} invoice(s) are still uncertain — not found in this register
              file, or their totals didn't match closely enough to trust.
            </p>
          )}
          {result.unmatched_register_rows > 0 && (
            <div className="text-slate-600">
              <p>
                <span className="font-semibold text-amber-700">{result.unmatched_register_rows}</span> invoice(s)
                in your register weren't found in GSTR-2B —{" "}
                <button
                  type="button"
                  onClick={() => setShowUnmatched((v) => !v)}
                  className="underline underline-offset-2 text-indigo-600 hover:text-indigo-800 font-medium"
                >
                  {showUnmatched ? "hide" : "see which ones"}
                </button>
              </p>
              {showUnmatched && (
                <div className="mt-2 border border-amber-200 bg-amber-50 rounded-lg overflow-hidden">
                  <div className="px-3 py-2 text-xs text-amber-800 border-b border-amber-200">
                    These are invoices you already paid and recorded, but GSTR-2B doesn't show them yet —
                    usually because the supplier hasn't uploaded/filed that invoice on GSTN. Input tax
                    credit on these isn't currently claimable until they do; worth following up with the
                    supplier before this filing period closes.
                  </div>
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-left text-amber-700">
                        <th className="px-3 py-1.5 font-medium">Supplier</th>
                        <th className="px-3 py-1.5 font-medium">Invoice #</th>
                        <th className="px-3 py-1.5 font-medium">Date</th>
                        <th className="px-3 py-1.5 font-medium text-right">Amount</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.unmatched_register_rows_detail?.map((row, i) => (
                        <tr key={i} className="border-t border-amber-200/60">
                          <td className="px-3 py-1.5">{row.supplier_name || "—"}</td>
                          <td className="px-3 py-1.5">{row.invoice_number}</td>
                          <td className="px-3 py-1.5">{row.invoice_date || "—"}</td>
                          <td className="px-3 py-1.5 text-right">
                            ₹{row.total_value?.toLocaleString("en-IN", { minimumFractionDigits: 2 })}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

const CONFIG = {
  sales: {
    docType: "SALES",
    label: "Sales Bills",
    accept: "image/*,.pdf,.xlsx,.xls",
    uploadLabel: "Upload a sales bill photo/PDF, or import a sales register Excel file",
  },
  purchase: {
    docType: "PURCHASE",
    label: "Purchase Bills",
    accept: "image/*,.pdf,.xlsx,.xls",
    uploadLabel: "Upload a purchase bill photo/PDF, a bill-list Excel, or import a GSTR-2B B2B Excel file",
  },
  gstr2b: { docType: "GSTR2B", label: "GSTR-2B (Discount Notes)", accept: ".xlsx,.xls", uploadLabel: "Import GSTR-2B Excel file" },
  bank: {
    docType: "BANK",
    label: "Bank Statements",
    accept: ".pdf",
    uploadLabel: "Upload a bank statement PDF — each transaction is parsed into a row you can review and approve, same as Sales/Purchase, before it's pushed to Tally",
  },
};

export default function DocumentTypePage({ pageKey }) {
  const cfg = CONFIG[pageKey];
  const [documents, setDocuments] = useState([]);

  const refresh = useCallback(async () => {
    const docs = await api.listDocuments();
    setDocuments(docs.filter((d) => d.document_type === cfg.docType));
  }, [cfg.docType]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [refresh]);

  const handleUpload = async (file) => {
    const isExcel = /\.(xlsx|xls)$/i.test(file.name);

    if (pageKey === "gstr2b") {
      await api.uploadGstr2b(file);
    } else if (pageKey === "bank") {
      await api.uploadBankStatement(file);
    } else if (pageKey === "purchase" && isExcel) {
      // Purchase Excel can be either the GSTR-2B B2B bulk export (one
      // government-format file, many invoices, used for the rate-mismatch
      // workflow) or just a general "my purchase bills" list a user kept
      // themselves. Try the B2B importer first since that's the more
      // specific/structured format; if the sheet doesn't actually match
      // it, fall back to the general per-bill parser rather than making
      // the user pick the "right" importer themselves.
      try {
        await api.uploadGstr2bPurchase(file);
      } catch (e) {
        await api.uploadDocument(file, cfg.docType);
      }
    } else if (pageKey === "sales" && isExcel) {
      await api.uploadSalesExcel(file);
    } else {
      // Photos and PDFs (both Sales and Purchase) go through the general
      // document pipeline — PDFs are rasterized page-by-page server-side
      // and read with the exact same AI vision extraction used for
      // photos, so accuracy doesn't drop just because the source was a
      // PDF instead of a phone photo.
      await api.uploadDocument(file, cfg.docType);
    }
    refresh();
  };

  const allBills = documents.flatMap((d) => d.bills);
  const failedDocs = documents.filter((d) => d.status === "FAILED");

  return (
    <div className="p-6 space-y-6">
      <div className="bg-white rounded-xl border border-slate-200 p-5">
        <h2 className="font-semibold text-slate-900 mb-1">{cfg.label}</h2>
        <p className="text-sm text-slate-500 mb-4">{cfg.uploadLabel}</p>
        <UploadBox
          accept={cfg.accept}
          label={cfg.uploadLabel}
          onUpload={handleUpload}
          multiple={pageKey === "sales"}
        />
      </div>

      {pageKey === "purchase" && <PurchaseRegisterCompare onResolved={refresh} />}

      {failedDocs.length > 0 && (
        <div className="bg-rose-50 border border-rose-200 rounded-xl p-4 text-sm text-rose-700">
          <p className="font-medium mb-1">{failedDocs.length} document(s) failed to process:</p>
          <ul className="list-disc list-inside space-y-0.5">
            {failedDocs.map((d) => (
              <li key={d.id}>
                {d.file_name} —{" "}
                <a className="underline" href={`http://localhost:8000/documents/${d.id}/logs`} target="_blank" rel="noreferrer">
                  view logs
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}

      <ReviewTable bills={allBills} onChanged={refresh} />
    </div>
  );
}
