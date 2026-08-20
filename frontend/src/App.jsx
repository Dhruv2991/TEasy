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
import ReportsPage from "./ReportsPage.jsx";
import GstSettingsPage from "./GstSettingsPage.jsx";
import PartiesPage from "./PartiesPage.jsx";
import GeneralSettingsPage from "./GeneralSettingsPage.jsx";
import FirstRunSetup from "./FirstRunSetup.jsx";
import LicenseGate from "./LicenseGate.jsx";
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
  const [license, setLicense] = useState(null); // null = still checking

  const checkLicense = useCallback(() => {
    api.getLicenseStatus()
      .then(setLicense)
      .catch(() => setLicense({ activated: false, valid: false, status: "none" }));
  }, []);

  useEffect(() => {
    checkLicense();
    // Re-verify with the license service periodically while the app is
    // open, so a cancelled/lapsed subscription is caught even on a long
    // running session, and so the local cache that the backend's per-request
    // check relies on doesn't go stale. Every 30 min is plenty — this isn't
    // meant to catch things instantly, just well within the offline grace
    // window.
    const t = setInterval(checkLicense, 30 * 60 * 1000);
    return () => clearInterval(t);
  }, [checkLicense]);

  useEffect(() => {
    if (!license?.valid) return;
    api.getSettingsStatus()
      .then((s) => setNeedsSetup(!s.groq_key_set))
      .catch(() => setNeedsSetup(false)); // backend not up yet — don't block on a failed check
  }, [license]);

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

  if (license === null) {
    return <div className="min-h-screen flex items-center justify-center text-slate-400 text-sm">Starting TEasy…</div>;
  }
  if (!license.valid) {
    return <LicenseGate license={license} onRecheck={checkLicense} />;
  }
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
        return <ReportsPage />;
      case "parties":
        return <PartiesPage />;
      case "gst-settings":
        return <GstSettingsPage />;
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