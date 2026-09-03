// Vercel serverless function — served at /api/config
//
// This replaces a static config.js file so that no URL is ever hardcoded
// or committed to the repo. Instead, it reads a real environment variable
// at request time, set in Vercel → Project → Settings → Environment
// Variables:
//
//   LICENSE_SERVICE_URL    e.g. https://teasy-vusw.onrender.com
//
// The Windows installer download also goes through the license service now
// (POST /download/installer/request — see license-service/main.py), so
// there's no separate WINDOWS_DOWNLOAD_URL to set here anymore.
//
// The response is cached at the edge for a few minutes so it doesn't add
// a slow request on every page load, but picks up a new env var value
// shortly after you change it in Vercel (no redeploy needed).

export default function handler(req, res) {
  const licenseServiceUrl = process.env.LICENSE_SERVICE_URL || "";

  res.setHeader("Content-Type", "application/javascript; charset=utf-8");
  res.setHeader("Cache-Control", "public, max-age=0, s-maxage=300, stale-while-revalidate=60");

  res.status(200).send(
    `window.TEASY_CONFIG = ${JSON.stringify({
      LICENSE_SERVICE_URL: licenseServiceUrl,
    })};`
  );
}
