import { useState } from "react";
import { api } from "./api.js";

export default function BankStatementPage() {
  const [loading, setLoading] = useState(false);
  const [pushing, setPushing] = useState(false);
  const [bankData, setBankData] = useState(null);
  const [statusMsg, setStatusMsg] = useState("");

  const handleFileUpload = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setLoading(true);
    setStatusMsg("");
    try {
      const res = await api.uploadBankStatement(file);
      setBankData(res);
    } catch (err) {
      alert(`Upload error: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const handlePushToTally = async () => {
    if (!bankData || !bankData.transactions?.length) return;

    setPushing(true);
    setStatusMsg("");
    try {
      const res = await api.pushBankToTally({
        company_name: bankData.company_name,
        bank_ledger: bankData.bank_ledger,
        transactions: bankData.transactions,
      });

      if (res.pushed > 0) {
        setStatusMsg(`Successfully pushed ${res.pushed} Journal vouchers to Tally!`);
      } else {
        setStatusMsg(`Failed to push entries. Check if '${bankData.company_name}' is open in Tally.`);
      }
    } catch (err) {
      alert(`Push error: ${err.message}`);
    } finally {
      setPushing(false);
    }
  };

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm space-y-4">
        <h1 className="text-xl font-bold text-slate-800">Bank Statement Integration</h1>
        <input
          type="file"
          accept="application/pdf"
          onChange={handleFileUpload}
          disabled={loading}
          className="block w-full text-sm text-slate-500 file:mr-4 file:py-2 file:px-4 file:rounded-md file:border-0 file:text-sm file:font-semibold file:bg-indigo-50 file:text-indigo-700 hover:file:bg-indigo-100"
        />
        {loading && <p className="text-sm text-indigo-600">Reading PDF bank statement...</p>}
      </div>

      {bankData && (
        <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm space-y-4">
          <div className="flex justify-between items-center">
            <div>
              <p className="text-sm font-semibold text-slate-700">Company: <span className="text-indigo-600">{bankData.company_name}</span></p>
              <p className="text-sm font-semibold text-slate-700">Bank Ledger: <span className="text-indigo-600">{bankData.bank_ledger}</span></p>
            </div>
            <button
              onClick={handlePushToTally}
              disabled={pushing}
              className="px-4 py-2 bg-indigo-600 text-white font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-50"
            >
              {pushing ? "Pushing..." : "Write to Tally"}
            </button>
          </div>

          {statusMsg && <p className="text-sm font-medium text-emerald-600">{statusMsg}</p>}

          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm text-slate-600 border-collapse">
              <thead>
                <tr className="bg-slate-100 text-slate-700 border-b">
                  <th className="p-2">Txn Date</th>
                  <th className="p-2">Value Date</th>
                  <th className="p-2">Description / Particulars</th>
                  <th className="p-2">Chq No</th>
                  <th className="p-2">Branch</th>
                  <th className="p-2 text-right">Debit (₹)</th>
                  <th className="p-2 text-right">Credit (₹)</th>
                  <th className="p-2 text-right">Balance (₹)</th>
                </tr>
              </thead>
              <tbody>
                {bankData.transactions.map((tx, idx) => (
                  <tr key={idx} className="border-b hover:bg-slate-50">
                    <td className="p-2 whitespace-nowrap">{tx.txn_date}</td>
                    <td className="p-2 whitespace-nowrap">{tx.value_date || "-"}</td>
                    <td className="p-2 font-medium text-slate-800">
                      <div>{tx.particulars}</div>
                      <div className="text-xs text-slate-400 font-normal">{tx.narration}</div>
                    </td>
                    <td className="p-2">{tx.cheque_no || "-"}</td>
                    <td className="p-2">{tx.branch_code || "-"}</td>
                    <td className="p-2 text-right text-rose-600 font-medium">
                      {tx.debit > 0 ? tx.debit.toLocaleString("en-IN", { minimumFractionDigits: 2 }) : "-"}
                    </td>
                    <td className="p-2 text-right text-emerald-600 font-medium">
                      {tx.credit > 0 ? tx.credit.toLocaleString("en-IN", { minimumFractionDigits: 2 }) : "-"}
                    </td>
                    <td className={`p-2 text-right font-medium ${tx.balance < 0 ? "text-rose-500" : "text-slate-700"}`}>
                      {tx.balance.toLocaleString("en-IN", { minimumFractionDigits: 2 })}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}