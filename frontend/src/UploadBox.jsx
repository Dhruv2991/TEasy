import { useState, useCallback, useRef } from "react";
import { Icon } from "./icons.jsx";

function formatFileSize(bytes) {
  if (!bytes && bytes !== 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Smart parsing of wait time in seconds from 429 rate limit error messages.
 * Matches formats such as:
 * - "Please try again in 18.23s." -> 18.23 + buffer
 * - "Please try again in 1m 15s." -> 75 + buffer
 * - "retry after 30 seconds" -> 30 + buffer
 * - "rate_limit_exceeded" or "429" without explicit time -> defaultDelaySec + buffer
 */
function parseRateLimitDelay(errorMessage, defaultDelaySec = 25, safetyBufferSec = 3) {
  if (!errorMessage) return null;
  const msg = String(errorMessage).toLowerCase();

  const isRateLimit =
    msg.includes("429") ||
    msg.includes("rate limit") ||
    msg.includes("rate_limit") ||
    msg.includes("tpm") ||
    msg.includes("tokens per minute") ||
    msg.includes("try again in");

  if (!isRateLimit) return null;

  // 1. Match "try again in Xm Ys" or "Xm"
  const minSecMatch = msg.match(
    /try again in (?:(\d+)\s*m(?:in(?:ute)?s?)?)?\s*(?:(\d+(?:\.\d+)?)\s*s(?:ec(?:ond)?s?)?)?/i
  );
  if (minSecMatch && (minSecMatch[1] || minSecMatch[2])) {
    const mins = parseFloat(minSecMatch[1] || "0");
    const secs = parseFloat(minSecMatch[2] || "0");
    const total = mins * 60 + secs;
    if (total > 0) {
      return Math.ceil(total + safetyBufferSec);
    }
  }

  // 2. Match "try again in Xs" or "retry after Xs"
  const secMatch = msg.match(
    /(?:try again in|retry after|wait)\s*(\d+(?:\.\d+)?)\s*(?:s|sec|seconds)?/i
  );
  if (secMatch && secMatch[1]) {
    const val = parseFloat(secMatch[1]);
    if (!isNaN(val) && val > 0) {
      return Math.ceil(val + safetyBufferSec);
    }
  }

  // 3. Match any duration like "18.23s" following "try again"
  const simpleMatch = msg.match(/try again.*?(\d+(?:\.\d+)?)\s*s\b/i);
  if (simpleMatch && simpleMatch[1]) {
    const val = parseFloat(simpleMatch[1]);
    if (!isNaN(val) && val > 0) {
      return Math.ceil(val + safetyBufferSec);
    }
  }

  // Fallback for general 429 without explicit time
  return defaultDelaySec + safetyBufferSec;
}

export default function UploadBox({
  onUpload,
  accept = "image/*",
  label = "Drop a bill photo here, or choose a file",
  multiple = false,
  throttleDelayMs = 25000, // 25s default to respect Groq free-tier 7,000 TPM limit
}) {
  const [uploading, setUploading] = useState(false);
  const [queue, setQueue] = useState([]); // [{ id, file, name, size, status: "pending"|"throttling"|"processing"|"done"|"error", error? }]
  const [currentIndex, setCurrentIndex] = useState(null);
  const [isThrottling, setIsThrottling] = useState(false);
  const [throttleCountdown, setThrottleCountdown] = useState(0);
  const [isRateLimitPause, setIsRateLimitPause] = useState(false);

  const fileInputRef = useRef(null);

  const runQueue = useCallback(
    async (itemsToProcess) => {
      if (!itemsToProcess.length) return;

      setUploading(true);
      const totalInBatch = itemsToProcess.length;

      for (let i = 0; i < itemsToProcess.length; i++) {
        const item = itemsToProcess[i];
        setCurrentIndex(i);

        // Controlled throttle delay between requests (after the first file)
        // to protect against Groq Tokens-Per-Minute (7,000 TPM) rolling limit.
        if (i > 0 && throttleDelayMs > 0) {
          setIsThrottling(true);
          setIsRateLimitPause(false);
          setQueue((q) =>
            q.map((qItem) =>
              qItem.id === item.id ? { ...qItem, status: "throttling" } : qItem
            )
          );

          const totalSeconds = Math.ceil(throttleDelayMs / 1000);
          for (let sec = totalSeconds; sec > 0; sec--) {
            setThrottleCountdown(sec);
            await new Promise((r) => setTimeout(r, 1000));
          }
          setThrottleCountdown(0);
          setIsThrottling(false);
        }

        // Processing attempt loop with smart HTTP 429 rate-limit backoff
        let attempts = 0;
        const MAX_RATE_LIMIT_RETRIES = 2; // Up to 2 retries (total 3 attempts per bill)
        let fileSuccess = false;

        while (attempts <= MAX_RATE_LIMIT_RETRIES && !fileSuccess) {
          attempts++;

          // Mark current file as actively processing
          setQueue((q) =>
            q.map((qItem) =>
              qItem.id === item.id
                ? { ...qItem, status: "processing", error: null }
                : qItem
            )
          );

          try {
            await onUpload(item.file);
            fileSuccess = true;
            setQueue((q) =>
              q.map((qItem) =>
                qItem.id === item.id ? { ...qItem, status: "done", error: null } : qItem
              )
            );
          } catch (err) {
            const errMsg = err?.message || "Upload or processing failed";
            const rateLimitDelaySec = parseRateLimitDelay(errMsg, 25, 3);

            if (rateLimitDelaySec && attempts <= MAX_RATE_LIMIT_RETRIES) {
              console.warn(
                `[Batch Upload] Groq 429 rate limit hit on bill ${i + 1}/${totalInBatch} ('${item.name}'). ` +
                `Pausing queue for ${rateLimitDelaySec}s before retry attempt ${attempts}/${MAX_RATE_LIMIT_RETRIES}. Error:`,
                errMsg
              );

              setIsThrottling(true);
              setIsRateLimitPause(true);

              setQueue((q) =>
                q.map((qItem) =>
                  qItem.id === item.id
                    ? {
                        ...qItem,
                        status: "throttling",
                        error: `Rate limit hit (429) — auto-retrying in ${rateLimitDelaySec}s (attempt ${attempts}/${MAX_RATE_LIMIT_RETRIES})…`,
                      }
                    : qItem
                )
              );

              // Live countdown for the 429 backoff pause
              for (let sec = rateLimitDelaySec; sec > 0; sec--) {
                setThrottleCountdown(sec);
                await new Promise((r) => setTimeout(r, 1000));
              }
              setThrottleCountdown(0);
              setIsThrottling(false);
              setIsRateLimitPause(false);
            } else {
              // Non-rate-limit error or max retries exceeded
              console.error(
                `[Batch Upload] Error processing bill ${i + 1}/${totalInBatch} ('${item.name}'):`,
                err
              );
              setQueue((q) =>
                q.map((qItem) =>
                  qItem.id === item.id ? { ...qItem, status: "error", error: errMsg } : qItem
                )
              );
              break;
            }
          }
        }
      }

      setUploading(false);
      setCurrentIndex(null);
      setIsThrottling(false);
      setIsRateLimitPause(false);
      setThrottleCountdown(0);
    },
    [onUpload, throttleDelayMs]
  );

  const handleFiles = useCallback(
    async (fileList) => {
      const files = Array.from(fileList || []).filter(Boolean);
      if (!files.length) return;

      const newQueue = files.map((f, i) => ({
        id: `${f.name}-${Date.now()}-${i}`,
        file: f,
        name: f.name,
        size: f.size,
        status: "pending",
        error: null,
      }));

      setQueue(newQueue);
      await runQueue(newQueue);
    },
    [runQueue]
  );

  const handleRetryFailed = useCallback(async () => {
    const failedItems = queue.filter((q) => q.status === "error");
    if (!failedItems.length) return;

    // Reset status of failed items to pending
    const resetFailed = failedItems.map((item) => ({
      ...item,
      status: "pending",
      error: null,
    }));

    setQueue((q) =>
      q.map((item) => {
        const matchingFailed = resetFailed.find((f) => f.id === item.id);
        return matchingFailed ? { ...item, status: "pending", error: null } : item;
      })
    );

    await runQueue(resetFailed);
  }, [queue, runQueue]);

  const handleClearQueue = useCallback(() => {
    setQueue([]);
    setCurrentIndex(null);
    setIsThrottling(false);
    setIsRateLimitPause(false);
    setThrottleCountdown(0);
  }, []);

  const doneCount = queue.filter((q) => q.status === "done").length;
  const errorCount = queue.filter((q) => q.status === "error").length;
  const completedCount = doneCount + errorCount;
  const progressPercent = queue.length > 0 ? Math.round((completedCount / queue.length) * 100) : 0;

  // Real-time status feedback text
  let statusMessage = label;
  if (uploading && currentIndex !== null && queue[currentIndex]) {
    const currentFile = queue[currentIndex];
    if (isThrottling) {
      if (isRateLimitPause) {
        statusMessage = `Groq TPM rate limit hit (429). Pausing queue for ${throttleCountdown}s before retrying file ${currentIndex + 1} of ${queue.length}…`;
      } else {
        statusMessage = `Rate-limit cooldown: waiting ${throttleCountdown}s before file ${currentIndex + 1} of ${queue.length} to respect Groq rolling token limit…`;
      }
    } else {
      statusMessage = `Processing file ${currentIndex + 1} of ${queue.length}: ${currentFile.name}…`;
    }
  } else if (!uploading && queue.length > 0) {
    statusMessage = `${doneCount} of ${queue.length} uploaded successfully${errorCount > 0 ? `, ${errorCount} failed` : ""}.`;
  }

  return (
    <div>
      <div
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          if (uploading) return;
          handleFiles(multiple ? e.dataTransfer.files : [e.dataTransfer.files?.[0]]);
        }}
        className={`border-2 border-dashed rounded-xl p-6 text-center transition-all ${
          uploading
            ? isRateLimitPause
              ? "border-amber-400 bg-amber-50/50"
              : "border-blue-400 bg-blue-50/40"
            : "border-slate-300 bg-slate-50 hover:bg-slate-100 hover:border-slate-400"
        }`}
      >
        <div className="flex justify-center mb-2">
          {uploading ? (
            isThrottling ? (
              <div
                className={`w-9 h-9 rounded-full flex items-center justify-center animate-pulse ${
                  isRateLimitPause
                    ? "bg-amber-100 text-amber-700 ring-2 ring-amber-300"
                    : "bg-amber-100 text-amber-600"
                }`}
              >
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              </div>
            ) : (
              <div className="w-9 h-9 rounded-full bg-blue-100 text-blue-600 flex items-center justify-center">
                <svg className="animate-spin w-5 h-5" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                </svg>
              </div>
            )
          ) : (
            <Icon.Upload className="text-slate-400" width={24} height={24} />
          )}
        </div>

        <p className="text-sm font-medium text-slate-800 mb-1">
          {statusMessage}
        </p>

        {uploading && queue.length > 1 && (
          <div className="mt-2 mb-3 max-w-sm mx-auto">
            <div className="flex justify-between text-[11px] text-slate-500 mb-1">
              <span>
                {isRateLimitPause
                  ? "Groq 429 Backoff Pause"
                  : isThrottling
                  ? "Groq Rolling 7,000 TPM Cooldown"
                  : "Batch Progress"}
              </span>
              <span>{completedCount} of {queue.length} ({progressPercent}%)</span>
            </div>
            <div className="w-full bg-slate-200 rounded-full h-1.5 overflow-hidden">
              <div
                className={`h-full transition-all duration-300 ${
                  isRateLimitPause
                    ? "bg-amber-500 animate-pulse"
                    : isThrottling
                    ? "bg-amber-400 animate-pulse"
                    : "bg-blue-600"
                }`}
                style={{ width: `${progressPercent}%` }}
              />
            </div>
            {isThrottling && (
              <p className="text-[10px] text-slate-400 mt-1">
                {isRateLimitPause
                  ? `Extracting wait time from Groq error… auto-retrying in ${throttleCountdown}s.`
                  : `Waiting ${throttleCountdown}s between files to replenish Groq's 7,000 TPM token window.`}
              </p>
            )}
          </div>
        )}

        <div className="mt-3">
          <label
            className={`inline-flex items-center gap-2 text-sm px-4 py-2 rounded-lg font-medium transition-colors ${
              uploading
                ? "bg-slate-300 text-slate-500 cursor-not-allowed"
                : "bg-slate-900 text-white cursor-pointer hover:bg-slate-800"
            }`}
          >
            {multiple ? "Choose files" : "Choose file"}
            <input
              ref={fileInputRef}
              type="file"
              accept={accept}
              multiple={multiple}
              disabled={uploading}
              className="hidden"
              onChange={(e) => {
                handleFiles(e.target.files);
                e.target.value = "";
              }}
            />
          </label>
        </div>

        {multiple && !uploading && (
          <p className="text-xs text-slate-400 mt-2">
            Select or drag multiple bills at once. They will be processed sequentially with a 25s throttle delay to respect Groq's 7,000 TPM limit.
          </p>
        )}
      </div>

      {queue.length > 0 && (
        <div className="mt-3 border border-slate-200 rounded-xl bg-white p-3 shadow-sm">
          <div className="flex items-center justify-between pb-2 mb-2 border-b border-slate-100 text-xs">
            <span className="font-semibold text-slate-700">
              Batch Queue ({completedCount}/{queue.length} processed)
            </span>
            <div className="flex items-center gap-2">
              {!uploading && errorCount > 0 && (
                <button
                  type="button"
                  onClick={handleRetryFailed}
                  className="text-xs text-blue-600 hover:text-blue-800 font-medium inline-flex items-center gap-1"
                >
                  <Icon.Refresh width={12} height={12} />
                  Retry Failed ({errorCount})
                </button>
              )}
              {!uploading && (
                <button
                  type="button"
                  onClick={handleClearQueue}
                  className="text-xs text-slate-400 hover:text-slate-600 font-medium"
                >
                  Clear
                </button>
              )}
            </div>
          </div>

          <div className="space-y-1.5 max-h-56 overflow-y-auto pr-1">
            {queue.map((item, i) => (
              <div
                key={item.id || i}
                className={`p-2 rounded-lg border text-xs transition-colors ${
                  item.status === "processing"
                    ? "bg-blue-50/60 border-blue-200"
                    : item.status === "throttling"
                    ? "bg-amber-50/50 border-amber-200"
                    : item.status === "error"
                    ? "bg-rose-50/30 border-rose-200"
                    : "bg-slate-50/50 border-slate-200"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-[10px] text-slate-400 font-mono w-4 text-right">
                      {i + 1}.
                    </span>
                    <span className="truncate font-medium text-slate-700" title={item.name}>
                      {item.name}
                    </span>
                    {item.size && (
                      <span className="text-[10px] text-slate-400 flex-shrink-0">
                        ({formatFileSize(item.size)})
                      </span>
                    )}
                  </div>

                  <div className="flex-shrink-0">
                    {item.status === "pending" && (
                      <span className="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium bg-slate-100 text-slate-500">
                        Queued
                      </span>
                    )}
                    {item.status === "throttling" && (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium bg-amber-100 text-amber-800 animate-pulse">
                        <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                        </svg>
                        {isRateLimitPause ? `Rate limited (${throttleCountdown}s)` : `Cooldown (${throttleCountdown}s)`}
                      </span>
                    )}
                    {item.status === "processing" && (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium bg-blue-100 text-blue-800">
                        <svg className="animate-spin w-3 h-3 text-blue-600" fill="none" viewBox="0 0 24 24">
                          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                        </svg>
                        Processing…
                      </span>
                    )}
                    {item.status === "done" && (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium bg-emerald-100 text-emerald-700">
                        <Icon.Check width={12} height={12} />
                        Done
                      </span>
                    )}
                    {item.status === "error" && (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium bg-rose-100 text-rose-700" title={item.error}>
                        <Icon.Alert width={12} height={12} />
                        Failed
                      </span>
                    )}
                  </div>
                </div>

                {item.error && (
                  <div
                    className={`mt-1 text-[11px] rounded px-2 py-1 ${
                      item.status === "throttling"
                        ? "text-amber-800 bg-amber-100/70 border border-amber-200"
                        : "text-rose-600 bg-rose-100/60"
                    }`}
                  >
                    <span className="font-semibold">
                      {item.status === "throttling" ? "Notice:" : "Error:"}
                    </span>{" "}
                    {item.error}
                  </div>
                )}
              </div>
            ))}
          </div>

          {!uploading && (doneCount > 0 || errorCount > 0) && (
            <div className="mt-2 pt-2 border-t border-slate-100 flex items-center justify-between text-xs text-slate-500">
              <span>
                Summary: {doneCount} completed{errorCount > 0 ? `, ${errorCount} failed` : ""}.
              </span>
              {errorCount > 0 && (
                <span className="text-amber-600 font-medium">
                  Use "Retry Failed" above to re-attempt failed items.
                </span>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
