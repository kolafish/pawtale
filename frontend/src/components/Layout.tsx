import { useQueryClient } from '@tanstack/react-query';
import { LogOut, UserCircle } from 'lucide-react';
import { useAppStore } from '../stores/appStore';
import { WritingWorkspace } from './WritingWorkspace';
import { AI_MODEL_OPTIONS } from '../constants/aiModels';
import type { User } from '../types';

interface LayoutProps {
  user: User;
  onLogout: () => void;
}

export function Layout({ user, onLogout }: LayoutProps) {
  const queryClient = useQueryClient();
  const { defaultAiModel, setDefaultAiModel, closeProject } = useAppStore();

  const handleLogoClick = () => {
    closeProject();
    queryClient.invalidateQueries({ queryKey: ['projects'] });
  };

  return (
    <div className="h-screen flex flex-col bg-gradient-to-br from-pink-50 to-purple-50">
      {/* Header */}
      <header className="h-14 bg-white border-b border-pink-100 flex items-center justify-between px-4 flex-shrink-0">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={handleLogoClick}
            className="flex items-center gap-3 rounded-lg px-1.5 py-1 transition-colors hover:bg-pink-50 focus:outline-none focus:ring-2 focus:ring-pink-500"
            aria-label="返回我的项目"
            title="返回我的项目"
          >
            <img src="/logo.png" alt="" className="h-8 w-8 object-contain" />
            <span className="text-xl font-bold bg-gradient-to-r from-pink-500 to-purple-500 bg-clip-text text-transparent">
              Pawtale
            </span>
          </button>
        </div>

        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-500">默认模型</span>
            <select
              value={defaultAiModel}
              onChange={(e) => setDefaultAiModel(e.target.value as typeof defaultAiModel)}
              className="text-sm px-2 py-1.5 border border-gray-200 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-pink-500"
            >
              {AI_MODEL_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
          <div className="hidden items-center gap-1.5 text-sm text-gray-600 md:flex">
            <UserCircle size={16} />
            <span className="max-w-28 truncate">{user.display_name}</span>
          </div>
          <button
            onClick={onLogout}
            className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 px-2.5 py-1.5 text-sm text-gray-600 transition-colors hover:bg-gray-50"
          >
            <LogOut size={15} />
            <span className="hidden md:inline">退出</span>
          </button>
        </div>
      </header>

      {/* Main content */}
      <main className="flex-1 flex overflow-hidden">
        <WritingWorkspace />
      </main>
    </div>
  );
}
