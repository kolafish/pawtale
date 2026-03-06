import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { BookOpen, Heart, Trash2, Pencil, Check, X } from 'lucide-react';
import { storiesApi } from '../api';
import { useAppStore } from '../stores/appStore';
import type { Story } from '../types';
import { AI_MODEL_OPTIONS } from '../constants/aiModels';

export function StoryListPanel() {
  const queryClient = useQueryClient();
  const { setCurrentStory, setCurrentNode, setCurrentWorkspace, setCreationMode } = useAppStore();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState('');

  const { data: stories = [], isLoading, refetch } = useQuery({
    queryKey: ['stories'],
    queryFn: storiesApi.getAll,
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      storiesApi.update(id, { title }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['stories'] });
      setEditingId(null);
    },
  });

  const handleSelectStory = async (story: Story) => {
    setCurrentStory(story);
    if (story.current_node_id && story.nodes[story.current_node_id]) {
      const node = story.nodes[story.current_node_id];
      setCurrentNode({
        story_id: story.id,
        node_id: story.current_node_id,
        title: node.title,
        content: node.content,
        word_count: node.word_count,
        ai_model: node.ai_model || story.ai_model,
        choices: node.choices,
      });
    }
    try {
      const workspace = await storiesApi.getWorkspace(story.id);
      setCurrentWorkspace(workspace);
      setCreationMode('writing');
    } catch {
      // 忽略工作区加载失败，不影响传统故事预览模式
    }
  };

  const handleToggleFavorite = async (e: React.MouseEvent, storyId: string) => {
    e.stopPropagation();
    await storiesApi.toggleFavorite(storyId);
    refetch();
  };

  const handleDelete = async (e: React.MouseEvent, storyId: string) => {
    e.stopPropagation();
    if (confirm('确定要删除这个故事吗？')) {
      await storiesApi.delete(storyId);
      refetch();
    }
  };

  const startEdit = (e: React.MouseEvent, story: Story) => {
    e.stopPropagation();
    setEditingId(story.id);
    setEditTitle(story.title);
  };

  const saveEdit = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (editingId && editTitle.trim()) {
      updateMutation.mutate({ id: editingId, title: editTitle.trim() });
    }
  };

  const cancelEdit = (e: React.MouseEvent) => {
    e.stopPropagation();
    setEditingId(null);
  };

  // 计算故事总字数
  const getStoryWordCount = (story: Story) => {
    const path = story.current_path || Object.keys(story.nodes);
    return path.reduce((sum, nodeId) => {
      const node = story.nodes[nodeId];
      return sum + (node?.word_count || node?.content.length || 0);
    }, 0);
  };

  return (
    <div className="h-full flex flex-col">
      <div className="p-4 border-b border-pink-100">
        <h2 className="text-lg font-semibold text-gray-800">我的故事</h2>
        <p className="text-sm text-gray-500 mt-1">查看和继续你的故事</p>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {isLoading ? (
          <div className="text-center text-gray-500 py-8">加载中...</div>
        ) : stories.length === 0 ? (
          <div className="text-center text-gray-500 py-8">
            <BookOpen size={48} className="mx-auto mb-2 opacity-50" />
            <p>还没有故事</p>
            <p className="text-sm mt-1">选择角色和场景，开始创作吧</p>
          </div>
        ) : (
          stories.map((story) => (
            <div
              key={story.id}
              onClick={() => handleSelectStory(story)}
              className="p-4 bg-white rounded-xl border border-gray-100 hover:border-pink-200 cursor-pointer transition-all"
            >
              <div className="flex items-start justify-between">
                <div className="flex-1 min-w-0">
                  {editingId === story.id ? (
                    <div className="flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
                      <input
                        type="text"
                        value={editTitle}
                        onChange={(e) => setEditTitle(e.target.value)}
                        className="flex-1 px-2 py-1 border border-gray-200 rounded focus:outline-none focus:ring-2 focus:ring-pink-500"
                        autoFocus
                      />
                      <button
                        onClick={saveEdit}
                        disabled={updateMutation.isPending}
                        className="p-1 text-green-500 hover:bg-green-50 rounded"
                      >
                        <Check size={16} />
                      </button>
                      <button
                        onClick={cancelEdit}
                        className="p-1 text-gray-400 hover:bg-gray-100 rounded"
                      >
                        <X size={16} />
                      </button>
                    </div>
                  ) : (
                    <h3 className="font-medium text-gray-800 truncate">{story.title}</h3>
                  )}
                  <div className="flex items-center gap-2 mt-1 flex-wrap">
                    {story.mood && (
                      <span className="px-2 py-0.5 text-xs bg-pink-100 text-pink-600 rounded-full">
                        {story.mood}
                      </span>
                    )}
                    {story.writing_style && (
                      <span className="px-2 py-0.5 text-xs bg-purple-100 text-purple-600 rounded-full">
                        {story.writing_style}
                      </span>
                    )}
                    {story.ai_model && (
                      <span className="px-2 py-0.5 text-xs bg-blue-100 text-blue-600 rounded-full">
                        {AI_MODEL_OPTIONS.find((m) => m.value === story.ai_model)?.label || story.ai_model}
                      </span>
                    )}
                    <span className="text-xs text-gray-400">
                      {(story.current_path || Object.keys(story.nodes)).length} 章
                    </span>
                    <span className="text-xs text-gray-400">
                      {getStoryWordCount(story)} 字
                    </span>
                  </div>
                  {story.characters.length > 0 && (
                    <p className="text-sm text-gray-500 mt-2">
                      {story.characters.map(c => c.name).join(' & ')}
                    </p>
                  )}
                </div>
                <div className="flex gap-1 ml-2">
                  <button
                    onClick={(e) => startEdit(e, story)}
                    className="p-1.5 text-gray-400 hover:text-blue-500 hover:bg-blue-50 rounded-lg transition-colors"
                    title="编辑标题"
                  >
                    <Pencil size={16} />
                  </button>
                  <button
                    onClick={(e) => handleToggleFavorite(e, story.id)}
                    className={`p-1.5 rounded-lg transition-colors ${
                      story.is_favorite
                        ? 'text-pink-500 bg-pink-50'
                        : 'text-gray-400 hover:text-pink-500 hover:bg-pink-50'
                    }`}
                    title="收藏"
                  >
                    <Heart size={16} fill={story.is_favorite ? 'currentColor' : 'none'} />
                  </button>
                  <button
                    onClick={(e) => handleDelete(e, story.id)}
                    className="p-1.5 text-gray-400 hover:text-red-500 hover:bg-red-50 rounded-lg transition-colors"
                    title="删除"
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
