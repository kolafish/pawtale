import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Archive, ChevronRight, FileText, FolderOpen, Download, X, Github, RefreshCw, UploadCloud, Unlink } from 'lucide-react';
import { projectsApi } from '../api';
import type { GitSyncRepositoryOption, Project, ProjectExportDownloadFormat } from '../types';

type InspectorTab = 'document' | 'project';

interface InspectorProps {
  project: Project;
  currentDocumentId: string | null;
  synopsis: string;
  setSynopsis: (v: string) => void;
  status: string;
  setStatus: (v: string) => void;
  label: string;
  setLabel: (v: string) => void;
  wordCount: number;
  totalWordCount: number;
  exportingFormat: ProjectExportDownloadFormat | null;
  onExport: (format: ProjectExportDownloadFormat) => void;
}

const exportOptions: {
  format: ProjectExportDownloadFormat;
  label: string;
  title: string;
  icon: typeof FileText;
}[] = [
  { format: 'txt', label: 'TXT', title: '导出 TXT', icon: FileText },
  { format: 'markdown', label: 'MD', title: '导出 Markdown', icon: Download },
  { format: 'zip', label: 'ZIP', title: '导出 ZIP', icon: Archive },
];

function parseRepositoryOption(
  fullName: string,
  repositories: GitSyncRepositoryOption[],
): GitSyncRepositoryOption | null {
  const existing = repositories.find((repo) => repo.full_name === fullName);
  if (existing) return existing;
  const [owner, name] = fullName.split('/');
  if (!owner || !name || repositories.length === 0) return null;
  return {
    installation_id: repositories[0].installation_id,
    owner,
    name,
    full_name: `${owner}/${name}`,
    private: true,
    default_branch: 'main',
  };
}

