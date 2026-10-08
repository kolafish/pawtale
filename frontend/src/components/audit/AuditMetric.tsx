import type { ReactNode } from 'react';

interface MetricProps {
  icon: ReactNode;
  label: string;
  value: string;
  subLabel?: string;
}

export function Metric({ icon, label, value, subLabel }: MetricProps) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4">
      <div className="mb-3 flex items-center justify-between text-gray-500">
        <span className="text-sm">{label}</span>
        {icon}
      </div>
      <div className="text-2xl font-semibold text-gray-900">{value}</div>
      {subLabel && <div className="mt-1 text-xs text-gray-500">{subLabel}</div>}
    </div>
  );
}
