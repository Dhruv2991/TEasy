// Vercel serverless function — served at /api/config
//
// This replaces a static config.js file so that no URL is ever hardcoded
// or committed to the repo. Instead, it reads real environment variables
// at request time, set in Vercel → Project → Settings → Environment
// Variables:
//
//   LICENSE_SERVICE_URL    e.g. https://teasy-vusw.onrender.com
//   WINDOWS_DOWNLOAD_URL   e.g. a GitHub Releases asset link (optional)
//
// The response is cached at the edge for a few minutes so it doesn't add
// a slow request on every page load, but picks up new env var values
// shortly after you change them in Vercel (no redeploy needed).

export default function handler(req, res) {
  const licenseServiceUrl = process.env.LICENSE_SERVICE_URL || "";
  const windowsDownloadUrl = process.env.WINDOWS_DOWNLOAD_URL || "#";

  res.setHeader("Content-Type", "application/javascript; charset=utf-8");
  res.setHeader("Cache-Control", "public, max-age=0, s-maxage=300, stale-while-revalidate=60");

  res.status(200).send(
    `window.TEASY_CONFIG = ${JSON.stringify({
      LICENSE_SERVICE_URL: licenseServiceUrl,
      WINDOWS_DOWNLOAD_URL: windowsDownloadUrl,
    })};`
  );
}
