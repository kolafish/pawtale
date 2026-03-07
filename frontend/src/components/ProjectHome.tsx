import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, FolderOpen, Trash2, RefreshCw } from 'lucide-react';
import { projectsApi } from '../api';
import { useAppStore } from '../stores/appStore';
import type { ProjectListItem } from '../types';

export function ProjectHome() {
  const queryClient = useQueryClient();
  const { setCurrentProject, setCurrentDocumentId } = useAppStore();
  const [newTitle, setNewTitle] = useState('');
  const [showCreate, setShowCreate] = useState(false);

  const { data: projects = [], isLoading } = useQuery({
    queryKey: ['projects'],
    queryFn: projectsApi.list,
  });

  const createMutation = useMutation({
    mutationFn: (title: string) => projectsApi.create({ title }),
    onSuccess: (project) => {
      queryClient.invalidateQueries({ queryKey: ['projects'] });
      setCurrentProject(project);
      const firstDoc = project.groups?.[0]?.documents?.[0];
      if (firstDoc) setCurrentDocumentId(firstDoc.id);
      setNewTitle('');
      setShowCreate(false);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => projectsApi.delete(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['projects'] }),
  });

  const openProject = async (item: ProjectListItem) => {
    const project = await projectsApi.get(item.id);
    setCurrentProject(project);
    const firstDoc = project.groups?.[0]?.documents?.[0];
    if (firstDoc) setCurrentDocumentId(firstDoc.id);
  };

  const formatDate = (dateStr: string) => {
    const d = new Date(dateStr);
    return d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' });
  };

  return (
    <div className="h-full overflow-y-auto p-6 bg-gradient-to-b from-white to-pink-50/40">
      <div className="max-w-4xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h2 className="text-2xl font-bold text-gray-800">我的项目</h2>
            <p className="text-sm text-gray-500 mt-1">选择一个项目开始写作，或创建新项目。</p>
          </div>
          <button
            onClick={() => setShowCreate(true)}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-pink-500 text-white hover:bg-pink-600 transition-colors"
          >
            <Plus size={16} />
            新建项目
          </button>
        </div>

        {showCreate && (
          <div className="mb-6 rounded-2xl border border-pink-100 bg-white p-5">
            <h3 className="text-lg font-semibold text-gray-800 mb-3">新建项目</h3>
            <div className="flex gap-3">
              <input
                value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)}
                placeholder="项目标题"
                autoFocus
                className="flex-1 px-3 py-2 rounded-lg border border-gray-200 focus:outline-none focus:ring-2 focus:ring-pink-500"
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && newTitle.trim()) {
                    createMutation.mutate(newTitle.trim());
                  }
                }}
              />
              <button
                onClick={() => newTitle.trim() && createMutation.mutate(newTitle.trim())}
                disabled={!newTitle.trim() || createMutation.isPending}
                className="px-4 py-2 rounded-lg bg-pink-500 text-white hover:bg-pink-600 disabled:opacity-60 transition-colors"
              >
                {createMutation.isPending ? <RefreshCw size={16} className="animate-spin" /> : '创建'}
              </button>
              <button
                onClick={() => { setShowCreate(false); setNewTitle(''); }}
                className="px-4 py-2 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50 transition-colors"
              >
                取消
              </button>
            </div>
          </div>
        )}

        {isLoading ? (
          <div className="text-center py-12 text-gray-400">
            <RefreshCw size={24} className="animate-spin mx-auto mb-2" />
            加载中...
          </div>
        ) : projects.length === 0 ? (
          <div className="text-center py-16">
            <FolderOpen size={48} className="mx-auto text-gray-300 mb-4" />
            <p className="text-gray-500 mb-2">还没有任何项目</p>
            <button
              onClick={() => setShowCreate(true)}
              className="text-pink-500 hover:text-pink-600 font-medium"
            >
              创建第一个项目
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {projects.map((proj) => (
              <div
                key={proj.id}
                onClick={() => openProject(proj)}
                className="group rounded-2xl border border-gray-100 bg-white p-5 hover:border-pink-200 hover:shadow-md transition-all cursor-pointer"
              >
                <div className="flex items-start justify-between">
                  <h3 className="font-semibold text-gray-800 group-hover:text-pink-600 transition-colors truncate flex-1">
                    {proj.title}
                  </h3>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      if (confirm('确定删除该项目？')) deleteMutation.mutate(proj.id);
                    }}
                    className="opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-red-50 text-gray-400 hover:text-red-500 transition-all"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
                {proj.description && (
                  <p className="text-sm text-gray-500 mt-1 line-clamp-2">{proj.description}</p>
                )}
                <div className="flex items-center gap-3 mt-3 text-xs text-gray-400">
                  <span>{proj.total_word_count.toLocaleString()} 字</span>
                  {proj.word_count_target && (
                    <span>/ {proj.word_count_target.toLocaleString()} 目标</span>
                  )}
                  <span className="ml-auto">{formatDate(proj.updated_at)}</span>
                </div>
                {proj.word_count_target && proj.word_count_target > 0 && (
                  <div className="mt-2 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-gradient-to-r from-pink-400 to-purple-400 rounded-full transition-all"
                      style={{ width: `${Math.min(100, (proj.total_word_count / proj.word_count_target) * 100)}%` }}
                    />
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
