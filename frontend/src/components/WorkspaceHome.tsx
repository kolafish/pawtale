import { RefreshCw, Sparkles, FolderOpen } from 'lucide-react';
import type { Story } from '../types';

interface WorkspaceHomeProps {
  newTitle: string;
  setNewTitle: (v: string) => void;
  newContent: string;
  setNewContent: (v: string) => void;
  selectedExistingStoryId: string;
  setSelectedExistingStoryId: (v: string) => void;
  storyOptions: Story[];
  isCreating: boolean;
  isLoading: boolean;
  onCreateWorkspace: () => void;
  onOpenExistingStory: () => void;
}

export function WorkspaceHome({
  newTitle,
  setNewTitle,
  newContent,
  setNewContent,
  selectedExistingStoryId,
  setSelectedExistingStoryId,
  storyOptions,
  isCreating,
  isLoading,
  onCreateWorkspace,
  onOpenExistingStory,
}: WorkspaceHomeProps) {
  return (
    <div className="h-full overflow-y-auto p-6 bg-gradient-to-b from-white to-pink-50/40">
      <div className="max-w-5xl mx-auto grid grid-cols-1 lg:grid-cols-2 gap-6">
        <section className="rounded-2xl border border-pink-100 bg-white p-5">
          <h2 className="text-lg font-semibold text-gray-800">新建作品</h2>
          <p className="text-sm text-gray-500 mt-1">不需要先配角色/场景，直接开写。</p>

          <div className="mt-4 space-y-3">
            <input
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
              placeholder="作品标题（可选）"
              className="w-full px-3 py-2 rounded-lg border border-gray-200 focus:outline-none focus:ring-2 focus:ring-pink-500"
            />
            <textarea
              value={newContent}
              onChange={(e) => setNewContent(e.target.value)}
              placeholder="在这里开始写第一段..."
              rows={10}
              className="w-full px-3 py-2 rounded-lg border border-gray-200 focus:outline-none focus:ring-2 focus:ring-pink-500"
            />
            <button
              onClick={onCreateWorkspace}
              disabled={isCreating}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-pink-500 text-white hover:bg-pink-600 disabled:opacity-60"
            >
              {isCreating ? <RefreshCw size={16} className="animate-spin" /> : <Sparkles size={16} />}
              开始写作
            </button>
          </div>
        </section>

        <section className="rounded-2xl border border-pink-100 bg-white p-5">
          <h2 className="text-lg font-semibold text-gray-800">打开已有作品</h2>
          <p className="text-sm text-gray-500 mt-1">
            把历史故事转成编辑器工作区继续写。当前检测到 {storyOptions.length} 个故事。
          </p>

          <div className="mt-4 space-y-3">
            <select
              value={selectedExistingStoryId}
              onChange={(e) => setSelectedExistingStoryId(e.target.value)}
              className="w-full px-3 py-2 rounded-lg border border-gray-200 focus:outline-none focus:ring-2 focus:ring-pink-500"
            >
              <option value="">选择一个故事</option>
              {storyOptions.map((story) => (
                <option key={story.id} value={story.id}>
                  {story.title}
                </option>
              ))}
            </select>
            <button
              onClick={onOpenExistingStory}
              disabled={!selectedExistingStoryId || isLoading}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-gray-800 text-white hover:bg-black disabled:opacity-60"
            >
              {isLoading ? <RefreshCw size={16} className="animate-spin" /> : <FolderOpen size={16} />}
              打开并进入写作
            </button>
          </div>
        </section>
      </div>
    </div>
  );
}
