import { useState, useCallback } from "react";
import { Icon } from "./icons.jsx";

export default function UploadBox({
  onUpload,
  accept = "image/*",
  label = "Drop a bill photo here, or choose a file",
  multiple = false,
}) {
  const [uploading, setUploading] = useState(false);
  const [queue, setQueue] = useState([]); // [{name, status: "pending"|"done"|"error", error?}]

  const handleFiles = useCallback(
    async (fileList) => {
      const files = Array.from(fileList || []).filter(Boolean);
      if (!files.length) return;

      setUploading(true);
      setQueue(files.map((f) => ({ name: f.name, status: "pending" })));

      // Uploaded one at a time, reusing the exact same single-file upload
      // call as before — this is just a loop around it, nothing about the
      // upload itself changes, so a batch is exactly as safe as one file.
      for (let i = 0; i < files.length; i++) {
        try {
          await onUpload(files[i]);
          setQueue((q) => q.map((item, idx) => (idx === i ? { ...item, status: "done" } : item)));
        } catch (e) {
          setQueue((q) => q.map((item, idx) => (idx === i ? { ...item, status: "error", error: e.message } : item)));
        }
      }
      setUploading(false);
    },
    [onUpload]
  );

  const doneCount = queue.filter((q) => q.status === "done").length;
  const errorCount = queue.filter((q) => q.status === "error").length;

  return (
    <div>
      <div
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          handleFiles(multiple ? e.dataTransfer.files : [e.dataTransfer.files?.[0]]);
        }}
        className="border-2 border-dashed border-slate-300 rounded-xl p-6 text-center bg-slate-50 hover:bg-slate-100 transition-colors"
      >
        <Icon.Upload className="mx-auto text-slate-400 mb-2" width={24} height={24} />
        <p className="text-sm text-slate-500 mb-3">
          {uploading
            ? `Uploading & processing… (${doneCount + errorCount}/${queue.length})`
            : label}
        </p>
        <label className="inline-flex items-center gap-2 bg-slate-900 text-white text-sm px-4 py-2 rounded-lg cursor-pointer hover:bg-slate-800">
          {multiple ? "Choose files" : "Choose file"}
          <input
            type="file"
            accept={accept}
            multiple={multiple}
            disabled={uploading}
            className="hidden"
            onChange={(e) => handleFiles(e.target.files)}
          />
        </label>
        {multiple && <p className="text-xs text-slate-400 mt-2">You can select or drop several photos at once.</p>}
      </div>

      {queue.length > 0 && (
        <div className="mt-3 space-y-1 max-h-48 overflow-y-auto">
          {queue.map((item, i) => (
            <div key={i} className="flex items-center justify-between text-xs bg-white border border-slate-200 rounded-lg px-3 py-1.5">
              <span className="truncate max-w-[70%] text-slate-600">{item.name}</span>
              {item.status === "pending" && <span className="text-slate-400">Uploading…</span>}
              {item.status === "done" && <span className="text-emerald-600">Done</span>}
              {item.status === "error" && (
                <span className="text-rose-600" title={item.error}>Failed</span>
              )}
            </div>
          ))}
          {!uploading && (errorCount > 0 || doneCount > 0) && (
            <p className="text-xs text-slate-400 pt-1">
              {doneCount} uploaded{errorCount > 0 ? `, ${errorCount} failed` : ""}.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

