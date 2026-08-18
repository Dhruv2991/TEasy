import { useState, useEffect } from "react";
import { api, cropImageUrl } from "./api.js";
import { StatusBadge, ConfidenceBadge } from "./ui.jsx";
import { Icon } from "./icons.jsx";

function TransactionRow({ bill, onChanged }) {
  const tx = bill.transaction;
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState(tx || {});
  const [busy, setBusy] = useState(false);

  // Prevent background polling from overwriting the form while actively editing
  useEffect(() => {
    if (!editing) {
      setForm(tx || {});
    }
  }, [tx, editing]);

  if (!tx) return null;

  const startEdit = () => {
    setForm({ ...tx });
    setEditing(true);
  };

  const cancelEdit = () => {
    setForm({ ...tx });
    setEditing(false);
  };

  const save = async () => {
    setBusy(true);
    try {
      await api.updateTransaction(tx.id, form);
      setEditing(false);
      onChanged();
    } catch (e) {
      alert(e.message);
    } finally {
      setBusy(false);
    }
  };

  const act = async (fn) => {
    setBusy(true);
    try {
      await fn(tx.id);
      onChanged();
    } catch (e) {
      alert(e.message);
    } finally {
      setBusy(false);
    }
  };

  const img = cropImageUrl(bill.crop_path);

  return (
    <tr className="border-b border-slate-100 hover:bg-slate-50/60">
      <td className="p-3 align-top">
        {img ? (
          <img src={img} alt="bill crop" className="w-16 h-16 object-cover rounded-lg border border-slate-200" />
        ) : (
          <div className="w-16 h-16 rounded-lg border border-slate-200 flex items-center justify-center text-slate-300">
            <Icon.Documents width={20} height={20} />
          </div>
        )}
      </td>
      {editing ? (
        <>
          <td className="p-3">
            <input
              className="border border-slate-300 rounded px-2 py-1 text-sm w-32"
              value={form.party || ""}
              onChange={(e) => setForm({ ...form, party: e.target.value })}
            />
          </td>
          <td className="p-3">
            <input
              type="text"
              className="border border-slate-300 rounded px-2 py-1 text-sm w-28"
              value={form.date || ""}
              onChange={(e) => setForm({ ...form, date: e.target.value })}
            />
          </td>
          <td className="p-3">
            <input
              type="text"
              className="border border-slate-300 rounded px-2 py-1 text-sm w-24"
              value={form.invoice_number || ""}
              onChange={(e) => setForm({ ...form, invoice_number: e.target.value })}
            />
          </td>
          <td className="p-3">
            <input
              type="number"
              className="border border-slate-300 rounded px-2 py-1 text-sm w-24"
              value={form.total_value ?? 0}
              onChange={(e) => setForm({ ...form, total_value: parseFloat(e.target.value) || 0 })}
            />
          </td>
          <td className="p-3">
            <input
              type="number"
              className="border border-slate-300 rounded px-2 py-1 text-sm w-16"
              value={form.gst_rate ?? 0}
              onChange={(e) => setForm({ ...form, gst_rate: parseFloat(e.target.value) || 0 })}
            />
          </td>
        </>
      ) : (
        <>
          <td className="p-3 text-sm text-slate-800">{tx.party}</td>
          <td className="p-3 text-sm text-slate-600">{tx.date || "—"}</td>
          <td className="p-3 text-sm text-slate-600">
            {tx.invoice_number || "—"}
            {tx.possible_duplicate && (
              <span className="ml-1.5 text-[10px] font-semibold bg-rose-100 text-rose-700 px-1.5 py-0.5 rounded">
                DUPLICATE?
              </span>
            )}
          </td>
          <td className="p-3 text-sm font-medium text-slate-900">
            ₹{Number(tx.total_value).toLocaleString("en-IN")}
          </td>
          <td className="p-3 text-sm text-slate-600">{tx.gst_rate}%</td>
        </>
      )}
      <td className="p-3">
        <ConfidenceBadge value={tx.confidence} />
        {tx.type === "SALES" && Number(tx.confidence || 0) < 0.80 && (
          <div className="mt-1 text-[10px] font-medium text-amber-700">Manual check required</div>
        )}
      </td>
      <td className="p-3">
        <StatusBadge status={tx.status} />
      </td>
      <td className="p-3">
        {tx.tally_status && tx.tally_status !== "NOT_SENT" ? (
          <StatusBadge status={tx.tally_status} />
        ) : (
          <span className="text-xs text-slate-400">—</span>
        )}
      </td>
      <td className="p-3 whitespace-nowrap">
        {editing ? (
          <div className="flex gap-1.5">
            <button
              disabled={busy}
              onClick={save}
              className="text-xs bg-slate-900 text-white px-2.5 py-1 rounded-md"
            >
              Save
            </button>
            <button
              disabled={busy}
              onClick={cancelEdit}
              className="text-xs border border-slate-300 px-2.5 py-1 rounded-md"
            >
              Cancel
            </button>
          </div>
        ) : (
          <div className="flex gap-1.5">
            <button
              disabled={busy}
              onClick={startEdit}
              className="text-xs border border-slate-300 px-2.5 py-1 rounded-md hover:bg-slate-100"
            >
              Edit
            </button>
            <button
              disabled={busy || tx.status === "APPROVED"}
              onClick={() => act(api.approveTransaction)}
              className="text-xs bg-emerald-600 text-white px-2.5 py-1 rounded-md disabled:opacity-40"
            >
              Approve
            </button>
            <button
              disabled={busy || tx.status === "REJECTED"}
              onClick={() => act(api.rejectTransaction)}
              className="text-xs bg-rose-600 text-white px-2.5 py-1 rounded-md disabled:opacity-40"
            >
              Reject
            </button>
          </div>
        )}
      </td>
    </tr>
  );
}

export default function ReviewTable({ bills, onChanged }) {
  if (!bills.length) {
    return <div className="text-sm text-slate-400 py-8 text-center">No transactions here.</div>;
  }
  return (
    <div className="overflow-x-auto bg-white rounded-xl border border-slate-200">
      <table className="w-full">
        <thead>
          <tr className="text-left text-xs font-semibold text-slate-500 uppercase border-b border-slate-200 bg-slate-50">
            <th className="p-3">Bill</th>
            <th className="p-3">Party</th>
            <th className="p-3">Date</th>
            <th className="p-3">Invoice #</th>
            <th className="p-3">Total</th>
            <th className="p-3">GST %</th>
            <th className="p-3">Confidence</th>
            <th className="p-3">Status</th>
            <th className="p-3">Tally</th>
            <th className="p-3">Actions</th>
          </tr>
        </thead>
        <tbody>
          {bills.map((b) => (
            <TransactionRow key={b.id} bill={b} onChanged={onChanged} />
          ))}
        </tbody>
      </table>
    </div>
  );
}