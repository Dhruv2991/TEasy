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

// Lets the user attach the supplier's own invoice Excel for a purchase
// transaction so the real per-rate GST split can be recovered — GSTR-2B's
// B2B sheet only gives an invoice-level aggregate, never a line-item rate
// breakdown. The upload is only accepted server-side if its totals
// reconcile with what's already on file for this transaction.
function SupplierInvoiceUpload({ transactionId, onChanged }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const inputId = `supplier-invoice-${transactionId}`;

  const handleFile = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      const result = await api.matchSupplierInvoice(transactionId, file);
      if (!result.matched) {
        setError(result.reason);
      } else {
        await onChanged();
      }
    } catch (err) {
      setError(err.message || "Upload failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <span className="ml-1.5 inline-flex items-center align-middle">
      <input
        id={inputId}
        type="file"
        accept=".xlsx,.xls"
        className="hidden"
        onChange={handleFile}
        disabled={busy}
      />
      <label
        htmlFor={inputId}
        title="Upload the supplier's own invoice Excel to resolve the real per-rate GST split"
        className={`text-[10px] font-medium border border-slate-300 px-1.5 py-0.5 rounded cursor-pointer hover:bg-slate-100 ${
          busy ? "opacity-40 cursor-not-allowed" : ""
        }`}
      >
        {busy ? "Matching…" : "Match supplier invoice"}
      </label>
      {error && (
        <span className="ml-1 text-[10px] text-rose-600 max-w-[220px]" title={error}>
          ⚠ {error.length > 40 ? error.slice(0, 40) + "…" : error}
        </span>
      )}
    </span>
  );
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
          <td className="p-3 text-sm text-slate-600">
            {tx.gst_rate}%
            {tx.gst_rate_uncertain && !tx.rate_breakdown && (
              <span
                className="ml-1.5 text-[10px] font-semibold bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded"
                title="This Excel doesn't break the invoice down by rate — likely a mixed-rate invoice (multiple GST slabs on one bill). The taxable/tax amounts are exact; verify the correct rate split before pushing to Tally."
              >
                RATE UNCERTAIN
              </span>
            )}
            {tx.rate_breakdown && (
              <span
                className="ml-1.5 text-[10px] font-semibold bg-emerald-100 text-emerald-700 px-1.5 py-0.5 rounded"
                title={`Resolved from supplier invoice '${tx.rate_breakdown_source || ""}': ${
                  (() => {
                    try {
                      return JSON.parse(tx.rate_breakdown).map((b) => `${b.rate}% on ₹${b.taxable_value}`).join(", ");
                    } catch {
                      return "";
                    }
                  })()
                }`}
              >
                RATE SPLIT RESOLVED
              </span>
            )}
            {tx.type === "PURCHASE" && !tx.rate_breakdown && (
              <SupplierInvoiceUpload transactionId={tx.id} onChanged={onChanged} />
            )}
          </td>
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

// Builds a CSV file client-side from whatever rows are currently visible
// (i.e. after search + status filter are applied) and triggers a download.
// Kept dependency-free — just quote/escape per RFC 4180 and blob-download.
function downloadCsv(rows, filename) {
  const headers = [
    "Party",
    "Date",
    "Invoice #",
    "Taxable Value",
    "CGST",
    "SGST",
    "IGST",
    "Total",
    "GST %",
    "Confidence",
    "Status",
    "Tally Status",
  ];
  const escape = (v) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [headers.map(escape).join(",")];
  for (const b of rows) {
    const tx = b.transaction || {};
    lines.push(
      [
        tx.party,
        tx.date,
        tx.invoice_number,
        tx.taxable_value,
        tx.cgst,
        tx.sgst,
        tx.igst,
        tx.total_value,
        tx.gst_rate,
        tx.confidence != null ? Math.round(tx.confidence * 100) + "%" : "",
        tx.status,
        tx.tally_status,
      ]
        .map(escape)
        .join(",")
    );
  }
  const csv = "\uFEFF" + lines.join("\r\n"); // BOM so Excel opens ₹/UTF-8 correctly
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export default function ReviewTable({ bills, onChanged }) {
  const [selectedIds, setSelectedIds] = useState([]);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [sortKey, setSortKey] = useState(null);   // "party" | "date" | "invoice_number" | "total_value" | "gst_rate" | "confidence" | "status"
  const [sortDir, setSortDir] = useState("asc");  // "asc" | "desc"
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("ALL");

  if (!bills.length) {
    return (
      <div className="text-sm text-slate-400 py-8 text-center">
        No transactions here.
      </div>
    );
  }

  // Distinct statuses actually present, so the filter dropdown never shows
  // an option that would just return an empty table.
  const statusesPresent = Array.from(
    new Set(bills.map((b) => b.transaction?.status).filter(Boolean))
  );

  // Search matches party name, invoice number, or date (all substring,
  // case-insensitive) — the three things someone scanning a big batch is
  // actually looking for. Status filter narrows independently.
  const q = searchQuery.trim().toLowerCase();
  const filteredBills = bills.filter((b) => {
    const tx = b.transaction;
    if (!tx) return false;
    if (statusFilter !== "ALL" && tx.status !== statusFilter) return false;
    if (!q) return true;
    const haystack = `${tx.party || ""} ${tx.invoice_number || ""} ${tx.date || ""}`.toLowerCase();
    return haystack.includes(q);
  });

  // Sorting a freshly-scanned Excel/bill batch by column (party, date,
  // amount, etc.) makes a messy dump of rows actually scannable — this is
  // purely a display-order change; it doesn't touch the underlying data or
  // which transactions get approved/pushed.
  const sortableColumns = {
    party: (b) => (b.transaction?.party || "").toLowerCase(),
    date: (b) => b.transaction?.date || "",
    invoice_number: (b) => (b.transaction?.invoice_number || "").toLowerCase(),
    total_value: (b) => Number(b.transaction?.total_value) || 0,
    gst_rate: (b) => Number(b.transaction?.gst_rate) || 0,
    confidence: (b) => Number(b.transaction?.confidence) || 0,
    status: (b) => b.transaction?.status || "",
  };

  const sortedBills = sortKey
    ? [...filteredBills].sort((a, b) => {
        const getter = sortableColumns[sortKey];
        const av = getter(a);
        const bv = getter(b);
        let cmp;
        if (typeof av === "number" && typeof bv === "number") {
          cmp = av - bv;
        } else {
          cmp = String(av).localeCompare(String(bv));
        }
        return sortDir === "asc" ? cmp : -cmp;
      })
    : filteredBills;

  // Column totals footer reflects whatever's currently visible (search +
  // status filter applied), not the whole unfiltered batch — matches what
  // the person is actually looking at.
  const totals = sortedBills.reduce(
    (acc, b) => {
      const tx = b.transaction || {};
      acc.taxable += Number(tx.taxable_value) || 0;
      acc.cgst += Number(tx.cgst) || 0;
      acc.sgst += Number(tx.sgst) || 0;
      acc.igst += Number(tx.igst) || 0;
      acc.total += Number(tx.total_value) || 0;
      return acc;
    },
    { taxable: 0, cgst: 0, sgst: 0, igst: 0, total: 0 }
  );

  const toggleSort = (key) => {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  };

  const SortHeader = ({ colKey, children }) => (
    <th
      className="p-3 cursor-pointer select-none hover:text-slate-700"
      onClick={() => toggleSort(colKey)}
      title="Click to sort"
    >
      <span className="inline-flex items-center gap-1">
        {children}
        {sortKey === colKey && (
          <span className="text-slate-400">{sortDir === "asc" ? "▲" : "▼"}</span>
        )}
      </span>
    </th>
  );

  // "Select all" / bulk actions operate on what's currently visible
  // (post search+filter), not the whole batch — selecting a filtered-down
  // view and hitting "Approve Selected" shouldn't silently touch hidden rows.
  const allTxIds = sortedBills
    .map((b) => b.transaction?.id)
    .filter(Boolean);

  const isAllSelected =
    allTxIds.length > 0 && allTxIds.every((id) => selectedIds.includes(id));

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
      {/* Search / Filter / Export Toolbar */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2 flex-wrap">
          <div className="relative">
            <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400">
              <Icon.Search width={14} height={14} />
            </span>
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search party, invoice #, date…"
              className="text-sm border border-slate-300 rounded-lg pl-8 pr-3 py-1.5 w-64 focus:outline-none focus:ring-2 focus:ring-slate-900/10"
            />
          </div>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="text-sm border border-slate-300 rounded-lg px-2.5 py-1.5 bg-white"
          >
            <option value="ALL">All statuses</option>
            {statusesPresent.map((s) => (
              <option key={s} value={s}>
                {s.replace("_", " ")}
              </option>
            ))}
          </select>
          {(searchQuery || statusFilter !== "ALL") && (
            <span className="text-xs text-slate-400">
              {sortedBills.length} of {bills.length} shown
            </span>
          )}
        </div>
        <button
          onClick={() =>
            downloadCsv(
              sortedBills,
              `transactions-${new Date().toISOString().slice(0, 10)}.csv`
            )
          }
          disabled={!sortedBills.length}
          className="text-xs font-medium border border-slate-300 px-3 py-1.5 rounded-lg hover:bg-slate-100 disabled:opacity-40 flex items-center gap-1.5"
          title="Export currently visible rows to CSV"
        >
          <Icon.Download width={14} height={14} />
          Export CSV
        </button>
      </div>

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
              <SortHeader colKey="party">Party</SortHeader>
              <SortHeader colKey="date">Date</SortHeader>
              <SortHeader colKey="invoice_number">Invoice #</SortHeader>
              <SortHeader colKey="total_value">Total</SortHeader>
              <SortHeader colKey="gst_rate">GST %</SortHeader>
              <SortHeader colKey="confidence">Confidence</SortHeader>
              <SortHeader colKey="status">Status</SortHeader>
              <th className="p-3">Tally</th>
              <th className="p-3">Actions</th>
            </tr>
          </thead>
          <tbody>
            {sortedBills.map((b) => (
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
          <tfoot>
            <tr className="border-t-2 border-slate-200 bg-slate-50 text-sm font-semibold text-slate-700">
              <td className="p-3" colSpan={5}>
                Totals ({sortedBills.length} row{sortedBills.length === 1 ? "" : "s"})
                <span className="ml-2 text-xs text-slate-500 font-normal">
                  Taxable ₹{formatMoney(totals.taxable)}
                </span>
              </td>
              <td className="p-3">₹{formatMoney(totals.total)}</td>
              <td className="p-3 text-xs text-slate-500 font-normal" colSpan={4}>
                CGST ₹{formatMoney(totals.cgst)} · SGST ₹{formatMoney(totals.sgst)} · IGST ₹{formatMoney(totals.igst)}
              </td>
              <td className="p-3"></td>
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  );
}