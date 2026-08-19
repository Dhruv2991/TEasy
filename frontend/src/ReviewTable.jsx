import { useState, useEffect } from "react";
import { api, cropImageUrl } from "./api.js";
import { StatusBadge, ConfidenceBadge, formatMoney } from "./ui.jsx";
import { Icon } from "./icons.jsx";

function round2(n) {
  return Math.round((Number(n) || 0) * 100) / 100;
}

// The Total is what actually gets saved/pushed to Tally — GST rules expect
// it rounded off to the nearest whole rupee, so we force it to an integer
// here (not just at display time via formatMoney).
function roundRupee(n) {
  return Math.round(Number(n) || 0);
}

function TransactionRow({ bill, onChanged, isSelected, onSelect }) {
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

  // Recompute the tax split whenever taxable value or GST rate change,
  // keeping the existing intra-state (CGST+SGST) vs inter-state (IGST)
  // shape of whatever this transaction already had — we don't decide that
  // here, we just re-split the same way it was split before.
  const recalcFromTaxableAndRate = (taxable, rate) => {
    const taxAmount = (Number(taxable) || 0) * (Number(rate) || 0) / 100;
    const wasInterState = (Number(form.igst) || 0) > 0;
    const next = wasInterState
      ? { igst: round2(taxAmount), cgst: 0, sgst: 0 }
      : { cgst: round2(taxAmount / 2), sgst: round2(taxAmount / 2), igst: 0 };
    const total = roundRupee((Number(taxable) || 0) + taxAmount);
    return { ...next, total_value: total };
  };

  const handleTaxableChange = (value) => {
    const taxable = parseFloat(value) || 0;
    setForm((f) => ({ ...f, taxable_value: taxable, ...recalcFromTaxableAndRate(taxable, f.gst_rate) }));
  };

  const handleRateChange = (value) => {
    const rate = parseFloat(value) || 0;
    setForm((f) => ({ ...f, gst_rate: rate, ...recalcFromTaxableAndRate(f.taxable_value, rate) }));
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

  const taxSum = (Number(form.cgst) || 0) + (Number(form.sgst) || 0) + (Number(form.igst) || 0);
  const computedTotal = roundRupee((Number(form.taxable_value) || 0) + taxSum);
  const reconciled = Math.abs(computedTotal - (Number(form.total_value) || 0)) <= 1.5;

  return (
    <tr className="border-b border-slate-100 hover:bg-slate-50/60">
      {/* Selection Checkbox */}
      <td className="p-3 align-middle w-10">
        <input
          type="checkbox"
          checked={isSelected}
          onChange={onSelect}
          className="rounded border-slate-300 text-slate-900 focus:ring-slate-900"
        />
      </td>

      {/* Bill Crop */}
      <td className="p-3 align-top">
        {img ? (
          <img
            src={img}
            alt="bill crop"
            className="w-16 h-16 object-cover rounded-lg border border-slate-200"
          />
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
              onChange={(e) =>
                setForm({ ...form, invoice_number: e.target.value })
              }
            />
          </td>
          <td className="p-3">
            <div className="text-[10px] text-slate-400 mb-0.5">Taxable value</div>
            <input
              type="number"
              className="border border-slate-300 rounded px-2 py-1 text-sm w-24"
              value={form.taxable_value ?? 0}
              onChange={(e) => handleTaxableChange(e.target.value)}
            />
          </td>
          <td className="p-3">
            <div className="text-[10px] text-slate-400 mb-0.5">GST % → Total</div>
            <input
              type="number"
              className="border border-slate-300 rounded px-2 py-1 text-sm w-16"
              value={form.gst_rate ?? 0}
              onChange={(e) => handleRateChange(e.target.value)}
            />
            <div className="text-sm font-medium text-slate-900 mt-1">
              ₹{formatMoney(computedTotal)}
            </div>
            <div className={`text-[10px] ${reconciled ? "text-emerald-600" : "text-rose-600"}`}>
              {reconciled ? "Balances ✓" : `≠ saved total (₹${formatMoney(form.total_value)})`}
            </div>
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
            ₹{formatMoney(tx.total_value)}
          </td>
          <td className="p-3 text-sm text-slate-600">{tx.gst_rate}%</td>
        </>
      )}

      <td className="p-3">
        <ConfidenceBadge value={tx.confidence} />
        {tx.type === "SALES" && Number(tx.confidence || 0) < 0.80 && (
          <div className="mt-1 text-[10px] font-medium text-amber-700">
            Manual check required
          </div>
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
              disabled={busy || !reconciled}
              onClick={save}
              title={!reconciled ? "Taxable + GST must equal Total before saving" : ""}
              className="text-xs bg-slate-900 text-white px-2.5 py-1 rounded-md disabled:opacity-40 disabled:cursor-not-allowed"
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
  const [selectedIds, setSelectedIds] = useState([]);
  const [bulkBusy, setBulkBusy] = useState(false);

  if (!bills.length) {
    return (
      <div className="text-sm text-slate-400 py-8 text-center">
        No transactions here.
      </div>
    );
  }

  // Collect all transaction IDs currently in the table
  const allTxIds = bills
    .map((b) => b.transaction?.id)
    .filter(Boolean);

  const isAllSelected =
    allTxIds.length > 0 && selectedIds.length === allTxIds.length;

  const handleSelectAll = (e) => {
    if (e.target.checked) {
      setSelectedIds(allTxIds);
    } else {
      setSelectedIds([]);
    }
  };

  const handleSelectRow = (txId) => {
    setSelectedIds((prev) =>
      prev.includes(txId) ? prev.filter((id) => id !== txId) : [...prev, txId]
    );
  };

  // --- Bulk Action Handlers ---
  const handleBulkApprove = async () => {
    if (!selectedIds.length) return;
    setBulkBusy(true);
    try {
      const res = await api.bulkApproveTransactions(selectedIds);
      if (res.errors && res.errors.length > 0) {
        alert(
          `Approved ${res.approved_count} item(s).\n\nWarnings/Errors:\n` +
            res.errors.join("\n")
        );
      }
      setSelectedIds([]);
      onChanged();
    } catch (e) {
      alert(`Bulk approve failed: ${e.message}`);
    } finally {
      setBulkBusy(false);
    }
  };

  const handleBulkReject = async () => {
    if (!selectedIds.length) return;
    setBulkBusy(true);
    try {
      await api.bulkRejectTransactions(selectedIds);
      setSelectedIds([]);
      onChanged();
    } catch (e) {
      alert(`Bulk reject failed: ${e.message}`);
    } finally {
      setBulkBusy(false);
    }
  };

  const handleBulkDelete = async () => {
    if (!selectedIds.length) return;
    if (
      !window.confirm(
        `Are you sure you want to delete ${selectedIds.length} selected transaction(s)?`
      )
    ) {
      return;
    }
    setBulkBusy(true);
    try {
      await api.bulkDeleteTransactions(selectedIds);
      setSelectedIds([]);
      onChanged();
    } catch (e) {
      alert(`Bulk delete failed: ${e.message}`);
    } finally {
      setBulkBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      {/* Bulk Action Toolbar */}
      <div className="flex items-center justify-between bg-slate-50 border border-slate-200 px-4 py-2.5 rounded-xl text-sm">
        <span className="text-slate-600 font-medium">
          {selectedIds.length} of {allTxIds.length} selected
        </span>

        <div className="flex gap-2">
          <button
            disabled={!selectedIds.length || bulkBusy}
            onClick={handleBulkApprove}
            className="bg-emerald-600 text-white text-xs font-medium px-3 py-1.5 rounded-lg hover:bg-emerald-700 disabled:opacity-40 transition-colors"
          >
            Approve Selected
          </button>
          <button
            disabled={!selectedIds.length || bulkBusy}
            onClick={handleBulkReject}
            className="bg-amber-600 text-white text-xs font-medium px-3 py-1.5 rounded-lg hover:bg-amber-700 disabled:opacity-40 transition-colors"
          >
            Reject Selected
          </button>
          <button
            disabled={!selectedIds.length || bulkBusy}
            onClick={handleBulkDelete}
            className="bg-rose-600 text-white text-xs font-medium px-3 py-1.5 rounded-lg hover:bg-rose-700 disabled:opacity-40 transition-colors"
          >
            Delete Selected
          </button>
        </div>
      </div>

      {/* Table */}
      <div className="overflow-x-auto bg-white rounded-xl border border-slate-200">
        <table className="w-full">
          <thead>
            <tr className="text-left text-xs font-semibold text-slate-500 uppercase border-b border-slate-200 bg-slate-50">
              <th className="p-3 w-10">
                <input
                  type="checkbox"
                  checked={isAllSelected}
                  onChange={handleSelectAll}
                  className="rounded border-slate-300 text-slate-900 focus:ring-slate-900"
                />
              </th>
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
              <TransactionRow
                key={b.id}
                bill={b}
                onChanged={onChanged}
                isSelected={
                  b.transaction ? selectedIds.includes(b.transaction.id) : false
                }
                onSelect={() =>
                  b.transaction && handleSelectRow(b.transaction.id)
                }
              />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}