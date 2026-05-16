"use client";

type BranchSelectorProps = {
  branches: string[];
  value: string;
  label?: string;
  className?: string;
  disabled?: boolean;
  isLoading?: boolean;
  loadingLabel?: string;
  refreshLabel?: string;
  onChange: (branchId: string) => void;
  onRefresh?: () => void;
  optionLabel?: (branchId: string) => string;
};

export function BranchSelector({
  branches,
  value,
  label = "Branch Selector",
  className = "",
  disabled = false,
  isLoading = false,
  loadingLabel = "Loading...",
  refreshLabel = "Refresh branches",
  onChange,
  onRefresh,
  optionLabel,
}: BranchSelectorProps) {
  const isMainBranch = value === "main";
  const accentClasses = isMainBranch
    ? {
        dot: "bg-indigo-500",
        label: "text-indigo-700",
        select:
          "border-indigo-300 bg-indigo-50 text-indigo-950 shadow-sm ring-4 ring-indigo-100/70 focus:border-indigo-500 focus:ring-indigo-200",
        button:
          "border-indigo-200 bg-indigo-50 text-indigo-800 hover:border-indigo-300 hover:bg-indigo-100",
      }
    : {
        dot: "bg-fuchsia-500",
        label: "text-fuchsia-700",
        select:
          "border-fuchsia-300 bg-fuchsia-50 text-fuchsia-950 shadow-sm ring-4 ring-fuchsia-100/70 focus:border-fuchsia-500 focus:ring-fuchsia-200",
        button:
          "border-fuchsia-200 bg-fuchsia-50 text-fuchsia-800 hover:border-fuchsia-300 hover:bg-fuchsia-100",
      };

  return (
    <div
      className={`flex w-full flex-col gap-2 sm:flex-row sm:items-end ${className}`}
    >
      <label className="block min-w-0 flex-1">
        <span
          className={`inline-flex items-center gap-2 text-xs font-semibold ${accentClasses.label}`}
        >
          <span className={`h-2 w-2 rounded-full ${accentClasses.dot}`} />
          {label}
        </span>
        <select
          className={`mt-2 w-full min-w-0 rounded-xl border-2 px-4 py-2.5 text-sm font-semibold outline-none transition focus:ring-4 disabled:cursor-not-allowed disabled:opacity-60 ${accentClasses.select}`}
          disabled={disabled || isLoading}
          onChange={(event) => onChange(event.target.value)}
          value={value}
        >
          {branches.map((branchId) => (
            <option key={branchId} value={branchId}>
              {optionLabel ? optionLabel(branchId) : branchId}
            </option>
          ))}
        </select>
      </label>
      {onRefresh ? (
        <button
          className={`w-full whitespace-nowrap rounded-xl border px-4 py-2.5 text-sm font-semibold shadow-sm transition disabled:cursor-not-allowed disabled:opacity-60 sm:w-auto ${accentClasses.button}`}
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
