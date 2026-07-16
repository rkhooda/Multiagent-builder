export default function RegeneratingOverlay({ label = 'Regenerating with your feedback…' }) {
  return (
    <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-3 rounded bg-white/80 backdrop-blur-sm">
      <div className="h-6 w-6 animate-spin rounded-full border-2 border-gray-300 border-t-blue-500" />
      <p className="text-sm font-medium text-blue-600 animate-pulse">{label}</p>
      <div className="w-2/3 space-y-2">
        <div className="h-3 rounded bg-gray-200 animate-pulse" />
        <div className="h-3 w-5/6 rounded bg-gray-200 animate-pulse" />
        <div className="h-3 w-4/6 rounded bg-gray-200 animate-pulse" />
      </div>
    </div>
  )
}