export function Inspector({
  project,
  currentDocumentId,
  synopsis,
  setSynopsis,
  status,
  setStatus,
  label,
  setLabel,
  wordCount,
  totalWordCount,
  exportingFormat,
  onExport,
}: InspectorProps) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<InspectorTab>('document');
  const [selectedRepo, setSelectedRepo] = useState('');
  const [syncBranch, setSyncBranch] = useState<string | null>(null);
  const [syncBasePath, setSyncBasePath] = useState<string | null>(null);

  const { data: references = [] } = useQuery({
    queryKey: ['references', project.id],
    queryFn: () => projectsApi.listReferences(project.id),
    enabled: open && tab === 'document',
  });

  const { data: docRefs = [], refetch: refetchDocRefs } = useQuery({
    queryKey: ['docRefs', project.id, currentDocumentId],
    queryFn: () => currentDocumentId ? projectsApi.getDocumentReferences(project.id, currentDocumentId) : Promise.resolve([]),
    enabled: open && !!currentDocumentId,
  });

  const syncQuery = useQuery({
    queryKey: ['gitSync', project.id],
    queryFn: () => projectsApi.getGitSync(project.id),
    enabled: open && tab === 'project',
  });

  const syncStatus = syncQuery.data;
  const configuredRepo = syncStatus?.project_config
    ? `${syncStatus.project_config.repository_owner}/${syncStatus.project_config.repository_name}`
    : '';
  const activeRepo = selectedRepo || configuredRepo || syncStatus?.repositories[0]?.full_name || '';
  const activeRepoOption = syncStatus?.repositories.find((repo) => repo.full_name === activeRepo);
  const activeBranch = syncBranch ?? syncStatus?.project_config?.branch ?? activeRepoOption?.default_branch ?? 'main';
  const activeBasePath = syncBasePath ?? syncStatus?.project_config?.base_path ?? `pawtale/${project.id}`;

  const invalidateGitSync = () => queryClient.invalidateQueries({ queryKey: ['gitSync', project.id] });

  const saveSyncMutation = useMutation({
    mutationFn: () => {
      const repo = activeRepoOption || parseRepositoryOption(activeRepo, syncStatus?.repositories ?? []);
      if (!repo) throw new Error('请选择仓库');
      return projectsApi.saveGitSync(project.id, {
        installation_id: repo.installation_id,
        repository_owner: repo.owner,
        repository_name: repo.name,
        branch: activeBranch,
        base_path: activeBasePath,
      });
    },
    onSuccess: () => {
      setSelectedRepo('');
      setSyncBranch(null);
      setSyncBasePath(null);
      invalidateGitSync();
    },
  });

  const connectGitHubMutation = useMutation({
    mutationFn: () => projectsApi.createGitHubSetupUrl(),
    onSuccess: (data) => {
      window.location.assign(data.install_url);
    },
  });

  const runSyncMutation = useMutation({
    mutationFn: () => projectsApi.runGitSync(project.id),
    onSuccess: invalidateGitSync,
  });

  const deleteSyncMutation = useMutation({
    mutationFn: () => projectsApi.deleteGitSync(project.id),
    onSuccess: invalidateGitSync,
  });

  const tabs: { id: InspectorTab; label: string; icon: typeof FileText }[] = [
    { id: 'document', label: '文档', icon: FileText },
    { id: 'project', label: '项目', icon: FolderOpen },
  ];

  const toggleDocRef = async (refId: string) => {
    if (!currentDocumentId) return;
    const currentIds = docRefs.map((r) => r.id);
    const newIds = currentIds.includes(refId)
      ? currentIds.filter((id) => id !== refId)
      : [...currentIds, refId];
    await projectsApi.setDocumentReferences(project.id, currentDocumentId, newIds);
    refetchDocRefs();
  };

  const lastSyncedAt = syncStatus?.project_config?.last_synced_at
    ? new Date(syncStatus.project_config.last_synced_at).toLocaleString('zh-CN')
    : null;

  const syncError = syncStatus?.project_config?.last_error || runSyncMutation.data?.error || null;

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="absolute top-3 right-3 p-2 rounded-lg bg-white border border-gray-200 shadow-sm hover:bg-gray-50 text-gray-500 z-10"
        title="打开检查器"
      >
        <ChevronRight size={16} className="rotate-180" />
      </button>
    );
  }

  return (
    <div className="absolute top-0 right-0 h-full w-80 bg-white border-l border-gray-200 shadow-lg z-20 flex flex-col">
      {/* Header */}
      <div className="flex items-center border-b border-gray-200">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`flex-1 flex items-center justify-center gap-1.5 py-3 text-sm font-medium transition-colors ${
              tab === t.id
                ? 'text-pink-600 border-b-2 border-pink-500'
                : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            <t.icon size={14} />
            {t.label}
          </button>
        ))}
        <button onClick={() => setOpen(false)} className="p-2 text-gray-400 hover:text-gray-600">
          <X size={16} />
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-3">
        {tab === 'document' && currentDocumentId && (
          <div className="space-y-4">
            <div>
              <label className="text-xs font-medium text-gray-500 mb-1 block">摘要</label>
              <textarea
                value={synopsis}
                onChange={(e) => setSynopsis(e.target.value)}
                placeholder="简要描述这个文档的内容..."
                rows={3}
                className="w-full px-2 py-1.5 text-sm rounded border border-gray-200 focus:outline-none focus:ring-1 focus:ring-pink-400 resize-none"
              />
            </div>
            <div className="flex gap-3">
              <div className="flex-1">
                <label className="text-xs font-medium text-gray-500 mb-1 block">状态</label>
                <select
                  value={status}
                  onChange={(e) => setStatus(e.target.value)}
                  className="w-full px-2 py-1.5 text-sm rounded border border-gray-200 focus:outline-none focus:ring-1 focus:ring-pink-400"
                >
                  <option value="draft">草稿</option>
                  <option value="revision">修改中</option>
                  <option value="done">完成</option>
                </select>
              </div>
              <div className="flex-1">
                <label className="text-xs font-medium text-gray-500 mb-1 block">标签</label>
                <input
                  value={label}
                  onChange={(e) => setLabel(e.target.value)}
                  placeholder="自定义标签"
                  className="w-full px-2 py-1.5 text-sm rounded border border-gray-200 focus:outline-none focus:ring-1 focus:ring-pink-400"
                />
              </div>
            </div>
            <div>
              <label className="text-xs font-medium text-gray-500 mb-1 block">字数</label>
              <p className="text-sm text-gray-700">{wordCount.toLocaleString()} 字</p>
            </div>
            <div>
              <label className="text-xs font-medium text-gray-500 mb-2 block">关联资料</label>
              {docRefs.length === 0 ? (
                <p className="text-xs text-gray-400">没有关联的资料</p>
              ) : (
                <div className="space-y-1">
                  {docRefs.map((ref) => (
                    <div key={ref.id} className="flex items-center justify-between text-sm">
                      <span className="truncate text-gray-700">{ref.name}</span>
                      <button
                        onClick={() => toggleDocRef(ref.id)}
                        className="text-xs text-red-400 hover:text-red-600"
                      >
                        移除
                      </button>
                    </div>
                  ))}
                </div>
              )}
              {references.filter((r) => !docRefs.some((dr) => dr.id === r.id)).length > 0 && (
                <select
                  onChange={(e) => {
                    if (e.target.value) toggleDocRef(e.target.value);
                    e.target.value = '';
                  }}
                  className="mt-2 w-full px-2 py-1.5 text-sm rounded border border-gray-200 focus:outline-none focus:ring-1 focus:ring-pink-400"
                  defaultValue=""
                >
                  <option value="">+ 添加关联...</option>
                  {references
                    .filter((r) => !docRefs.some((dr) => dr.id === r.id))
                    .map((r) => (
                      <option key={r.id} value={r.id}>{r.name} ({r.type})</option>
                    ))}
                </select>
              )}
            </div>
          </div>
        )}

        {tab === 'project' && (
          <div className="space-y-4">
            <div>
              <label className="text-xs font-medium text-gray-500 mb-1 block">项目标题</label>
              <p className="text-sm text-gray-800">{project.title}</p>
            </div>
            {project.description && (
              <div>
                <label className="text-xs font-medium text-gray-500 mb-1 block">描述</label>
                <p className="text-sm text-gray-600">{project.description}</p>
              </div>
            )}
            <div>
              <label className="text-xs font-medium text-gray-500 mb-1 block">总字数</label>
              <p className="text-sm text-gray-800">{totalWordCount.toLocaleString()} 字</p>
              {project.word_count_target && project.word_count_target > 0 && (
                <>
                  <div className="mt-1 h-2 bg-gray-100 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-gradient-to-r from-pink-400 to-purple-400 rounded-full"
                      style={{ width: `${Math.min(100, (totalWordCount / project.word_count_target) * 100)}%` }}
                    />
                  </div>
                  <p className="text-xs text-gray-400 mt-1">
                    目标 {project.word_count_target.toLocaleString()} 字 ({Math.round((totalWordCount / project.word_count_target) * 100)}%)
                  </p>
                </>
              )}
            </div>
            <div className="space-y-2">
              <label className="text-xs font-medium text-gray-500 block">本地导出</label>
              <div className="grid grid-cols-3 gap-2">
                {exportOptions.map((option) => {
                  const Icon = option.icon;
                  const isExporting = exportingFormat === option.format;
                  return (
                    <button
                      key={option.format}
                      type="button"
                      onClick={() => onExport(option.format)}
                      disabled={exportingFormat !== null}
                      title={option.title}
                      className="inline-flex min-w-0 items-center justify-center gap-1.5 px-2 py-2 rounded-lg border border-gray-200 hover:bg-gray-50 text-xs disabled:opacity-60"
                    >
                      {isExporting ? <RefreshCw size={13} className="animate-spin" /> : <Icon size={13} />}
                      <span>{option.label}</span>
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="space-y-3 border-t border-gray-100 pt-4">
              <div className="flex items-center justify-between">
                <label className="text-xs font-medium text-gray-500">GitHub 同步</label>
                <button
                  onClick={() => syncQuery.refetch()}
                  className="p-1 rounded text-gray-400 hover:bg-gray-100 hover:text-gray-600"
                  title="刷新"
                >
                  <RefreshCw size={13} className={syncQuery.isFetching ? 'animate-spin' : ''} />
                </button>
              </div>

              {syncQuery.isLoading ? (
                <p className="text-xs text-gray-400">加载中...</p>
              ) : !syncStatus?.configured ? (
                <p className="text-xs text-gray-400">后端未配置 GitHub App</p>
              ) : (
                <div className="space-y-2">
                  <div className="flex gap-2">
                    <button
                      onClick={() => connectGitHubMutation.mutate()}
                      disabled={connectGitHubMutation.isPending}
                      className="flex-1 inline-flex items-center justify-center gap-1.5 px-2 py-1.5 rounded-md border border-gray-200 hover:bg-gray-50 text-xs disabled:opacity-60"
                    >
                      {connectGitHubMutation.isPending ? <RefreshCw size={13} className="animate-spin" /> : <Github size={13} />}
                      连接
                    </button>
                    {syncStatus.project_config && (
                      <button
                        onClick={() => deleteSyncMutation.mutate()}
                        disabled={deleteSyncMutation.isPending}
                        className="inline-flex items-center justify-center gap-1.5 px-2 py-1.5 rounded-md border border-gray-200 hover:bg-red-50 hover:text-red-600 text-xs disabled:opacity-60"
                      >
                        <Unlink size={13} />
                        断开
                      </button>
                    )}
                  </div>

                  {syncStatus.authorization_error && (
                    <p className="text-xs text-red-500">{syncStatus.authorization_error}</p>
                  )}
                  {syncStatus.repositories.length === 0 ? (
                    <p className="text-xs text-gray-400">暂无可用仓库</p>
                  ) : (
                    <>
                      <select
                        value={activeRepo}
                        onChange={(e) => {
                          const repo = syncStatus.repositories.find((item) => item.full_name === e.target.value);
                          setSelectedRepo(e.target.value);
                          setSyncBranch(repo?.default_branch || 'main');
                        }}
                        className="w-full px-2 py-1.5 text-sm rounded border border-gray-200 focus:outline-none focus:ring-1 focus:ring-pink-400"
                      >
                        {syncStatus.repositories.map((repo) => (
                          <option key={`${repo.installation_id}:${repo.full_name}`} value={repo.full_name}>
                            {repo.full_name}
                          </option>
                        ))}
                      </select>
                      <div className="grid grid-cols-2 gap-2">
                        <input
                          value={activeBranch}
                          onChange={(e) => setSyncBranch(e.target.value)}
                          placeholder="branch"
                          className="w-full px-2 py-1.5 text-sm rounded border border-gray-200 focus:outline-none focus:ring-1 focus:ring-pink-400"
                        />
                        <input
                          value={activeBasePath}
                          onChange={(e) => setSyncBasePath(e.target.value)}
                          placeholder="path"
                          className="w-full px-2 py-1.5 text-sm rounded border border-gray-200 focus:outline-none focus:ring-1 focus:ring-pink-400"
                        />
                      </div>
                      <button
                        onClick={() => saveSyncMutation.mutate()}
                        disabled={saveSyncMutation.isPending || !activeRepo}
                        className="w-full inline-flex items-center justify-center gap-2 px-3 py-2 rounded-lg border border-gray-200 hover:bg-gray-50 text-sm disabled:opacity-60"
                      >
                        {saveSyncMutation.isPending ? <RefreshCw size={14} className="animate-spin" /> : <Github size={14} />}
                        保存配置
                      </button>
                    </>
                  )}

                  {syncStatus.project_config && (
                    <button
                      onClick={() => runSyncMutation.mutate()}
                      disabled={runSyncMutation.isPending}
                      className="w-full inline-flex items-center justify-center gap-2 px-3 py-2 rounded-lg bg-pink-500 text-white hover:bg-pink-600 text-sm disabled:opacity-60"
                    >
                      {runSyncMutation.isPending ? <RefreshCw size={14} className="animate-spin" /> : <UploadCloud size={14} />}
                      同步到 GitHub
                    </button>
                  )}

                  {lastSyncedAt && (
                    <p className="text-xs text-gray-400">上次同步 {lastSyncedAt}</p>
                  )}
                  {syncError && (
                    <p className="text-xs text-red-500">{syncError}</p>
                  )}
                  {saveSyncMutation.error instanceof Error && (
                    <p className="text-xs text-red-500">{saveSyncMutation.error.message}</p>
                  )}
                  {connectGitHubMutation.error instanceof Error && (
                    <p className="text-xs text-red-500">{connectGitHubMutation.error.message}</p>
                  )}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
