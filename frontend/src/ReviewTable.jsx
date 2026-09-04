import { useState, useEffect } from "react";
import { api, cropImageUrl } from "./api.js";
import { StatusBadge, ConfidenceBadge, ReconciliationBadge, formatMoney } from "./ui.jsx";
import { Icon } from "./icons.jsx";

// Converts various incoming date formats (DD/MM/YYYY, DD-MM-YYYY, YYYYMMDD) 
// into YYYY-MM-DD required by HTML5 <input type="date">
function toIsoDate(dateStr) {
  if (!dateStr) return "";
  if (/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) return dateStr;

  // Handle YYYYMMDD
  if (/^\d{8}$/.test(dateStr)) {
    return `${dateStr.slice(0, 4)}-${dateStr.slice(4, 6)}-${dateStr.slice(6, 8)}`;
  }

  // Handle DD/MM/YYYY or DD-MM-YYYY
  const parts = dateStr.split(/[/-]/);
  if (parts.length === 3) {
    if (parts[2].length === 4) {
      const day = parts[0].padStart(2, "0");
      const month = parts[1].padStart(2, "0");
      const year = parts[2];
      return `${year}-${month}-${day}`;
    }
    if (parts[0].length === 4) {
      const year = parts[0];
      const month = parts[1].padStart(2, "0");
      const day = parts[2].padStart(2, "0");
      return `${year}-${month}-${day}`;
    }
  }
  return dateStr;
}

function round2(n) {
  return Math.round((Number(n) || 0) * 100) / 100;
}

function roundRupee(n) {
  return Math.round(Number(n) || 0);
}

