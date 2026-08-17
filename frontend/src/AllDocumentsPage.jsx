import { useEffect, useState, useCallback } from "react";
import { api } from "./api.js";
import { StatusBadge } from "./ui.jsx";

export default function AllDocumentsPage() {
  const [documents, setDocuments] = useState([]);

  const refresh = useCallback(async () => {
    setDocuments(await api.listDocuments());
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [refresh]);

  return (
    <div className="p-6 space-y-4">
      <h2 className="font-semibold text-slate-900 text-lg">All Documents</h2>
      <div className="bg-white rounded-xl border border-slate-200 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-slate-400 uppercase border-b border-slate-100 bg-slate-50">
              <th className="p-3">Document</th>
              <th className="p-3">Type</th>
              <th className="p-3">Uploaded</th>
              <th className="p-3">Status</th>
              <th className="p-3">Bills</th>
              <th className="p-3">Extracted</th>
            </tr>
          </thead>
          <tbody>
            {documents.map((d) => (
              <tr key={d.id} className="border-b border-slate-50">
                <td className="p-3 font-medium text-slate-800">{d.file_name}</td>
                <td className="p-3 text-slate-500">{d.document_type}</td>
                <td className="p-3 text-slate-500">{new Date(d.uploaded_at).toLocaleString()}</td>
                <td className="p-3"><StatusBadge status={d.status} /></td>
                <td className="p-3 text-slate-500">{d.bills.length}</td>
                <td className="p-3 text-slate-500">{d.bills.filter((b) => b.transaction).length} / {d.bills.length}</td>
              </tr>
            ))}
            {!documents.length && (
              <tr><td colSpan={6} className="p-6 text-center text-slate-400">No documents uploaded yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
