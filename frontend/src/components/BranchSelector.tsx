"use client";

type BranchSelectorProps = {
  branches: string[];
  value: string;
  label?: string;
  disabled?: boolean;
  isLoading?: boolean;
  loadingLabel?: string;
  refreshLabel?: string;
  onChange: (branchId: string) => void;
  onRefresh?: () => void;
};

export function BranchSelector({
  branches,
  value,
  label = "Branch Selector",
  disabled = false,
  isLoading = false,
  loadingLabel = "Loading...",
  refreshLabel = "Refresh branches",
  onChange,
  onRefresh,
}: BranchSelectorProps) {
  const isMainBranch = value === "main";

  return (
    <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
      <label className="block">
        <span
          className={`text-xs font-semibold uppercase tracking-wide ${
            isMainBranch ? "text-gray-500" : "text-purple-600"
          }`}
        >
          {label}
        </span>
        <select
          className={`mt-2 w-full rounded-full border px-4 py-2 text-sm font-medium outline-none transition sm:w-auto ${
            isMainBranch
              ? "border-gray-200 bg-white text-gray-900 focus:border-indigo-400 focus:ring-4 focus:ring-indigo-100"
              : "border-purple-200 bg-white text-purple-950 focus:border-purple-400 focus:ring-4 focus:ring-purple-100"
          }`}
          disabled={disabled || isLoading}
          onChange={(event) => onChange(event.target.value)}
          value={value}
        >
          {branches.map((branchId) => (
            <option key={branchId} value={branchId}>
              {branchId}
            </option>
          ))}
        </select>
      </label>
      {onRefresh ? (
        <button
          className="rounded-full border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm transition hover:border-gray-300 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-60"
          disabled={disabled || isLoading}
          onClick={onRefresh}
          type="button"
        >
          {isLoading ? loadingLabel : refreshLabel}
        </button>
      ) : null}
    </div>
  );
}
