import { useState, useCallback } from "react";
import { Icon } from "./icons.jsx";

export default function UploadBox({ onUpload, accept = "image/*", label = "Drop a bill photo here, or choose a file" }) {
  const [uploading, setUploading] = useState(false);

  const handleFile = useCallback(
    async (file) => {
      if (!file) return;
      setUploading(true);
      try {
        await onUpload(file);
      } catch (e) {
        alert("Upload failed: " + e.message);
      } finally {
        setUploading(false);
      }
    },
    [onUpload]
  );

  return (
    <div
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        handleFile(e.dataTransfer.files?.[0]);
      }}
      className="border-2 border-dashed border-slate-300 rounded-xl p-6 text-center bg-slate-50 hover:bg-slate-100 transition-colors"
    >
      <Icon.Upload className="mx-auto text-slate-400 mb-2" width={24} height={24} />
      <p className="text-sm text-slate-500 mb-3">{uploading ? "Uploading & processing…" : label}</p>
      <label className="inline-flex items-center gap-2 bg-slate-900 text-white text-sm px-4 py-2 rounded-lg cursor-pointer hover:bg-slate-800">
        Choose file
        <input
          type="file"
          accept={accept}
          disabled={uploading}
          className="hidden"
          onChange={(e) => handleFile(e.target.files?.[0])}
        />
      </label>
    </div>
  );
}
