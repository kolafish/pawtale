import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, FolderOpen, Trash2, RefreshCw, Pencil, Check, X } from 'lucide-react';
import { projectsApi } from '../api';
import { getInitialProjectDocumentId } from '../domain/projectNavigation';
import { formatProjectStructure, getProjectProfile } from '../domain/projectProfiles';
import { useAppStore } from '../stores/appStore';
import { ProjectDeleteDialog } from './ProjectDeleteDialog';
import type { Project, ProjectListItem } from '../types';

export function ProjectHome() {
  const queryClient = useQueryClient();
  const { setCurrentProject, setCurrentDocumentId } = useAppStore();
  const [newTitle, setNewTitle] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [editingProjectId, setEditingProjectId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState('');
  const [projectPendingDeletion, setProjectPendingDeletion] = useState<ProjectListItem | null>(null);
  const [deleteError, setDeleteError] = useState('');
  const novelProfile = getProjectProfile('novel');

  const openLoadedProject = (project: Project) => {
    setCurrentProject(project);
    setCurrentDocumentId(getInitialProjectDocumentId(project));
  };

  const { data: projects = [], isLoading } = useQuery({
    queryKey: ['projects'],
    queryFn: projectsApi.list,
  });

  const createMutation = useMutation({
    mutationFn: ({ title }: { title: string }) => projectsApi.create({ type: 'novel', title }),
    onSuccess: (project) => {
      queryClient.invalidateQueries({ queryKey: ['projects'] });
      openLoadedProject(project);
      setNewTitle('');
      setShowCreate(false);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => projectsApi.delete(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['projects'] });
      setProjectPendingDeletion(null);
      setDeleteError('');
    },
    onError: () => setDeleteError('删除失败，项目尚未从列表中移除。请刷新后重试。'),
  });

  const requestProjectDeletion = (project: ProjectListItem) => {
    if (!project.can_delete || deleteMutation.isPending) return;
    setDeleteError('');
    setProjectPendingDeletion(project);
  };

  const cancelProjectDeletion = () => {
    if (deleteMutation.isPending) return;
    setProjectPendingDeletion(null);
    setDeleteError('');
  };

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
    openLoadedProject(project);
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
            className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-pink-500 px-4 py-2 text-white transition-[background-color,scale] duration-150 ease-out hover:bg-pink-600 active:scale-[0.96]"
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
                className="min-h-10 rounded-lg bg-pink-500 px-4 py-2 text-white transition-[background-color,scale] duration-150 ease-out hover:bg-pink-600 active:scale-[0.96] disabled:opacity-60 disabled:active:scale-100"
              >
                {createMutation.isPending ? <RefreshCw size={16} className="animate-spin" /> : '创建'}
              </button>
              <button
                onClick={() => { setShowCreate(false); setNewTitle(''); }}
                className="min-h-10 rounded-lg border border-gray-200 px-4 py-2 text-gray-600 transition-[background-color,scale] duration-150 ease-out hover:bg-gray-50 active:scale-[0.96]"
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
              className="min-h-10 rounded-lg px-3 font-medium text-pink-500 transition-[color,background-color,scale] duration-150 ease-out hover:bg-pink-50 hover:text-pink-600 active:scale-[0.96]"
            >
              创建第一个项目
            </button>
          </div>
        ) : (
          <div className="flex flex-col gap-4">
            {projects.map((proj) => {
              const canEditProject = proj.can_edit !== false;
              const showWritingStats = proj.type !== 'memoir';
              const ownerLabel = proj.owner_display_name || proj.owner_email || '';
              const showOwner = Boolean(ownerLabel && !canEditProject);
              return (
                <div
                  key={proj.id}
                  onClick={() => openProject(proj)}
                  className="group cursor-pointer rounded-2xl border border-gray-100 bg-white p-5 transition-[border-color,box-shadow] duration-150 ease-out hover:border-pink-200 hover:shadow-md"
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
                          className="flex size-10 items-center justify-center rounded-lg text-green-600 transition-[background-color,scale] duration-150 ease-out hover:bg-green-50 active:scale-[0.96] disabled:opacity-40 disabled:active:scale-100"
                          title="保存名称"
                        >
                          {renameMutation.isPending ? <RefreshCw size={14} className="animate-spin" /> : <Check size={14} />}
                        </button>
                        <button
                          onClick={cancelRename}
                          className="flex size-10 items-center justify-center rounded-lg text-gray-400 transition-[color,background-color,scale] duration-150 ease-out hover:bg-gray-100 hover:text-gray-600 active:scale-[0.96]"
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
                        {(canEditProject || proj.can_delete) && (
                          <div className="ml-2 flex items-center gap-1">
                            {canEditProject && (
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  startRename(proj);
                                }}
                                className="flex size-10 items-center justify-center rounded-lg text-gray-400 opacity-100 transition-[opacity,color,background-color,scale] duration-150 ease-out hover:bg-gray-100 hover:text-gray-600 active:scale-[0.96] sm:opacity-0 sm:group-hover:opacity-100 sm:focus-visible:opacity-100"
                                title="重命名项目"
                                aria-label={`重命名项目 ${proj.title}`}
                              >
                                <Pencil size={14} />
                              </button>
                            )}
                            {proj.can_delete && (
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  requestProjectDeletion(proj);
                                }}
                                className="flex size-10 items-center justify-center rounded-lg text-gray-400 opacity-100 transition-[opacity,color,background-color,scale] duration-150 ease-out hover:bg-red-50 hover:text-red-600 active:scale-[0.96] sm:opacity-0 sm:group-hover:opacity-100 sm:focus-visible:opacity-100"
                                title="删除项目及全部数据"
                                aria-label={`删除项目及全部数据 ${proj.title}`}
                              >
                                <Trash2 size={14} />
                              </button>
                            )}
                          </div>
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
                    {showWritingStats && <span>{proj.total_word_count.toLocaleString()} 字</span>}
                    {showWritingStats && proj.word_count_target && (
                      <span>/ {proj.word_count_target.toLocaleString()} 目标</span>
                    )}
                    <span className="ml-auto">{formatDate(proj.updated_at)}</span>
                  </div>
                  {showWritingStats && proj.word_count_target && proj.word_count_target > 0 && (
                    <div className="mt-2 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                      <div
                        className="h-full rounded-full bg-gradient-to-r from-pink-400 to-purple-400 transition-[width] duration-150"
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
      {projectPendingDeletion && (
        <ProjectDeleteDialog
          project={projectPendingDeletion}
          isDeleting={deleteMutation.isPending}
          errorMessage={deleteError}
          onCancel={cancelProjectDeletion}
          onConfirm={() => deleteMutation.mutate(projectPendingDeletion.id)}
        />
      )}
    </div>
  );
}
