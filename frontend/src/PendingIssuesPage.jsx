import { useEffect, useState, useCallback } from "react";
import { api } from "./api.js";
import { StatusBadge } from "./ui.jsx";

function FailedDocCard({ doc }) {
  const [logs, setLogs] = useState(null);
  const [open, setOpen] = useState(false);

  const toggle = async () => {
    if (!open && !logs) {
      setLogs(await api.getDocumentLogs(doc.id));
    }
    setOpen(!open);
  };

  return (
    <div className="bg-white rounded-xl border border-slate-200 p-4">
      <div className="flex items-center justify-between">
        <div>
          <p className="font-medium text-slate-900">{doc.file_name}</p>
          <p className="text-xs text-slate-500">{doc.document_type} · {new Date(doc.uploaded_at).toLocaleString()}</p>
        </div>
        <div className="flex items-center gap-3">
          <StatusBadge status={doc.status} />
          <button onClick={toggle} className="text-sm text-indigo-600 hover:underline">
            {open ? "Hide logs" : "View logs"}
          </button>
        </div>
      </div>
      {open && logs && (
        <div className="mt-3 border-t border-slate-100 pt-3 space-y-1.5">
          {logs.map((l, i) => (
            <div key={i} className="text-xs">
              <span className="text-slate-400">{new Date(l.time).toLocaleTimeString()}</span>{" "}
              <span className="text-slate-700">{l.message}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function PendingIssuesPage() {
  const [documents, setDocuments] = useState([]);

  const refresh = useCallback(async () => {
    setDocuments(await api.listDocuments());
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 6000);
    return () => clearInterval(t);
  }, [refresh]);

  const failed = documents.filter((d) => d.status === "FAILED");

  return (
    <div className="p-6 space-y-4">
      <div>
        <h2 className="font-semibold text-slate-900 text-lg">Pending Issues</h2>
        <p className="text-sm text-slate-500">Documents that failed processing — check the logs to see exactly why.</p>
      </div>
      {failed.length === 0 && (
        <div className="text-sm text-slate-400 bg-white border border-slate-200 rounded-xl p-8 text-center">
          Nothing failed. 🎉
        </div>
      )}
      <div className="space-y-3">
        {failed.map((d) => (
          <FailedDocCard key={d.id} doc={d} />
        ))}
      </div>
    </div>
  );
}
