// Local-dev fallback only — in production, index.html loads /api/config
// (landing/api/config.js), a Vercel serverless function that reads the
// real LICENSE_SERVICE_URL from an environment variable instead. Edit
// this file only if you're previewing the landing page without Vercel.
//
// The Windows installer download goes through the license service too now
// (POST /download/installer/request) — no separate URL to set for it.
window.TEASY_CONFIG = {
  LICENSE_SERVICE_URL: "https://your-license-service.up.railway.app",
};
