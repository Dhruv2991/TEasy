// -------- signature moment: reveal the receipt scan + stamp once it's in view --------
(function () {
  const receipt = document.getElementById("receipt");
  if (!receipt) return;

  const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (prefersReducedMotion) {
    receipt.classList.add("scanned");
    return;
  }

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          receipt.classList.add("scanned");
          observer.disconnect();
        }
      });
    },
    { threshold: 0.5 }
  );
  observer.observe(receipt);
})();

// -------- trial signup --------
(function () {
  const form = document.getElementById("trial-form");
  const note = document.getElementById("trial-note");
  const result = document.getElementById("trial-result");
  const keyEl = document.getElementById("trial-key");
  const downloadLink = document.getElementById("download-link");
  if (!form) return;

  downloadLink.href = (window.TEASY_CONFIG && window.TEASY_CONFIG.WINDOWS_DOWNLOAD_URL) || "#";

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = document.getElementById("trial-email").value.trim();
    if (!email) return;

    const button = form.querySelector("button");
    const originalLabel = button.textContent;
    button.disabled = true;
    button.textContent = "Starting…";
    note.classList.remove("error");
    note.textContent = "No card required. Runs on your own PC — your data never leaves it.";

    try {
      const base = (window.TEASY_CONFIG && window.TEASY_CONFIG.LICENSE_SERVICE_URL) || "";
      const res = await fetch(`${base}/trial/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || "Something went wrong starting your trial.");
      }
      const data = await res.json();
      keyEl.textContent = data.license_key;
      result.hidden = false;
      form.hidden = true;
      note.hidden = true;
    } catch (err) {
      note.classList.add("error");
      note.textContent = err.message || "Couldn't reach the trial server — try again in a moment.";
      button.disabled = false;
      button.textContent = originalLabel;
    }
  });
})();
