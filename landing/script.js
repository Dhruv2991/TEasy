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
      const base = window.TEASY_CONFIG && window.TEASY_CONFIG.LICENSE_SERVICE_URL;
      if (!base) {
        throw new Error("Trial signup isn't configured yet — LICENSE_SERVICE_URL is missing.");
      }
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

// -------- pricing card subscribe (skip trial, pay now) --------
(function () {
  const forms = document.querySelectorAll(".subscribe-form");
  if (!forms.length) return;

  forms.forEach((form) => {
    const plan = form.dataset.plan; // "monthly" or "yearly"
    const tier = form.dataset.tier || "silver"; // "silver" or "gold"
    const input = form.querySelector("input[type=email]");
    const button = form.querySelector("button");
    const note = form.parentElement.querySelector(".subscribe-note");

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const email = input.value.trim();
      if (!email) return;

      const originalLabel = button.textContent;
      button.disabled = true;
      button.textContent = "Redirecting to payment…";
      if (note) {
        note.classList.remove("error");
        note.textContent = "Setting up your subscription…";
      }

      try {
        const base = window.TEASY_CONFIG && window.TEASY_CONFIG.LICENSE_SERVICE_URL;
        if (!base) {
          throw new Error("Checkout isn't configured yet — LICENSE_SERVICE_URL is missing.");
        }

        // Step 1: get-or-create a license for this email (idempotent — if
        // this email already has one, the same license_key comes back and
        // no new trial is granted, per the one-trial-per-email rule).
        const trialRes = await fetch(`${base}/trial/start`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email }),
        });
        if (!trialRes.ok) {
          const body = await trialRes.json().catch(() => ({}));
          throw new Error(body.detail || "Couldn't set up your account.");
        }
        const trialData = await trialRes.json();

        // Step 2: create a Razorpay subscription for the chosen plan
        // against that license, then send the browser straight to the
        // real Razorpay checkout page.
        const subRes = await fetch(`${base}/billing/create-subscription`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ license_key: trialData.license_key, plan, tier }),
        });
        if (!subRes.ok) {
          const body = await subRes.json().catch(() => ({}));
          throw new Error(body.detail || "Couldn't start checkout.");
        }
        const subData = await subRes.json();
        if (!subData.short_url) {
          throw new Error("Checkout link wasn't returned — try again in a moment.");
        }

        window.location.href = subData.short_url;
      } catch (err) {
        if (note) {
          note.classList.add("error");
          note.textContent = err.message || "Something went wrong — try again in a moment.";
        }
        button.disabled = false;
        button.textContent = originalLabel;
      }
    });
  });
})();
