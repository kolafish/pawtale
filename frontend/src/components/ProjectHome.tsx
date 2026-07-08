import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, FolderOpen, Trash2, RefreshCw, Pencil, Check, X } from 'lucide-react';
import { projectsApi } from '../api';
import { formatProjectStructure, getProjectProfile } from '../domain/projectProfiles';
import { useAppStore } from '../stores/appStore';
import type { ProjectListItem } from '../types';

export function ProjectHome() {
  const queryClient = useQueryClient();
  const { setCurrentProject, setCurrentDocumentId } = useAppStore();
  const [newTitle, setNewTitle] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [editingProjectId, setEditingProjectId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState('');
  const novelProfile = getProjectProfile('novel');

  const { data: projects = [], isLoading } = useQuery({
    queryKey: ['projects'],
    queryFn: projectsApi.list,
  });

  const createMutation = useMutation({
    mutationFn: ({ title }: { title: string }) => projectsApi.create({ type: 'novel', title }),
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

  const renameMutation = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) => projectsApi.update(id, { title }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['projects'] });
      setEditingProjectId(null);
      setEditingTitle('');
    },
  });

  const startRename = (project: ProjectListItem) => {
    if (project.can_edit === false) return;
    setEditingProjectId(project.id);
    setEditingTitle(project.title);
  };

  const cancelRename = () => {
    setEditingProjectId(null);
    setEditingTitle('');
  };

  const commitRename = (project: ProjectListItem) => {
    if (project.can_edit === false) {
      cancelRename();
      return;
    }
    const title = editingTitle.trim();
    if (!title || title === project.title || renameMutation.isPending) {
      cancelRename();
      return;
    }
    renameMutation.mutate({ id: project.id, title });
  };

  const openProject = async (item: ProjectListItem) => {
    if (editingProjectId) return;
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
            <h3 className="text-lg font-semibold text-gray-800 mb-3">新建小说项目</h3>
            <p className="mb-4 text-xs text-gray-500">{novelProfile.description}</p>
            <div className="flex flex-col gap-3 sm:flex-row">
              <input
                value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)}
                placeholder={novelProfile.placeholder}
                autoFocus
                className="flex-1 px-3 py-2 rounded-lg border border-gray-200 focus:outline-none focus:ring-2 focus:ring-pink-500"
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && newTitle.trim()) {
                    createMutation.mutate({ title: newTitle.trim() });
                  }
                }}
              />
              <button
                onClick={() => newTitle.trim() && createMutation.mutate({ title: newTitle.trim() })}
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
          <div className="flex flex-col gap-4">
            {projects.map((proj) => {
              const canEditProject = proj.can_edit !== false;
              const ownerLabel = proj.owner_display_name || proj.owner_email || '';
              const showOwner = Boolean(ownerLabel && !canEditProject);
              return (
                <div
                  key={proj.id}
                  onClick={() => openProject(proj)}
                  className="group rounded-2xl border border-gray-100 bg-white p-5 hover:border-pink-200 hover:shadow-md transition-all cursor-pointer"
                >
                  <div className="flex items-start justify-between">
                    {editingProjectId === proj.id && canEditProject ? (
                    <div className="flex flex-1 items-center gap-2" onClick={(e) => e.stopPropagation()}>
                      <input
                        value={editingTitle}
                        onChange={(e) => setEditingTitle(e.target.value)}
                        autoFocus
                        className="flex-1 rounded-md border border-pink-200 px-2 py-1 text-sm font-semibold text-gray-800 focus:outline-none focus:ring-2 focus:ring-pink-400"
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') commitRename(proj);
                          if (e.key === 'Escape') cancelRename();
                        }}
                      />
                      <button
                        onClick={() => commitRename(proj)}
                        disabled={!editingTitle.trim() || renameMutation.isPending}
                        className="p-1 rounded text-green-600 hover:bg-green-50 disabled:opacity-40"
                        title="保存名称"
                      >
                        {renameMutation.isPending ? <RefreshCw size={14} className="animate-spin" /> : <Check size={14} />}
                      </button>
                      <button
                        onClick={cancelRename}
                        className="p-1 rounded text-gray-400 hover:bg-gray-100 hover:text-gray-600"
                        title="取消"
                      >
                        <X size={14} />
                      </button>
                    </div>
                  ) : (
                    <>
                        <div className="min-w-0 flex-1">
                          <h3 className="font-semibold text-gray-800 group-hover:text-pink-600 transition-colors truncate">
                            {proj.title}
                          </h3>
                          {showOwner && (
                            <p className="mt-1 truncate text-xs text-gray-400">所属：{ownerLabel}</p>
                          )}
                        </div>
                        {canEditProject && (
                          <>
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                startRename(proj);
                              }}
                              className="opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-gray-100 text-gray-400 hover:text-gray-600 transition-all"
                              title="重命名项目"
                            >
                              <Pencil size={14} />
                            </button>
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                if (confirm('确定删除该项目？')) deleteMutation.mutate(proj.id);
                              }}
                              className="opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-red-50 text-gray-400 hover:text-red-500 transition-all"
                              title="删除项目"
                            >
                              <Trash2 size={14} />
                            </button>
                          </>
                        )}
                    </>
                  )}
                  </div>
                  {proj.description && (
                    <p className="text-sm text-gray-500 mt-1 line-clamp-2">{proj.description}</p>
                  )}
                  <div className="flex items-center gap-3 mt-3 text-xs text-gray-400">
                    <span>{getProjectProfile(proj.type).label}</span>
                    <span>{formatProjectStructure(proj)}</span>
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
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
