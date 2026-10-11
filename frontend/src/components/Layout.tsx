import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { BarChart3, FolderOpen, LogOut, Receipt, UserCircle, Users } from 'lucide-react';
import { useAppStore } from '../stores/appStore';
import { WritingWorkspace } from './WritingWorkspace';
import { UserAuditDashboard } from './audit/UserAuditDashboard';
import { LaoyuUserList } from './audit/LaoyuUserList';
import { CreditOrderList } from './audit/CreditOrderList';
import { getAIModelOptions } from '../constants/aiModels';
import type { User } from '../types';
import { useDraftSaves } from '../hooks/project/draftSaves';

interface LayoutProps {
  user: User;
  onLogout: () => void;
}

export function Layout({ user, onLogout }: LayoutProps) {
  const queryClient = useQueryClient();
  const drafts = useDraftSaves();
  const [navigationError, setNavigationError] = useState('');
  const { defaultAiModel, setDefaultAiModel, closeProject, isAdmin, setIsAdmin } = useAppStore();
  useEffect(() => {
    setIsAdmin(user.role === 'admin');
    if (user.role !== 'admin') setDefaultAiModel('deepseek-flash');
  }, [setDefaultAiModel, setIsAdmin, user.role]);
  const [activeView, setActiveView] = useState<'workspace' | 'audit' | 'laoyu' | 'orders'>('workspace');
  const [auditUserId, setAuditUserId] = useState('');
  const canViewAudit = user.role === 'admin';
  const currentView = canViewAudit ? activeView : 'workspace';

  const leaveWorkspace = async (action: () => void | Promise<void>) => {
    setNavigationError('');
    try {
      await drafts.flush();
      await action();
    } catch {
      setNavigationError('保存未完成，请重试保存后再离开。');
    }
  };

  return (
    <div className="h-screen flex flex-col bg-gradient-to-br from-pink-50 to-purple-50">
      {/* Header */}
      <header className="h-14 bg-white border-b border-pink-100 flex items-center justify-between px-4 flex-shrink-0">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => leaveWorkspace(() => {
              setActiveView('workspace');
              closeProject();
              void queryClient.invalidateQueries({ queryKey: ['projects'] });
            })}
            className="flex items-center gap-3 rounded-lg px-1.5 py-1 transition-colors hover:bg-pink-50 focus:outline-none focus:ring-2 focus:ring-pink-500"
            aria-label="返回我的项目"
            title="返回我的项目"
          >
            <img src="/logo.png" alt="" className="h-8 w-8 object-contain" />
            <span className="text-xl font-bold bg-gradient-to-r from-pink-500 to-purple-500 bg-clip-text text-transparent">
              Pawtale
            </span>
          </button>
          {canViewAudit && (
            <div className="flex items-center gap-1 rounded-lg border border-pink-100 bg-pink-50/60 p-1">
              <button
                type="button"
                onClick={() => setActiveView('workspace')}
                className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm transition-colors ${
                  currentView === 'workspace'
                    ? 'bg-white text-pink-600 shadow-sm'
                    : 'text-gray-500 hover:bg-white/70 hover:text-gray-700'
                }`}
                title="项目工作区"
              >
                <FolderOpen size={15} />
                <span className="hidden md:inline">项目</span>
              </button>
              <button
                type="button"
                onClick={() => leaveWorkspace(() => {
                  closeProject();
                  setAuditUserId('');
                  setActiveView('audit');
                })}
                className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm transition-colors ${
                  currentView === 'audit'
                    ? 'bg-white text-pink-600 shadow-sm'
                    : 'text-gray-500 hover:bg-white/70 hover:text-gray-700'
                }`}
                title="用户审计"
              >
                <BarChart3 size={15} />
                <span className="hidden md:inline">审计</span>
              </button>
              <button
                type="button"
                onClick={() => leaveWorkspace(() => {
                  closeProject();
                  setActiveView('laoyu');
                })}
                className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm transition-colors ${
                  currentView === 'laoyu'
                    ? 'bg-white text-pink-600 shadow-sm'
                    : 'text-gray-500 hover:bg-white/70 hover:text-gray-700'
                }`}
                title="老余口述用户"
              >
                <Users size={15} />
                <span className="hidden md:inline">老余用户</span>
              </button>
              <button
                type="button"
                onClick={() => leaveWorkspace(() => {
                  closeProject();
                  setActiveView('orders');
                })}
                className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm transition-colors ${
                  currentView === 'orders'
                    ? 'bg-white text-pink-600 shadow-sm'
                    : 'text-gray-500 hover:bg-white/70 hover:text-gray-700'
                }`}
                title="充值订单"
              >
                <Receipt size={15} />
                <span className="hidden md:inline">充值订单</span>
              </button>
            </div>
          )}
        </div>

        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-500">默认模型</span>
            <select
              value={defaultAiModel}
              onChange={(e) => setDefaultAiModel(e.target.value as typeof defaultAiModel)}
              className="text-sm px-2 py-1.5 border border-gray-200 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-pink-500"
            >
              {getAIModelOptions(isAdmin).map((option) => (
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
            onClick={() => leaveWorkspace(onLogout)}
            className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 px-2.5 py-1.5 text-sm text-gray-600 transition-colors hover:bg-gray-50"
          >
            <LogOut size={15} />
            <span className="hidden md:inline">退出</span>
          </button>
        </div>
      </header>
      {navigationError && <p role="alert" className="px-4 py-2 text-sm text-red-700">{navigationError}</p>}

      {/* Main content */}
      <main className="flex-1 flex overflow-hidden">
        {currentView === 'audit' && (
          <UserAuditDashboard
            key={auditUserId}
            initialUserId={auditUserId}
            initialPlatform={auditUserId ? 'miniprogram' : undefined}
          />
        )}
        {currentView === 'laoyu' && (
          <LaoyuUserList
            onOpenAudit={(userId) => {
              setAuditUserId(userId);
              setActiveView('audit');
            }}
          />
        )}
        {currentView === 'orders' && (
          <CreditOrderList
            onOpenAudit={(userId) => {
              setAuditUserId(userId);
              setActiveView('audit');
            }}
          />
        )}
        {currentView === 'workspace' && <WritingWorkspace />}
      </main>
    </div>
  );
}
