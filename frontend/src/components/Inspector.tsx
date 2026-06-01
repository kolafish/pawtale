import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronRight, FileText, BookOpen, FolderOpen, Trash2, Download, X } from 'lucide-react';
import { projectsApi } from '../api';
import { ReferenceSheet } from './ReferenceSheet';
import type { ReferenceItem, Project } from '../types';

type InspectorTab = 'document' | 'references' | 'project';

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
  onExport: () => void;
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
  onExport,
}: InspectorProps) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<InspectorTab>('document');
  const [editingRef, setEditingRef] = useState<ReferenceItem | null>(null);

  const { data: references = [], refetch: refetchRefs } = useQuery({
    queryKey: ['references', project.id],
    queryFn: () => projectsApi.listReferences(project.id),
    enabled: open,
  });

  const { data: docRefs = [], refetch: refetchDocRefs } = useQuery({
    queryKey: ['docRefs', project.id, currentDocumentId],
    queryFn: () => currentDocumentId ? projectsApi.getDocumentReferences(project.id, currentDocumentId) : Promise.resolve([]),
    enabled: open && !!currentDocumentId,
  });

  const tabs: { id: InspectorTab; label: string; icon: typeof FileText }[] = [
    { id: 'document', label: '文档', icon: FileText },
    { id: 'references', label: '资料', icon: BookOpen },
    { id: 'project', label: '项目', icon: FolderOpen },
  ];

  const handleCreateRef = async (type: string) => {
    const name = type === 'character' ? '新角色' : type === 'location' ? '新地点' : '新笔记';
    const ref = await projectsApi.createReference(project.id, { type, name });
    setEditingRef(ref);
    refetchRefs();
  };

  const handleUpdateRef = async (ref: ReferenceItem) => {
    await projectsApi.updateReference(project.id, ref.id, {
      name: ref.name,
      content: ref.content,
      metadata_json: ref.metadata_json,
    });
    refetchRefs();
    setEditingRef(null);
  };

  const handleDeleteRef = async (refId: string) => {
    await projectsApi.deleteReference(project.id, refId);
    refetchRefs();
    if (editingRef?.id === refId) setEditingRef(null);
  };

  const toggleDocRef = async (refId: string) => {
    if (!currentDocumentId) return;
    const currentIds = docRefs.map((r) => r.id);
    const newIds = currentIds.includes(refId)
      ? currentIds.filter((id) => id !== refId)
      : [...currentIds, refId];
    await projectsApi.setDocumentReferences(project.id, currentDocumentId, newIds);
    refetchDocRefs();
  };

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

        {tab === 'references' && (
          <div className="space-y-3">
            {editingRef ? (
              <ReferenceSheet
                ref_item={editingRef}
                onChange={setEditingRef}
                onSave={() => handleUpdateRef(editingRef)}
                onCancel={() => setEditingRef(null)}
              />
            ) : (
              <>
                <div className="flex items-center gap-2 mb-2">
                  <button
                    onClick={() => handleCreateRef('character')}
                    className="flex-1 text-xs px-2 py-1.5 rounded border border-gray-200 hover:bg-gray-50"
                  >
                    + 角色
                  </button>
                  <button
                    onClick={() => handleCreateRef('location')}
                    className="flex-1 text-xs px-2 py-1.5 rounded border border-gray-200 hover:bg-gray-50"
                  >
                    + 地点
                  </button>
                  <button
                    onClick={() => handleCreateRef('note')}
                    className="flex-1 text-xs px-2 py-1.5 rounded border border-gray-200 hover:bg-gray-50"
                  >
                    + 笔记
                  </button>
                </div>
                {references.length === 0 ? (
                  <p className="text-xs text-gray-400 text-center py-4">暂无参考资料</p>
                ) : (
                  <div className="space-y-1">
                    {references.map((ref) => (
                      <div
                        key={ref.id}
                        className="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-gray-50 cursor-pointer group"
                        onClick={() => setEditingRef(ref)}
                      >
                        <span className="text-xs px-1.5 py-0.5 rounded bg-gray-100 text-gray-500">
                          {ref.type === 'character' ? '角色' : ref.type === 'location' ? '地点' : '笔记'}
                        </span>
                        <span className="flex-1 text-sm text-gray-700 truncate">{ref.name}</span>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDeleteRef(ref.id);
                          }}
                          className="opacity-0 group-hover:opacity-100 p-0.5 text-gray-400 hover:text-red-500"
                        >
                          <Trash2 size={12} />
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}
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
            <button
              onClick={onExport}
              className="w-full inline-flex items-center justify-center gap-2 px-3 py-2 rounded-lg border border-gray-200 hover:bg-gray-50 text-sm"
            >
              <Download size={14} />
              导出 TXT
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
