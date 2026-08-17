import { useEffect, useState, useCallback } from "react";
import { api } from "./api.js";
import ReviewTable from "./ReviewTable.jsx";

export default function ReviewApprovePage() {
  const [documents, setDocuments] = useState([]);

  const refresh = useCallback(async () => {
    setDocuments(await api.listDocuments());
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [refresh]);

  const bills = documents
    .flatMap((d) => d.bills)
    .filter((b) => b.transaction && b.transaction.status === "NEEDS_REVIEW");

  return (
    <div className="p-6 space-y-4">
      <div>
        <h2 className="font-semibold text-slate-900 text-lg">Review & Approve</h2>
        <p className="text-sm text-slate-500">
          {bills.length} transaction(s) need your attention before they can go to Tally.
        </p>
      </div>
      <ReviewTable bills={bills} onChanged={refresh} />
    </div>
  );
}
