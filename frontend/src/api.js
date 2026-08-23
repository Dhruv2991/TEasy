export const API_BASE = "http://localhost:8000";

function _qs(params) {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "");
  if (!entries.length) return "";
  return "?" + entries.map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&");
}

async function req(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, options);
  if (!res.ok) {
    let detail = "";
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      detail = await res.text();
    }
    throw new Error(detail || `${res.status} ${res.statusText}`);
  }
  const text = await res.text();
  return text ? JSON.parse(text) : null;
}

export const api = {
  getLicenseStatus: () => req("/api/license/status"),
  startTrial: (email) =>
    req("/api/license/start-trial", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    }),
  activateLicense: (license_key) =>
    req("/api/license/activate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ license_key }),
    }),
  createSubscription: (plan = "monthly") =>
    req(`/api/license/create-subscription?plan=${encodeURIComponent(plan)}`, { method: "POST" }),
  cancelSubscription: () => req("/api/license/cancel-subscription", { method: "POST" }),

  listDocuments: () => req("/documents"),
  getDocument: (id) => req(`/documents/${id}`),
  getDocumentLogs: (id) => req(`/documents/${id}/logs`),
  uploadDocument: (file, documentType) => {
    const form = new FormData();
    form.append("file", file);
    form.append("document_type", documentType);
    return req("/documents/upload", { method: "POST", body: form });
  },
  uploadGstr2b: (file) => {
    const form = new FormData();
    form.append("file", file);
    return req("/gstr2b/upload", { method: "POST", body: form });
  },
  uploadGstr2bPurchase: (file) => {
    const form = new FormData();
    form.append("file", file);
    return req("/gstr2b/purchase-upload", { method: "POST", body: form });
  },
  uploadSalesExcel: (file) => {
    const form = new FormData();
    form.append("file", file);
    return req("/gstr2b/sales-upload", { method: "POST", body: form });
  },

  listTransactions: (status) =>
    req(`/transactions${status ? `?status=${status}` : ""}`),
  updateTransaction: (id, payload) =>
    req(`/transactions/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  approveTransaction: (id) =>
    req(`/transactions/${id}/approve`, { method: "POST" }),
  rejectTransaction: (id) =>
    req(`/transactions/${id}/reject`, { method: "POST" }),

  // --- Bulk Actions ---
  bulkApproveTransactions: (ids) =>
    req("/transactions/bulk-approve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids }),
    }),
  bulkRejectTransactions: (ids) =>
    req("/transactions/bulk-reject", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids }),
    }),
  bulkDeleteTransactions: (ids) =>
    req("/transactions/bulk-delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids }),
    }),

  listParties: () => req("/transactions/parties"),
  renameParty: (oldName, newName, docType) =>
    req("/transactions/rename-party", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ old_name: oldName, new_name: newName, doc_type: docType || null }),
    }),

  getTallyStatus: () => req("/tally/status"),
  getTallyConfig: () => req("/tally/config"),
  getTallyLedgers: (forceRefresh = false) =>
    req(`/tally/ledgers${forceRefresh ? "?force_refresh=true" : ""}`),
  updateTallyConfig: (config) =>
    req("/tally/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    }),
  pushToTally: () => req("/tally/push", { method: "POST" }),
  pushSingleToTally: (id) => req(`/tally/push/${id}`, { method: "POST" }),

  getRecentActivity: (limit = 10) => req(`/activity/recent?limit=${limit}`),

  getSettings: () => req("/settings"),
  saveSettings: (payload) =>
    req("/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  getSettingsStatus: () => req("/settings/status"),

  uploadBankStatement: (file) => {
    const formData = new FormData();
    formData.append("file", file);
    return req("/bank/upload", { method: "POST", body: formData });
  },

  getReportSummary: (params = {}) =>
    req(`/reports/summary${_qs(params)}`),
  getReportByMonth: (params = {}) =>
    req(`/reports/by-month${_qs(params)}`),
  getReportByParty: (params = {}) =>
    req(`/reports/by-party${_qs(params)}`),
  getReportByGstRate: (params = {}) =>
    req(`/reports/by-gst-rate${_qs(params)}`),

  pushBankToTally: (payload) =>
    req("/bank/push-to-tally", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
};

export function cropImageUrl(cropPath) {
  if (!cropPath) return null;
  return `${API_BASE}/files/processed/${cropPath.split(/[\\/]/).pop()}`;
}