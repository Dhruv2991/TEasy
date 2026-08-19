import { Icon } from "./icons.jsx";

const MAIN_ITEMS = [
  { key: "dashboard", label: "Dashboard", icon: Icon.Dashboard },
  { key: "documents", label: "Documents", icon: Icon.Documents },
  { key: "transactions", label: "Transactions", icon: Icon.Transactions },
  { key: "bank", label: "Bank Statements", icon: Icon.Documents },
  { key: "tally", label: "Tally Integration", icon: Icon.Tally },
  { key: "reports", label: "Reports", icon: Icon.Reports },
];

const DOCUMENT_ITEMS = [
  { key: "sales", label: "Sales Bills", icon: Icon.Sales },
  { key: "purchase", label: "Purchase (B2B Excel)", icon: Icon.Purchase },
  { key: "gstr2b", label: "Discount (GSTR-2B Excel)", icon: Icon.Gstr2b },
];

const WORKFLOW_ITEMS = [
  { key: "review", label: "Review & Approve", icon: Icon.Review, badgeKey: "review" },
  { key: "issues", label: "Pending Issues", icon: Icon.Alert, badgeKey: "issues" },
];

const SETTINGS_ITEMS = [
  { key: "parties", label: "Parties & Ledgers", icon: Icon.Parties },
  { key: "gst-settings", label: "GST Settings", icon: Icon.Percent },
  { key: "users", label: "Users", icon: Icon.User },
  { key: "general-settings", label: "General Settings", icon: Icon.Settings },
];

function NavItem({ item, active, onClick, badgeCount }) {
  const IconComp = item.icon;
  return (
    <button
      onClick={onClick}
      className={`ta-nav-item w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors ${active ? "active" : ""}`}
    >
      <IconComp className="shrink-0" width={18} height={18} />
      <span className="flex-1 text-left">{item.label}</span>
      {badgeCount > 0 && (
        <span className="ta-nav-badge text-xs font-semibold rounded-full px-1.5 py-0.5 min-w-[20px] text-center">
          {badgeCount}
        </span>
      )}
    </button>
  );
}

function SectionLabel({ children }) {
  return <div className="ta-section-label px-3 pt-4 pb-1 text-[11px] font-semibold tracking-wider uppercase">{children}</div>;
}

export default function Sidebar({ active, onNavigate, counts, tallyConnected }) {
  return (
    <aside className="ta-sidebar w-64 shrink-0 flex flex-col h-screen sticky top-0">
      <div className="flex items-center gap-2 px-4 py-4 ta-sidebar-header">
        <div className="w-9 h-9 rounded-lg bg-indigo-600 flex items-center justify-center font-bold text-white">AI</div>
        <div>
          <div className="font-semibold leading-tight">TEasy</div>
          <div className="text-xs leading-tight ta-sidebar-subtitle">Accounting Assistant</div>
        </div>
      </div>

      <nav className="flex-1 overflow-y-auto px-2 pb-4">
        <SectionLabel>Main</SectionLabel>
        <div className="space-y-1">
          {MAIN_ITEMS.map((item) => (
            <NavItem key={item.key} item={item} active={active === item.key} onClick={() => onNavigate(item.key)} />
          ))}
        </div>

        <SectionLabel>Documents</SectionLabel>
        <div className="space-y-1">
          {DOCUMENT_ITEMS.map((item) => (
            <NavItem key={item.key} item={item} active={active === item.key} onClick={() => onNavigate(item.key)} />
          ))}
        </div>

        <SectionLabel>Workflow</SectionLabel>
        <div className="space-y-1">
          {WORKFLOW_ITEMS.map((item) => (
            <NavItem
              key={item.key}
              item={item}
              active={active === item.key}
              onClick={() => onNavigate(item.key)}
              badgeCount={counts?.[item.badgeKey] || 0}
            />
          ))}
        </div>

        <SectionLabel>Settings</SectionLabel>
        <div className="space-y-1">
          {SETTINGS_ITEMS.map((item) => (
            <NavItem key={item.key} item={item} active={active === item.key} onClick={() => onNavigate(item.key)} />
          ))}
        </div>
      </nav>

      <div className="p-3 ta-sidebar-footer">
        <div className="ta-sidebar-card rounded-lg p-3">
          <div className="flex items-center gap-2 text-xs">
            <span className={`w-2 h-2 rounded-full ${tallyConnected ? "bg-emerald-400" : "bg-rose-400"}`} />
            <span className="font-medium">Tally Status</span>
          </div>
          <div className={`text-xs mt-0.5 ${tallyConnected ? "text-emerald-400" : "text-rose-400"}`}>
            {tallyConnected ? "Connected" : "Not connected"}
          </div>
          <button
            onClick={() => onNavigate("tally")}
            className="ta-sidebar-manage-btn mt-2 w-full text-xs rounded-md py-1.5 flex items-center justify-center gap-1"
          >
            <Icon.Refresh width={14} height={14} />
            Manage
          </button>
        </div>
      </div>
    </aside>
  );
}