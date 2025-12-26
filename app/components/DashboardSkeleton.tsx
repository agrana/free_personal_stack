export default function DashboardSkeleton() {
  return (
    <div className="max-w-6xl mx-auto py-8 px-4 sm:px-6 lg:px-8">
      <div className="mb-8">
        <div className="h-9 w-96 bg-gray-200 animate-pulse rounded mb-2" />
        <div className="h-5 w-64 bg-gray-200 animate-pulse rounded" />
      </div>

      <div className="mb-6 p-4 bg-gray-50 rounded-lg border border-gray-200">
        <div className="h-6 w-32 bg-gray-200 animate-pulse rounded mb-2" />
        <div className="h-4 w-48 bg-gray-200 animate-pulse rounded" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {[1, 2, 3, 4].map((i) => (
          <div
            key={i}
            className="bg-white rounded-lg shadow-sm border p-6"
          >
            <div className="h-6 w-40 bg-gray-200 animate-pulse rounded mb-4" />
            <div className="space-y-3">
              <div className="h-4 w-full bg-gray-200 animate-pulse rounded" />
              <div className="h-4 w-3/4 bg-gray-200 animate-pulse rounded" />
              <div className="h-4 w-1/2 bg-gray-200 animate-pulse rounded" />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

