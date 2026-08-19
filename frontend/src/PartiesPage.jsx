import { useEffect, useState, useCallback, useMemo } from "react";
import { api } from "./api.js";
import { Icon } from "./icons.jsx";

// Simple, dependency-free fuzzy match: normalize both strings and score by
// Levenshtein distance relative to length. Good enough to surface likely
// candidates (e.g. "Sharma Trdrs" vs "Sharma Traders") without pulling in a
// library — the user always confirms before anything is renamed.
function normalize(s) {
  return (s || "").toLowerCase().replace(/[^a-z0-9]/g, "");
}

function levenshtein(a, b) {
  const m = a.length, n = b.length;
  if (m === 0) return n;
  if (n === 0) return m;
  const dp = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = 0; i <= m; i++) dp[i][0] = i;
  for (let j = 0; j <= n; j++) dp[0][j] = j;
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      dp[i][j] = a[i - 1] === b[j - 1]
        ? dp[i - 1][j - 1]
        : 1 + Math.min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1]);
    }
  }
  return dp[m][n];
}

function similarity(a, b) {
  const na = normalize(a), nb = normalize(b);
  if (!na || !nb) return 0;
  const dist = levenshtein(na, nb);
  return 1 - dist / Math.max(na.length, nb.length);
}

function bestMatch(partyName, ledgers) {
  let best = null;
  let bestScore = 0;
  for (const l of ledgers) {
    const score = similarity(partyName, l.name);
    if (score > bestScore) {
      bestScore = score;
      best = l;
    }
  }
  return best ? { ledger: best, score: bestScore } : null;
}

export default function PartiesPage() {
  const [parties, setParties] = useState([]);
  const [ledgers, setLedgers] = useState([]);
  const [ledgerError, setLedgerError] = useState("");
  const [loading, setLoading] = useState(true);
  const [renaming, setRenaming] = useState(null); // party name currently being renamed
  const [editValue, setEditValue] = useState("");
  const [toast, setToast] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setLedgerError("");
    try {
      const p = await api.listParties();
      setParties(p);
    } catch {
      setParties([]);
    }
    try {
      const l = await api.getTallyLedgers();
      setLedgers(l);
    } catch (e) {
      setLedgers([]);
      setLedgerError(e.message);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const ledgerNameSet = useMemo(() => new Set(ledgers.map((l) => l.name)), [ledgers]);

  const startRename = (party, suggested) => {
    setRenaming(party);
    setEditValue(suggested || party);
  };

  const applyRename = async (oldName) => {
    if (!editValue.trim() || editValue.trim() === oldName) {
      setRenaming(null);
      return;
    }
    try {
      const res = await api.renameParty(oldName, editValue.trim());
      setToast(`Renamed "${oldName}" → "${editValue.trim()}" (${res.updated_count} transaction(s) updated)`);
      setRenaming(null);
      load();
      setTimeout(() => setToast(""), 4000);
    } catch (e) {
      setToast(`Error: ${e.message}`);
    }
  };

  return (
    <div className="p-6 space-y-6">
      <div className="bg-white rounded-xl border border-slate-200 p-5">
        <h2 className="font-semibold text-slate-900 mb-1">Match party names to Tally ledgers</h2>
        <p className="text-sm text-slate-500">
          Tally needs an exact ledger-name match to accept a voucher push. Here you can compare
          the party names read off bills/Excel against your live Tally ledger list, and fix any
          mismatches in bulk before pushing. Transactions already sent to Tally are left as-is.
        </p>
      </div>

      {ledgerError && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 text-sm text-amber-700">
          Couldn't load the live ledger list from Tally ({ledgerError}). Make sure Tally Prime is
          open with its HTTP server enabled. You can still rename parties manually below.
        </div>
      )}

      {toast && (
        <div className="bg-emerald-50 border border-emerald-200 rounded-xl p-3 text-sm text-emerald-700">
          {toast}
        </div>
      )}

      {loading ? (
        <div className="text-slate-400 text-sm">Loading…</div>
      ) : parties.length === 0 ? (
        <div className="bg-white rounded-xl border border-slate-200 p-12 text-center text-slate-400 text-sm">
          No parties yet — they'll show up here once you have transactions.
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-slate-400 border-b border-slate-100 bg-slate-50">
                <th className="py-2 px-4 font-medium">Party (as recorded)</th>
                <th className="py-2 px-4 font-medium">Transactions</th>
                <th className="py-2 px-4 font-medium">Sent to Tally</th>
                <th className="py-2 px-4 font-medium">Match status</th>
                <th className="py-2 px-4 font-medium text-right">Action</th>
              </tr>
            </thead>
            <tbody>
              {parties.map((p) => {
                const exact = ledgerNameSet.has(p.party);
                const match = !exact && ledgers.length > 0 ? bestMatch(p.party, ledgers) : null;
                const goodSuggestion = match && match.score >= 0.6 && match.score < 1;
                const isEditing = renaming === p.party;

                return (
                  <tr key={p.party} className="border-b border-slate-50 last:border-0 align-top">
                    <td className="py-2.5 px-4 text-slate-700 font-medium">{p.party}</td>
                    <td className="py-2.5 px-4 text-slate-500">{p.count} ({p.types.join(", ")})</td>
                    <td className="py-2.5 px-4 text-slate-500">{p.sent_to_tally}</td>
                    <td className="py-2.5 px-4">
                      {exact ? (
                        <span className="inline-flex items-center gap-1 text-xs bg-emerald-100 text-emerald-700 px-2 py-0.5 rounded-full">
                          <Icon.Check width={12} height={12} /> Matches Tally ledger
                        </span>
                      ) : ledgers.length === 0 ? (
                        <span className="text-xs text-slate-400">No ledger list loaded</span>
                      ) : goodSuggestion ? (
                        <span className="text-xs text-amber-700 bg-amber-50 px-2 py-0.5 rounded-full">
                          Possible match: "{match.ledger.name}" ({Math.round(match.score * 100)}%)
                        </span>
                      ) : (
                        <span className="text-xs text-rose-600 bg-rose-50 px-2 py-0.5 rounded-full">
                          No ledger match found
                        </span>
                      )}
                    </td>
                    <td className="py-2.5 px-4 text-right">
                      {isEditing ? (
                        <div className="flex items-center gap-2 justify-end">
                          <input
                            list={`ledger-options-${p.party}`}
                            value={editValue}
                            onChange={(e) => setEditValue(e.target.value)}
                            className="border border-slate-300 rounded-lg px-2 py-1 text-xs w-48"
                            autoFocus
                          />
                          <datalist id={`ledger-options-${p.party}`}>
                            {ledgers.map((l) => (
                              <option key={l.name} value={l.name} />
                            ))}
                          </datalist>
                          <button
                            onClick={() => applyRename(p.party)}
                            className="text-xs px-2 py-1 rounded-lg bg-violet-600 text-white hover:bg-violet-700"
                          >
                            Save
                          </button>
                          <button
                            onClick={() => setRenaming(null)}
                            className="text-xs px-2 py-1 rounded-lg border border-slate-300 hover:bg-slate-50"
                          >
                            Cancel
                          </button>
                        </div>
                      ) : (
                        <button
                          onClick={() => startRename(p.party, goodSuggestion ? match.ledger.name : "")}
                          disabled={exact}
                          className="text-xs px-3 py-1.5 rounded-lg border border-slate-300 hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed"
                        >
                          {exact ? "OK" : goodSuggestion ? "Use suggestion" : "Fix name"}
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
