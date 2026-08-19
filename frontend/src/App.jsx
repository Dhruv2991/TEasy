import { useEffect, useState, useCallback } from "react";
import Sidebar from "./Sidebar.jsx";
import { TopBar } from "./ui.jsx";
import { api } from "./api.js";
import DashboardPage from "./DashboardPage.jsx";
import AllDocumentsPage from "./AllDocumentsPage.jsx";
import AllTransactionsPage from "./AllTransactionsPage.jsx";
import DocumentTypePage from "./DocumentTypePage.jsx";
import ReviewApprovePage from "./ReviewApprovePage.jsx";
import PendingIssuesPage from "./PendingIssuesPage.jsx";
import TallyIntegrationPage from "./TallyIntegrationPage.jsx";
import ComingSoonPage from "./ComingSoonPage.jsx";
import GeneralSettingsPage from "./GeneralSettingsPage.jsx";
import FirstRunSetup from "./FirstRunSetup.jsx";
import BankStatementPage from "./BankStatementPage.jsx";

const PAGE_META = {
  dashboard: { title: "Dashboard", subtitle: "Welcome back" },
  documents: { title: "Documents", subtitle: "All uploaded documents" },
  transactions: { title: "Transactions", subtitle: "Every extracted transaction" },
  bank: { title: "Bank Statements", subtitle: "Extract PDF bank statements and push to Tally" },
  tally: { title: "Tally Integration", subtitle: "Push approved transactions into Tally Prime" },
  reports: { title: "Reports", subtitle: "" },
  sales: { title: "Sales Bills", subtitle: "" },
  purchase: { title: "Purchase Bills (B2B Excel)", subtitle: "Exact values imported from GSTR-2B B2B data" },
  gstr2b: { title: "GSTR-2B (Discount Notes)", subtitle: "Credit/debit notes imported from GSTR-2B Excel" },
  review: { title: "Review & Approve", subtitle: "" },
  issues: { title: "Pending Issues", subtitle: "" },
  parties: { title: "Parties & Ledgers", subtitle: "" },
  "gst-settings": { title: "GST Settings", subtitle: "" },
  users: { title: "Users", subtitle: "" },
  "general-settings": { title: "General Settings", subtitle: "" },
};

export default function App() {
  const [page, setPage] = useState("dashboard");
  const [counts, setCounts] = useState({ review: 0, issues: 0 });
  const [tallyConnected, setTallyConnected] = useState(false);
  const [needsSetup, setNeedsSetup] = useState(null); // null = still checking

  useEffect(() => {
    api.getSettingsStatus()
      .then((s) => setNeedsSetup(!s.groq_key_set))
      .catch(() => setNeedsSetup(false)); // backend not up yet — don't block on a failed check
  }, []);

  const refreshCounts = useCallback(async () => {
    try {
      const [txs, docs, tallyStatus] = await Promise.all([
        api.listTransactions("NEEDS_REVIEW"),
        api.listDocuments(),
        api.getTallyStatus().catch(() => ({ connected: false })),
      ]);
      setCounts({
        review: txs.length,
        issues: docs.filter((d) => d.status === "FAILED").length,
      });
      setTallyConnected(tallyStatus.connected);
    } catch {
      // backend not reachable yet — sidebar just shows zero counts, no need to alarm the user
    }
  }, []);

  useEffect(() => {
    refreshCounts();
    const t = setInterval(refreshCounts, 8000);
    return () => clearInterval(t);
  }, [refreshCounts]);

  if (needsSetup === null) {
    return <div className="min-h-screen flex items-center justify-center text-slate-400 text-sm">Starting TEasy…</div>;
  }
  if (needsSetup) {
    return <FirstRunSetup onDone={() => setNeedsSetup(false)} />;
  }

  const meta = PAGE_META[page] || { title: page, subtitle: "" };

  const renderPage = () => {
    switch (page) {
      case "dashboard":
        return <DashboardPage onNavigate={setPage} />;
      case "documents":
        return <AllDocumentsPage />;
      case "transactions":
        return <AllTransactionsPage />;
      case "bank":
        return <BankStatementPage />;
      case "tally":
        return <TallyIntegrationPage />;
      case "sales":
      case "purchase":
      case "gstr2b":
        return <DocumentTypePage pageKey={page} />;
      case "review":
        return <ReviewApprovePage />;
      case "issues":
        return <PendingIssuesPage />;
      case "reports":
        return <ComingSoonPage title="Reports" description="Sales/purchase/GST summary reports are on the roadmap — for now, use Tally's own reports once vouchers are pushed." />;
      case "parties":
        return <ComingSoonPage title="Parties & Ledgers" description="Fuzzy ledger-name matching against your Tally ledgers isn't built yet — for now, party names must match exactly for a successful Tally push." />;
      case "gst-settings":
        return <ComingSoonPage title="GST Settings" description="Company GSTIN, state code, and default tax rate settings are on the roadmap." />;
      case "users":
        return <ComingSoonPage title="Users" description="This is currently a single-user local app — multi-user accounts aren't built yet." />;
      case "general-settings":
        return <GeneralSettingsPage />;
      default:
        return <ComingSoonPage title={page} />;
    }
  };

  return (
    <div className="flex bg-slate-50 min-h-screen">
      <Sidebar active={page} onNavigate={setPage} counts={counts} tallyConnected={tallyConnected} />
      <div className="flex-1 min-w-0">
        <TopBar title={meta.title} subtitle={meta.subtitle} onNavigate={setPage} alertCount={counts.issues} />
        {renderPage()}
      </div>
    </div>
  );
}