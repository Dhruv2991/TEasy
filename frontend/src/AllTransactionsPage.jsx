import { useEffect, useState, useCallback } from "react";
import { api } from "./api.js";
import ReviewTable from "./ReviewTable.jsx";

export default function AllTransactionsPage() {
  const [documents, setDocuments] = useState([]);

  const refresh = useCallback(async () => {
    setDocuments(await api.listDocuments());
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [refresh]);

  const bills = documents.flatMap((d) => d.bills).filter((b) => b.transaction);

  return (
    <div className="p-6 space-y-4">
      <h2 className="font-semibold text-slate-900 text-lg">All Transactions</h2>
      <ReviewTable bills={bills} onChanged={refresh} />
    </div>
  );
}
