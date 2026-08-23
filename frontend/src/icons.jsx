const base = {
  width: 20,
  height: 20,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2,
  strokeLinecap: "round",
  strokeLinejoin: "round",
};

export const Icon = {
  Dashboard: (p) => (
    <svg {...base} {...p}><rect x="3" y="3" width="7" height="9" rx="1" /><rect x="14" y="3" width="7" height="5" rx="1" /><rect x="14" y="12" width="7" height="9" rx="1" /><rect x="3" y="16" width="7" height="5" rx="1" /></svg>
  ),
  Documents: (p) => (
    <svg {...base} {...p}><path d="M4 4h10l6 6v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1Z" /><path d="M14 4v6h6" /></svg>
  ),
  Transactions: (p) => (
    <svg {...base} {...p}><line x1="4" y1="6" x2="20" y2="6" /><line x1="4" y1="12" x2="20" y2="12" /><line x1="4" y1="18" x2="14" y2="18" /></svg>
  ),
  Tally: (p) => (
    <svg {...base} {...p}><path d="M9 3v18" /><path d="M15 3v18" /><path d="M3 9h6" /><path d="M15 9h6" /><path d="M3 15h6" /><path d="M15 15h6" /></svg>
  ),
  Reports: (p) => (
    <svg {...base} {...p}><line x1="6" y1="20" x2="6" y2="10" /><line x1="12" y1="20" x2="12" y2="4" /><line x1="18" y1="20" x2="18" y2="14" /></svg>
  ),
  Sales: (p) => (
    <svg {...base} {...p}><circle cx="9" cy="20" r="1.4" /><circle cx="17" cy="20" r="1.4" /><path d="M3 4h2l2.2 11.2a2 2 0 0 0 2 1.6h7.6a2 2 0 0 0 2-1.6L21 8H6" /></svg>
  ),
  Purchase: (p) => (
    <svg {...base} {...p}><path d="M6 8h12l-1 12H7L6 8Z" /><path d="M9 8V6a3 3 0 0 1 6 0v2" /></svg>
  ),
  Gstr2b: (p) => (
    <svg {...base} {...p}><path d="M4 4h10l6 6v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1Z" /><line x1="7" y1="13" x2="15" y2="13" /><line x1="7" y1="17" x2="15" y2="17" /></svg>
  ),
  Review: (p) => (
    <svg {...base} {...p}><circle cx="12" cy="12" r="9" /><path d="m8 12 3 3 5-6" /></svg>
  ),
  Alert: (p) => (
    <svg {...base} {...p}><path d="M12 3 2 20h20L12 3Z" /><line x1="12" y1="9" x2="12" y2="14" /><circle cx="12" cy="17" r="0.6" fill="currentColor" /></svg>
  ),
  Parties: (p) => (
    <svg {...base} {...p}><circle cx="9" cy="8" r="3" /><path d="M3 20a6 6 0 0 1 12 0" /><circle cx="18" cy="9" r="2.4" /><path d="M15.5 20a5 5 0 0 1 5.8-5.6" /></svg>
  ),
  Percent: (p) => (
    <svg {...base} {...p}><line x1="5" y1="19" x2="19" y2="5" /><circle cx="7" cy="7" r="2" /><circle cx="17" cy="17" r="2" /></svg>
  ),
  User: (p) => (
    <svg {...base} {...p}><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></svg>
  ),
  Settings: (p) => (
    <svg {...base} {...p}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V9c.2.6.7 1 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z" /></svg>
  ),
  Bell: (p) => (
    <svg {...base} {...p}><path d="M6 9a6 6 0 0 1 12 0c0 5 2 6 2 6H4s2-1 2-6Z" /><path d="M10 20a2 2 0 0 0 4 0" /></svg>
  ),
  Chevron: (p) => (
    <svg {...base} {...p}><path d="m6 9 6 6 6-6" /></svg>
  ),
  Search: (p) => (
    <svg {...base} {...p}><circle cx="11" cy="11" r="7" /><line x1="21" y1="21" x2="16.65" y2="16.65" /></svg>
  ),
  Download: (p) => (
    <svg {...base} {...p}><path d="M12 3v12" /><path d="m7 10 5 5 5-5" /><path d="M5 21h14" /></svg>
  ),
  Upload: (p) => (
    <svg {...base} {...p}><path d="M12 15V4" /><path d="m7 9 5-5 5 5" /><path d="M5 19h14" /></svg>
  ),
  Eye: (p) => (
    <svg {...base} {...p}><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z" /><circle cx="12" cy="12" r="3" /></svg>
  ),
  Menu: (p) => (
    <svg {...base} {...p}><circle cx="12" cy="5" r="1.2" fill="currentColor" /><circle cx="12" cy="12" r="1.2" fill="currentColor" /><circle cx="12" cy="19" r="1.2" fill="currentColor" /></svg>
  ),
  Check: (p) => (
    <svg {...base} {...p}><path d="M20 6 9 17l-5-5" /></svg>
  ),
  X: (p) => (
    <svg {...base} {...p}><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></svg>
  ),
  Refresh: (p) => (
    <svg {...base} {...p}><path d="M21 12a9 9 0 1 1-3-6.7" /><path d="M21 3v6h-6" /></svg>
  ),
  Sidebar: (p) => (
    <svg {...base} {...p}><rect x="3" y="4" width="18" height="16" rx="2" /><line x1="9" y1="4" x2="9" y2="20" /></svg>
  ),
};