// Lets a human resolve an AMBIGUOUS bank row (or pick a match for an
// UNMATCHED one) instead of the reconciler guessing between same-amount
// invoices — see match_candidates() in backend/app/reconciliation.py.
function MatchPickerModal({ tx, onClose, onMatched }) {
  const [loading, setLoading] = useState(true);
  const [candidates, setCandidates] = useState([]);
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getMatchCandidates(tx.id)
      .then((res) => {
        if (!cancelled) setCandidates(res.candidates || []);
      })
      .catch((e) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [tx.id]);

  const pick = async (candidateId) => {
    setBusyId(candidateId);
    try {
      await api.reconcileManual(tx.id, candidateId);
      onMatched();
    } catch (e) {
      setError(e.message);
      setBusyId(null);
    }
  };

  const amount = tx.credit > 0 ? tx.credit : tx.debit;
  const lookingFor = tx.credit > 0 ? "sales invoice" : "purchase bill";

  return (
    <div className="fixed inset-0 bg-slate-900/40 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div
        className="bg-white rounded-xl border border-slate-200 shadow-xl w-full max-w-lg max-h-[80vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="p-4 border-b border-slate-100">
          <h3 className="text-[15.5px] font-semibold text-slate-900">Match this bank entry</h3>
          <p className="text-[13px] text-slate-500 mt-1">
            Looking for a {lookingFor} worth ₹{formatMoney(amount)}
            {tx.date ? ` around ${tx.date}` : ""}. {tx.narration && (
              <span className="italic">"{tx.narration}"</span>
            )}
          </p>
        </div>

        <div className="p-4 space-y-2">
          {loading && <div className="text-[15.5px] text-slate-400 py-4 text-center">Loading candidates…</div>}
          {error && <div className="text-[15.5px] text-rose-600">{error}</div>}
          {!loading && !error && candidates.length === 0 && (
            <div className="text-[15.5px] text-slate-400 py-4 text-center">
              No same-amount invoice found within the usual payment window. This bank entry may not
              correspond to a recorded sale/purchase at all (e.g. a bank charge, salary, GST payment,
              or owner's drawing) — that's fine, it can stay Unmatched.
            </div>
          )}
          {candidates.map((c) => (
            <button
              key={c.id}
              disabled={busyId !== null}
              onClick={() => pick(c.id)}
              className="w-full text-left border border-slate-200 rounded-lg p-3 hover:bg-slate-50 disabled:opacity-50 transition-colors flex items-center justify-between gap-3"
            >
              <div>
                <div className="text-[15.5px] font-medium text-slate-900">{c.party}</div>
                <div className="text-[13px] text-slate-500">
                  {c.invoice_number || "No invoice #"} · {c.date || "No date"}
                </div>
              </div>
              <div className="text-right">
                <div className="text-[15.5px] font-semibold text-slate-900">₹{formatMoney(c.total_value)}</div>
                <div className="text-[11px] text-slate-400">
                  {busyId === c.id ? "Matching…" : "Select"}
                </div>
              </div>
            </button>
          ))}
        </div>

        <div className="p-4 border-t border-slate-100 flex justify-end">
          <button
            onClick={onClose}
            disabled={busyId !== null}
            className="text-[13px] font-medium border border-slate-300 px-3 py-1.5 rounded-lg hover:bg-slate-100"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

// Full-size view of a bill's source photo/crop, opened by clicking its
// thumbnail in the table. Only ever opened for rows that actually have an
// image (bill.crop_path) — Excel/GSTR-2B-sourced rows have no photo, so
// their thumbnail slot isn't clickable at all (see TransactionRow below).
function BillImageModal({ imageUrl, onClose }) {
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 bg-slate-900/70 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-xl shadow-xl max-w-3xl max-h-[90vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between p-3 border-b border-slate-100">
          <h3 className="text-[15.5px] font-semibold text-slate-900">Bill image</h3>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-700 text-xl leading-none px-1"
            aria-label="Close"
          >
            ×
          </button>
        </div>
        <div className="overflow-auto p-3 bg-slate-50">
          <img
            src={imageUrl}
            alt="Full bill"
            className="max-w-full h-auto rounded-lg border border-slate-200 mx-auto"
          />
        </div>
      </div>
    </div>
  );
}

function ReconciliationCell({ tx, onChanged }) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const status = tx.reconciliation_status || "UNMATCHED";
  const needsAttention = status === "AMBIGUOUS" || status === "UNMATCHED";

  return (
    <div className="flex items-center gap-1.5">
      <ReconciliationBadge status={status} />
      {needsAttention && (
        <button
          onClick={() => setPickerOpen(true)}
          className="text-[11px] font-medium text-indigo-600 hover:text-indigo-800 underline underline-offset-2"
        >
          {status === "AMBIGUOUS" ? "Resolve" : "Match"}
        </button>
      )}
      {pickerOpen && (
        <MatchPickerModal
          tx={tx}
          onClose={() => setPickerOpen(false)}
          onMatched={() => {
            setPickerOpen(false);
            onChanged();
          }}
        />
      )}
    </div>
  );
}

function TransactionRow({ bill, onChanged, isSelected, onSelect }) {
  const tx = bill.transaction;
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState(tx || {});
  const [busy, setBusy] = useState(false);
  const [imageOpen, setImageOpen] = useState(false);

  useEffect(() => {
    if (!editing) {
      setForm(tx || {});
    }
  }, [tx, editing]);

  if (!tx) return null;

  const isBank = tx.type === "BANK";

  const startEdit = () => {
    setForm({ ...tx, date: toIsoDate(tx.date) });
    setEditing(true);
  };

  const cancelEdit = () => {
    setForm({ ...tx });
    setEditing(false);
  };

  const recalcFromTaxableAndRate = (taxable, rate) => {
    const taxAmount = ((Number(taxable) || 0) * (Number(rate) || 0)) / 100;
    const wasInterState = (Number(form.igst) || 0) > 0;
    const next = wasInterState
      ? { igst: round2(taxAmount), cgst: 0, sgst: 0 }
      : { cgst: round2(taxAmount / 2), sgst: round2(taxAmount / 2), igst: 0 };
    const total = roundRupee((Number(taxable) || 0) + taxAmount);
    return { ...next, total_value: total };
  };

  const handleTaxableChange = (value) => {
    const taxable = parseFloat(value) || 0;
    setForm((f) => ({
      ...f,
      taxable_value: taxable,
      ...recalcFromTaxableAndRate(taxable, f.gst_rate),
    }));
  };

  const handleRateChange = (value) => {
    const rate = parseFloat(value) || 0;
    setForm((f) => ({
      ...f,
      gst_rate: rate,
      ...recalcFromTaxableAndRate(f.taxable_value, rate),
    }));
  };

  // Bank rows have no GST/rate math to keep in sync — debit and credit are
  // mutually exclusive sides of the same journal entry, and total_value is
  // just whichever one is non-zero (used for CSV export / totals display).
  const handleDebitChange = (value) => {
    const debit = parseFloat(value) || 0;
    setForm((f) => ({ ...f, debit, credit: 0, total_value: debit }));
  };

  const handleCreditChange = (value) => {
    const credit = parseFloat(value) || 0;
    setForm((f) => ({ ...f, credit, debit: 0, total_value: credit }));
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

  const taxSum =
    (Number(form.cgst) || 0) +
    (Number(form.sgst) || 0) +
    (Number(form.igst) || 0);
  const computedTotal = roundRupee((Number(form.taxable_value) || 0) + taxSum);
  const reconciled = isBank
    ? true // a bank row's "total" is just debit or credit itself — nothing to reconcile against
    : Math.abs(computedTotal - (Number(form.total_value) || 0)) <= 1.5;

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
          <button
            type="button"
            onClick={() => setImageOpen(true)}
            className="block rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500"
            title="Click to view the full bill"
          >
            <img
              src={img}
              alt="bill crop"
              className="w-24 h-16 object-cover rounded-lg border border-slate-200 hover:opacity-80 transition-opacity cursor-pointer"
            />
          </button>
        ) : (
          <div
            className="w-24 h-16 rounded-lg border border-slate-200 flex items-center justify-center text-slate-300"
            title="No source image for this row (e.g. imported from Excel/GSTR-2B)"
          >
            <Icon.Documents width={20} height={20} />
          </div>
        )}
        {imageOpen && (
          <BillImageModal imageUrl={img} onClose={() => setImageOpen(false)} />
        )}
      </td>

      {editing ? (
        <>
          <td className="p-3">
            <input
              className="border border-slate-300 rounded px-2 py-1 text-[15.5px] w-36"
              value={form.party || ""}
              onChange={(e) => setForm({ ...form, party: e.target.value })}
              placeholder={isBank ? "Counter-party ledger" : ""}
            />
          </td>
          <td className="p-3">
            {/* Calendar Date Picker Input */}
            <input
              type="date"
              className="border border-slate-300 rounded px-2 py-1 text-[15.5px] w-40"
              value={toIsoDate(form.date)}
              onChange={(e) => setForm({ ...form, date: e.target.value })}
            />
          </td>
          <td className="p-3">
            <input
              type="text"
              className="border border-slate-300 rounded px-2 py-1 text-[15.5px] w-28"
              value={(isBank ? form.invoice_number : form.invoice_number) || ""}
              onChange={(e) =>
                setForm({ ...form, invoice_number: e.target.value })
              }
              placeholder={isBank ? "Chq/Ref no" : ""}
            />
          </td>
          {isBank ? (
            <td className="p-3">
              <div className="flex gap-2">
                <div>
                  <div className="text-[11px] text-slate-400 mb-0.5">Debit (₹)</div>
                  <input
                    type="number"
                    className="border border-slate-300 rounded px-2 py-1 text-[15.5px] w-24"
                    value={form.debit || 0}
                    onChange={(e) => handleDebitChange(e.target.value)}
                  />
                </div>
                <div>
                  <div className="text-[11px] text-slate-400 mb-0.5">Credit (₹)</div>
                  <input
                    type="number"
                    className="border border-slate-300 rounded px-2 py-1 text-[15.5px] w-24"
                    value={form.credit || 0}
                    onChange={(e) => handleCreditChange(e.target.value)}
                  />
                </div>
              </div>
              <input
                type="text"
                className="mt-1.5 border border-slate-300 rounded px-2 py-1 text-[13px] w-full"
                value={form.narration || ""}
                onChange={(e) => setForm({ ...form, narration: e.target.value })}
                placeholder="Narration"
              />
            </td>
          ) : (
            <td className="p-3">
              <div className="text-[11px] text-slate-400 mb-0.5">Taxable value</div>
              <input
                type="number"
                className="border border-slate-300 rounded px-2 py-1 text-[15.5px] w-28"
                value={form.taxable_value ?? 0}
                onChange={(e) => handleTaxableChange(e.target.value)}
              />
            </td>
          )}
          {isBank ? (
            <td className="p-3 text-[15.5px] text-slate-400">—</td>
          ) : (
            <td className="p-3">
              <div className="text-[11px] text-slate-400 mb-0.5">GST % → Total</div>
              <input
                type="number"
                className="border border-slate-300 rounded px-2 py-1 text-[15.5px] w-20"
                value={form.gst_rate ?? 0}
                onChange={(e) => handleRateChange(e.target.value)}
              />
              <div className="text-[15.5px] font-medium text-slate-900 mt-1">
                ₹{formatMoney(computedTotal)}
              </div>
              <div
                className={`text-[11px] ${
                  reconciled ? "text-emerald-600" : "text-rose-600"
                }`}
              >
                {reconciled
                  ? "Balances ✓"
                  : `≠ saved total (₹${formatMoney(form.total_value)})`}
              </div>
            </td>
          )}
        </>
      ) : (
        <>
          <td className="p-3 text-[15.5px] text-slate-800">{tx.party}</td>
          <td className="p-3 text-[15.5px] text-slate-600">{tx.date || "—"}</td>
          <td className="p-3 text-[15.5px] text-slate-600">
            {tx.invoice_number || "—"}
            {tx.possible_duplicate && (
              <span className="ml-1.5 text-[11px] font-semibold bg-rose-100 text-rose-700 px-1.5 py-0.5 rounded">
                DUPLICATE?
              </span>
            )}
            {tx.items && (() => {
              let itemList = [];
              try { itemList = JSON.parse(tx.items); } catch { itemList = []; }
              if (!itemList.length) return null;
              const names = itemList.map((it) => it.name).join(", ");
              return (
                <span
                  className="ml-1.5 text-[11px] font-semibold bg-indigo-100 text-indigo-700 px-1.5 py-0.5 rounded"
                  title={`Item-wise voucher (${itemList.length} item${itemList.length > 1 ? "s" : ""}): ${names}`}
                >
                  {itemList.length} ITEM{itemList.length > 1 ? "S" : ""}
                </span>
              );
            })()}
          </td>
          {isBank ? (
            <td className="p-3 text-[15.5px]">
              {tx.credit > 0 ? (
                <span className="font-medium text-emerald-700">Cr ₹{formatMoney(tx.credit)}</span>
              ) : (
                <span className="font-medium text-rose-700">Dr ₹{formatMoney(tx.debit)}</span>
              )}
              {tx.narration && (
                <div className="text-[11px] text-slate-400 max-w-[160px] truncate" title={tx.narration}>
                  {tx.narration}
                </div>
              )}
            </td>
          ) : (
            <td className="p-3 text-[15.5px] font-medium text-slate-900">
              ₹{formatMoney(tx.total_value)}
            </td>
          )}
          <td className="p-3 text-[15.5px] text-slate-600">
            {isBank ? (
              <span className="text-slate-400">—</span>
            ) : (
              <>
                {tx.gst_rate}%
                {tx.gst_rate_uncertain && !tx.rate_breakdown && (
                  <span
                    className="ml-1.5 text-[11px] font-semibold bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded"
                    title="This Excel doesn't break the invoice down by rate — likely a mixed-rate invoice (multiple GST slabs on one bill)."
                  >
                    RATE UNCERTAIN
                  </span>
                )}
                {tx.rate_breakdown && (
                  <span
                    className="ml-1.5 text-[11px] font-semibold bg-emerald-100 text-emerald-700 px-1.5 py-0.5 rounded"
                    title={`Resolved from supplier invoice '${tx.rate_breakdown_source || ""}'`}
                  >
                    RATE SPLIT RESOLVED
                  </span>
                )}
              </>
            )}
          </td>
        </>
      )}

      <td className="p-3">
        <ConfidenceBadge value={tx.confidence} />
        {tx.type === "SALES" && Number(tx.confidence || 0) < 0.8 && (
          <div className="mt-1 text-[11px] font-medium text-amber-700">
            Manual check required
          </div>
        )}
      </td>
      <td className="p-3">
        <StatusBadge status={tx.status} />
      </td>
      <td className="p-3">
        {isBank ? (
          <ReconciliationCell tx={tx} onChanged={onChanged} />
        ) : (
          <span className="text-[13px] text-slate-400">—</span>
        )}
      </td>
      <td className="p-3">
        {tx.tally_status && tx.tally_status !== "NOT_SENT" ? (
          <StatusBadge status={tx.tally_status} />
        ) : (
          <span className="text-[13px] text-slate-400">—</span>
        )}
      </td>
      <td className="p-3 whitespace-nowrap">
        {editing ? (
          <div className="flex gap-1.5">
            <button
              disabled={busy || !reconciled}
              onClick={save}
              title={
                !reconciled
                  ? "Taxable + GST must equal Total before saving"
                  : ""
              }
              className="text-[13px] bg-slate-900 text-white px-2.5 py-1 rounded-md disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Save
            </button>
            <button
              disabled={busy}
              onClick={cancelEdit}
              className="text-[13px] border border-slate-300 px-2.5 py-1 rounded-md"
            >
              Cancel
            </button>
          </div>
        ) : (
          <div className="flex gap-1.5">
            <button
              disabled={busy}
              onClick={startEdit}
              className="text-[13px] border border-slate-300 px-2.5 py-1 rounded-md hover:bg-slate-100"
            >
              Edit
            </button>
            <button
              disabled={busy || tx.status === "APPROVED"}
              onClick={() => act(api.approveTransaction)}
              className="text-[13px] bg-emerald-600 text-white px-2.5 py-1 rounded-md disabled:opacity-40"
            >
              Approve
            </button>
            <button
              disabled={busy || tx.status === "REJECTED"}
              onClick={() => act(api.rejectTransaction)}
              className="text-[13px] bg-rose-600 text-white px-2.5 py-1 rounded-md disabled:opacity-40"
            >
              Reject
            </button>
          </div>
        )}
      </td>
    </tr>
  );
}

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
    "Debit",
    "Credit",
    "Narration",
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
        tx.debit,
        tx.credit,
        tx.narration,
        tx.confidence != null ? Math.round(tx.confidence * 100) + "%" : "",
        tx.status,
        tx.tally_status,
      ]
        .map(escape)
        .join(",")
    );
  }
  const csv = "\uFEFF" + lines.join("\r\n");
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
  const [sortKey, setSortKey] = useState(null);
  const [sortDir, setSortDir] = useState("asc");
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("ALL");
  const [pushBusy, setPushBusy] = useState(false);
  const [pushResults, setPushResults] = useState([]);
  const [reconcileBusy, setReconcileBusy] = useState(false);
  const [reconcileResult, setReconcileResult] = useState(null);

  if (!bills.length) {
    return (
      <div className="text-[15.5px] text-slate-400 py-8 text-center">
        No transactions here.
      </div>
    );
  }

  const isBankTable = bills.some((b) => b.transaction?.type === "BANK");

  const statusesPresent = Array.from(
    new Set(bills.map((b) => b.transaction?.status).filter(Boolean))
  );

  const q = searchQuery.trim().toLowerCase();
  const filteredBills = bills.filter((b) => {
    const tx = b.transaction;
    if (!tx) return false;
    if (statusFilter !== "ALL" && tx.status !== statusFilter) return false;
    if (!q) return true;
    const haystack = `${tx.party || ""} ${tx.invoice_number || ""} ${tx.date || ""}`.toLowerCase();
    return haystack.includes(q);
  });

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

  const totals = sortedBills.reduce(
    (acc, b) => {
      const tx = b.transaction || {};
      acc.taxable += Number(tx.taxable_value) || 0;
      acc.cgst += Number(tx.cgst) || 0;
      acc.sgst += Number(tx.sgst) || 0;
      acc.igst += Number(tx.igst) || 0;
      acc.total += Number(tx.total_value) || 0;
      acc.debit += Number(tx.debit) || 0;
      acc.credit += Number(tx.credit) || 0;
      return acc;
    },
    { taxable: 0, cgst: 0, sgst: 0, igst: 0, total: 0, debit: 0, credit: 0 }
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

  // Pushes every APPROVED-and-not-yet-sent row currently visible in this
  // table, in EXACTLY the order it's displayed right now — i.e. whatever
  // the user has this page sorted/filtered by. That's what keeps a
  // multi-voucher push clean and predictable in Tally instead of an
  // unrelated global order.
  const pushableIds = sortedBills
    .map((b) => b.transaction)
    .filter((tx) => tx && tx.status === "APPROVED" && tx.tally_status !== "SENT")
    .map((tx) => tx.id);

  const handlePushInOrder = async () => {
    if (!pushableIds.length) return;
    setPushBusy(true);
    setPushResults([]);
    try {
      const results = await api.pushToTally({ order: pushableIds });
      setPushResults(results);
      onChanged();
    } catch (e) {
      alert(`Push failed: ${e.message}`);
    } finally {
      setPushBusy(false);
    }
  };

  const handleReconcile = async () => {
    setReconcileBusy(true);
    setReconcileResult(null);
    try {
      const result = await api.reconcileTransactions();
      setReconcileResult(result);
      onChanged();
    } catch (e) {
      alert(`Reconciliation failed: ${e.message}`);
    } finally {
      setReconcileBusy(false);
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
              className="text-[15.5px] border border-slate-300 rounded-lg pl-8 pr-3 py-1.5 w-64 focus:outline-none focus:ring-2 focus:ring-slate-900/10"
            />
          </div>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="text-[15.5px] border border-slate-300 rounded-lg px-2.5 py-1.5 bg-white"
          >
            <option value="ALL">All statuses</option>
            {statusesPresent.map((s) => (
              <option key={s} value={s}>
                {s.replace("_", " ")}
              </option>
            ))}
          </select>
          {(searchQuery || statusFilter !== "ALL") && (
            <span className="text-[13px] text-slate-400">
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
          className="text-[13px] font-medium border border-slate-300 px-3 py-1.5 rounded-lg hover:bg-slate-100 disabled:opacity-40 flex items-center gap-1.5"
          title="Export currently visible rows to CSV"
        >
          <Icon.Download width={14} height={14} />
          Export CSV
        </button>
      </div>

      {/* Bulk Action Toolbar */}
      <div className="flex items-center justify-between bg-slate-50 border border-slate-200 px-4 py-2.5 rounded-xl text-[15.5px]">
        <span className="text-slate-600 font-medium">
          {selectedIds.length} of {allTxIds.length} selected
        </span>

        <div className="flex gap-2">
          {isBankTable && (
            <button
              disabled={reconcileBusy}
              onClick={handleReconcile}
              title="Cross-checks bank credits/debits against your sales and purchase invoices by amount and date"
              className="bg-white border border-slate-300 text-slate-700 text-[13px] font-medium px-3 py-1.5 rounded-lg hover:bg-slate-100 disabled:opacity-40 transition-colors"
            >
              {reconcileBusy ? "Reconciling…" : "Reconcile with invoices"}
            </button>
          )}
          <button
            disabled={!selectedIds.length || bulkBusy}
            onClick={handleBulkApprove}
            className="bg-emerald-600 text-white text-[13px] font-medium px-3 py-1.5 rounded-lg hover:bg-emerald-700 disabled:opacity-40 transition-colors"
          >
            Approve Selected
          </button>
          <button
            disabled={!selectedIds.length || bulkBusy}
            onClick={handleBulkReject}
            className="bg-amber-600 text-white text-[13px] font-medium px-3 py-1.5 rounded-lg hover:bg-amber-700 disabled:opacity-40 transition-colors"
          >
            Reject Selected
          </button>
          <button
            disabled={!selectedIds.length || bulkBusy}
            onClick={handleBulkDelete}
            className="bg-rose-600 text-white text-[13px] font-medium px-3 py-1.5 rounded-lg hover:bg-rose-700 disabled:opacity-40 transition-colors"
          >
            Delete Selected
          </button>
          <button
            disabled={!pushableIds.length || pushBusy}
            onClick={handlePushInOrder}
            title="Pushes approved rows to Tally in exactly the order shown below (respects your current sort/filter)"
            className="bg-indigo-600 text-white text-[13px] font-medium px-3 py-1.5 rounded-lg hover:bg-indigo-700 disabled:opacity-40 transition-colors"
          >
            {pushBusy ? "Pushing…" : `Push ${pushableIds.length} to Tally (this order)`}
          </button>
        </div>
      </div>

      {reconcileResult && (
        <div className="bg-white border border-slate-200 rounded-xl p-3 text-[15.5px] text-slate-600">
          Reconciled: <span className="font-medium text-emerald-700">{reconcileResult.matched} matched</span>
          {reconcileResult.ambiguous > 0 && (
            <>, <span className="font-medium text-amber-700">{reconcileResult.ambiguous} ambiguous</span> (needs your pick)</>
          )}
          {reconcileResult.unmatched > 0 && (
            <>, <span className="font-medium text-slate-500">{reconcileResult.unmatched} unmatched</span></>
          )}
          {reconcileResult.unchanged > 0 && <>, {reconcileResult.unchanged} unchanged</>}.
        </div>
      )}

      {pushResults.length > 0 && (
        <div className="bg-white border border-slate-200 rounded-xl p-3 space-y-1 text-[15.5px]">
          {pushResults.map((r) => (
            <div
              key={r.transaction_id}
              className={`px-3 py-1.5 rounded-lg ${r.status === "SENT" ? "bg-emerald-50 text-emerald-700" : "bg-rose-50 text-rose-700"}`}
            >
              Transaction #{r.transaction_id}: {r.status} — {r.message}
            </div>
          ))}
        </div>
      )}

      {/* Table */}
      <div className="overflow-x-auto bg-white rounded-xl border border-slate-200">
        <table className="w-full">
          <thead>
            <tr className="text-left text-[13px] font-semibold text-slate-500 uppercase border-b border-slate-200 bg-slate-50">
              <th className="p-3 w-10">
                <input
                  type="checkbox"
                  checked={isAllSelected}
                  onChange={handleSelectAll}
                  className="rounded border-slate-300 text-slate-900 focus:ring-slate-900"
                />
              </th>
              <th className="p-3">Bill</th>
              <SortHeader colKey="party">{isBankTable ? "Counter-party" : "Party"}</SortHeader>
              <SortHeader colKey="date">Date</SortHeader>
              <SortHeader colKey="invoice_number">{isBankTable ? "Chq/Ref #" : "Invoice #"}</SortHeader>
              <SortHeader colKey="total_value">{isBankTable ? "Debit / Credit" : "Total"}</SortHeader>
              <SortHeader colKey="gst_rate">{isBankTable ? "—" : "GST %"}</SortHeader>
              <SortHeader colKey="confidence">Confidence</SortHeader>
              <SortHeader colKey="status">Status</SortHeader>
              <th className="p-3">Reconciled</th>
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
            <tr className="border-t-2 border-slate-200 bg-slate-50 text-[15.5px] font-semibold text-slate-700">
              {isBankTable ? (
                <>
                  <td className="p-3" colSpan={5}>
                    Totals ({sortedBills.length} row{sortedBills.length === 1 ? "" : "s"})
                  </td>
                  <td className="p-3 text-[13px] text-slate-500 font-normal" colSpan={6}>
                    Debit ₹{formatMoney(totals.debit)} · Credit ₹{formatMoney(totals.credit)}
                  </td>
                </>
              ) : (
                <>
                  <td className="p-3" colSpan={5}>
                    Totals ({sortedBills.length} row{sortedBills.length === 1 ? "" : "s"})
                    <span className="ml-2 text-[13px] text-slate-500 font-normal">
                      Taxable ₹{formatMoney(totals.taxable)}
                    </span>
                  </td>
                  <td className="p-3">₹{formatMoney(totals.total)}</td>
                  <td className="p-3 text-[13px] text-slate-500 font-normal" colSpan={5}>
                    CGST ₹{formatMoney(totals.cgst)} · SGST ₹{formatMoney(totals.sgst)} · IGST ₹{formatMoney(totals.igst)}
                  </td>
                </>
              )}
              <td className="p-3"></td>
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  );
}