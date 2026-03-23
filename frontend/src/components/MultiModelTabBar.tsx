import { AI_MODEL_OPTIONS } from '../constants/aiModels';
import type { MultiReviseResultItem } from '../types';

interface MultiModelTabBarProps {
  results: MultiReviseResultItem[];
  activeIndex: number;
  onSwitchTab: (index: number) => void;
}

export function MultiModelTabBar({ results, activeIndex, onSwitchTab }: MultiModelTabBarProps) {
  return (
    <div className="flex items-center gap-1 px-4 py-2 bg-amber-50 border-b border-amber-200">
      <span className="text-xs text-amber-600 mr-2">模型对比</span>
      {results.map((item, idx) => {
        const label = AI_MODEL_OPTIONS.find(m => m.value === item.ai_model)?.label || item.ai_model;
        const isActive = idx === activeIndex;
        const isError = !!item.error;

        return (
          <button
            key={item.ai_model}
            onClick={() => onSwitchTab(idx)}
            className={`inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-md font-medium transition-colors ${
              isActive
                ? 'bg-white shadow-sm text-pink-600 border border-pink-200'
                : 'text-gray-500 hover:text-gray-700 hover:bg-white/50'
            }`}
          >
            <span className={`w-1.5 h-1.5 rounded-full ${
              isError ? 'bg-red-400' : 'bg-green-400'
            }`} />
            {label}
          </button>
        );
      })}
    </div>
  );
}
