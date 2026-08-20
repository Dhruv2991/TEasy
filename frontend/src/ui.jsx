import { Icon } from "./icons.jsx";

// Always renders as ₹X,XXX.XX — exactly two decimal places, every time.
// Used everywhere a rupee amount is shown so Sales, Purchase, GSTR-2B
// discount notes, Bank Statements, and Reports all match the same format.
export function formatMoney(value) {
  const n = Number(value) || 0;
  return n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function StatCard({ icon: IconComp, color, label, value, sublabel }) {
  const colorMap = {
    green: "bg-emerald-50 text-emerald-600",
    orange: "bg-orange-50 text-orange-600",
    purple: "bg-violet-50 text-violet-600",
    blue: "bg-blue-50 text-blue-600",
    amber: "bg-amber-50 text-amber-600",
  };
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-4 flex-1 min-w-[180px]">
      <div className="flex items-center justify-between mb-3">
        <div className={`w-10 h-10 rounded-lg flex items-center justify-center ${colorMap[color]}`}>
          <IconComp width={20} height={20} />
        </div>
      </div>
      <div className="text-sm text-slate-500">{label}</div>
      <div className="text-2xl font-semibold text-slate-900 mt-0.5">{value}</div>
      {sublabel && <div className="text-xs text-slate-400 mt-1">{sublabel}</div>}
    </div>
  );
}

export function TopBar({ title, subtitle, onNavigate, alertCount, companyName }) {
  return (
    <div className="flex items-center justify-between px-6 py-4 bg-white border-b border-slate-200">
      <div>
        <h1 className="text-lg font-semibold text-slate-900">{title}</h1>
        {subtitle && <p className="text-sm text-slate-500">{subtitle}</p>}
      </div>
      <div className="flex items-center gap-4">
        {companyName && (
          <div className="text-sm text-slate-500 border border-slate-200 rounded-lg px-3 py-1.5">
            {companyName}
          </div>
        )}
        <button
          onClick={() => onNavigate?.("issues")}
          className="relative w-9 h-9 rounded-full border border-slate-200 flex items-center justify-center text-slate-500 hover:bg-slate-50"
        >
          <Icon.Bell width={18} height={18} />
          {alertCount > 0 && (
            <span className="absolute -top-1 -right-1 bg-rose-500 text-white text-[10px] rounded-full w-4 h-4 flex items-center justify-center">
              {alertCount}
            </span>
          )}
        </button>
        <div className="flex items-center gap-2">
          <div className="w-9 h-9 rounded-full bg-violet-600 text-white flex items-center justify-center text-sm font-semibold">
            U
          </div>
        </div>
      </div>
    </div>
  );
}

export function StatusBadge({ status }) {
  const styles = {
    UPLOADED: "bg-slate-100 text-slate-600",
    PROCESSING: "bg-amber-100 text-amber-700",
    NEEDS_REVIEW: "bg-amber-100 text-amber-700",
    APPROVED: "bg-emerald-100 text-emerald-700",
    REJECTED: "bg-rose-100 text-rose-700",
    FAILED: "bg-rose-100 text-rose-700",
    SENT: "bg-emerald-100 text-emerald-700",
    NOT_SENT: "bg-slate-100 text-slate-600",
  };
  return (
    <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${styles[status] || "bg-slate-100 text-slate-600"}`}>
      {status?.replace("_", " ")}
    </span>
  );
}

export function ConfidenceBadge({ value }) {
  const pct = Math.round((value || 0) * 100);
  const cls = pct >= 90 ? "text-emerald-600" : pct >= 60 ? "text-amber-600" : "text-rose-600";
  return <span className={`font-semibold ${cls}`}>{pct}%</span>;
}
