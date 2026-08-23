import { useEffect, useState, useCallback } from "react";
import { api } from "./api.js";
import UploadBox from "./UploadBox.jsx";
import ReviewTable from "./ReviewTable.jsx";

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
