import { useEffect, useState, useCallback } from "react";
import { api } from "./api.js";
import UploadBox from "./UploadBox.jsx";
import ReviewTable from "./ReviewTable.jsx";

const CONFIG = {
  sales: { docType: "SALES", label: "Sales Bills", accept: "image/*,.xlsx,.xls", uploadLabel: "Upload a sales bill photo, or import a sales register Excel file" },
  purchase: { docType: "PURCHASE", label: "Purchase Bills (B2B Excel)", accept: ".xlsx,.xls", uploadLabel: "Import Purchase B2B Excel file" },
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
    if (pageKey === "gstr2b") {
      await api.uploadGstr2b(file);
    } else if (pageKey === "purchase") {
      await api.uploadGstr2bPurchase(file);
    } else if (pageKey === "sales" && /\.(xlsx|xls)$/i.test(file.name)) {
      await api.uploadSalesExcel(file);
    } else {
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
