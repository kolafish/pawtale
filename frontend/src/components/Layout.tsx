import { useState } from 'react';
import { LogOut, MapPin, UserCircle, Users, BookOpen } from 'lucide-react';
import { useAppStore } from '../stores/appStore';
import { WritingWorkspace } from './WritingWorkspace';
import { CharacterPanel } from './CharacterPanel';
import { ScenePanel } from './ScenePanel';
import { AI_MODEL_OPTIONS } from '../constants/aiModels';
import type { User } from '../types';

type AppView = 'writing' | 'library';
type LibraryTab = 'characters' | 'scenes';

interface LayoutProps {
  user: User;
  onLogout: () => void;
}

export function Layout({ user, onLogout }: LayoutProps) {
  const { defaultAiModel, setDefaultAiModel } = useAppStore();
  const [view, setView] = useState<AppView>('writing');
  const [libraryTab, setLibraryTab] = useState<LibraryTab>('characters');

  return (
    <div className="h-screen flex flex-col bg-gradient-to-br from-pink-50 to-purple-50">
      {/* Header */}
      <header className="h-14 bg-white border-b border-pink-100 flex items-center justify-between px-4 flex-shrink-0">
        <div className="flex items-center gap-3">
          <img src="/logo.png" alt="Pawtale" className="h-8 w-8 object-contain" />
          <h1 className="text-xl font-bold bg-gradient-to-r from-pink-500 to-purple-500 bg-clip-text text-transparent">
            Pawtale
          </h1>

          {/* View toggle */}
          <div className="flex items-center gap-1 bg-gray-100 rounded-lg p-1 ml-2">
            <button
              onClick={() => setView('writing')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                view === 'writing' ? 'bg-white shadow-sm text-pink-600' : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              <BookOpen size={14} />
              写作
            </button>
            <button
              onClick={() => setView('library')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                view === 'library' ? 'bg-white shadow-sm text-pink-600' : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              <Users size={14} />
              全局资料库
            </button>
          </div>
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
        {view === 'writing' ? (
          <WritingWorkspace />
        ) : (
          <div className="flex-1 flex flex-col bg-white">
            {/* Library tabs */}
            <div className="flex border-b border-pink-100">
              <button
                onClick={() => setLibraryTab('characters')}
                className={`flex-1 flex items-center justify-center gap-2 py-3 text-sm font-medium transition-colors ${
                  libraryTab === 'characters'
                    ? 'text-pink-600 border-b-2 border-pink-500'
                    : 'text-gray-500 hover:text-gray-700'
                }`}
              >
                <Users size={16} />
                角色库
              </button>
              <button
                onClick={() => setLibraryTab('scenes')}
                className={`flex-1 flex items-center justify-center gap-2 py-3 text-sm font-medium transition-colors ${
                  libraryTab === 'scenes'
                    ? 'text-pink-600 border-b-2 border-pink-500'
                    : 'text-gray-500 hover:text-gray-700'
                }`}
              >
                <MapPin size={16} />
                场景库
              </button>
            </div>

            {/* Library content */}
            <div className="flex-1 overflow-hidden">
              {libraryTab === 'characters' && <CharacterPanel />}
              {libraryTab === 'scenes' && <ScenePanel />}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
