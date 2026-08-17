export default function ComingSoonPage({ title, description }) {
  return (
    <div className="p-6">
      <div className="bg-white rounded-xl border border-slate-200 p-12 text-center">
        <h2 className="font-semibold text-slate-900 text-lg mb-1">{title}</h2>
        <p className="text-sm text-slate-500 max-w-md mx-auto">{description || "This section isn't built yet."}</p>
      </div>
    </div>
  );
}
