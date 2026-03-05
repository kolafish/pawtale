import { Users, MapPin, BookOpen, PanelLeft, PanelRight, Columns2, FileText } from 'lucide-react';
import { useAppStore } from '../stores/appStore';
import { CharacterPanel } from './CharacterPanel';
import { ScenePanel } from './ScenePanel';
import { StoryListPanel } from './StoryListPanel';
import { StoryPreview } from './StoryPreview';
import { OutlinePanel } from './OutlinePanel';
import { AI_MODEL_OPTIONS } from '../constants/aiModels';

export function Layout() {
  const { viewMode, setViewMode, editTab, setEditTab, defaultAiModel, setDefaultAiModel } = useAppStore();

  const tabs = [
    { id: 'characters' as const, label: '角色', icon: Users },
    { id: 'scenes' as const, label: '场景', icon: MapPin },
    { id: 'outlines' as const, label: '大纲', icon: FileText },
    { id: 'stories' as const, label: '故事', icon: BookOpen },
  ];

  return (
    <div className="h-screen flex flex-col bg-gradient-to-br from-pink-50 to-purple-50">
      {/* 顶部导航 */}
      <header className="h-14 bg-white border-b border-pink-100 flex items-center justify-between px-4 flex-shrink-0">
        <div className="flex items-center gap-3">
          <img src="/logo.png" alt="Pawtale" className="h-8 w-8 object-contain" />
          <h1 className="text-xl font-bold bg-gradient-to-r from-pink-500 to-purple-500 bg-clip-text text-transparent">
            Pawtale
          </h1>
          <span className="text-sm text-gray-400">是我的CP呀</span>
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

          {/* 视图切换 */}
          <div className="flex items-center gap-1 bg-gray-100 rounded-lg p-1">
            <button
              onClick={() => setViewMode('edit')}
              className={`p-1.5 rounded-md transition-colors ${
                viewMode === 'edit' ? 'bg-white shadow-sm' : 'hover:bg-gray-200'
              }`}
              title="仅编辑"
            >
              <PanelLeft size={18} className="text-gray-600" />
            </button>
            <button
              onClick={() => setViewMode('dual')}
              className={`p-1.5 rounded-md transition-colors ${
                viewMode === 'dual' ? 'bg-white shadow-sm' : 'hover:bg-gray-200'
              }`}
              title="双栏"
            >
              <Columns2 size={18} className="text-gray-600" />
            </button>
            <button
              onClick={() => setViewMode('preview')}
              className={`p-1.5 rounded-md transition-colors ${
                viewMode === 'preview' ? 'bg-white shadow-sm' : 'hover:bg-gray-200'
              }`}
              title="仅预览"
            >
              <PanelRight size={18} className="text-gray-600" />
            </button>
          </div>
        </div>
      </header>

      {/* 主内容区 */}
      <main className="flex-1 flex overflow-hidden">
        {/* 左栏：编辑区 */}
        {(viewMode === 'dual' || viewMode === 'edit') && (
          <div className={`flex flex-col bg-white border-r border-pink-100 ${
            viewMode === 'dual' ? 'w-96' : 'flex-1'
          }`}>
            {/* Tab 切换 */}
            <div className="flex border-b border-pink-100">
              {tabs.map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setEditTab(tab.id)}
                  className={`flex-1 flex items-center justify-center gap-2 py-3 text-sm font-medium transition-colors ${
                    editTab === tab.id
                      ? 'text-pink-600 border-b-2 border-pink-500'
                      : 'text-gray-500 hover:text-gray-700'
                  }`}
                >
                  <tab.icon size={16} />
                  {tab.label}
                </button>
              ))}
            </div>

            {/* Tab 内容 */}
            <div className="flex-1 overflow-hidden">
              {editTab === 'characters' && <CharacterPanel />}
              {editTab === 'scenes' && <ScenePanel />}
              {editTab === 'outlines' && <OutlinePanel />}
              {editTab === 'stories' && <StoryListPanel />}
            </div>
          </div>
        )}

        {/* 右栏：预览区 */}
        {(viewMode === 'dual' || viewMode === 'preview') && (
          <div className="flex-1 bg-white relative">
            <StoryPreview />
          </div>
        )}
      </main>
    </div>
  );
}
