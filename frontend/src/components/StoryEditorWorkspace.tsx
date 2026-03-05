import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Bot, RefreshCw, Save, Sparkles, Plus, Trash2, Bug, FolderOpen } from 'lucide-react';
import { storiesApi } from '../api';
import { useAppStore } from '../stores/appStore';
import type {
  AIDebugInfo,
  ExtractedCharacter,
  ExtractedOutlinePoint,
  ExtractedScene,
  Story,
} from '../types';
import { AI_MODEL_OPTIONS } from '../constants/aiModels';
import { AIDebugPanel } from './AIDebugPanel';

function emptyCharacter(): ExtractedCharacter {
  return { name: '', role_hint: '', traits: [], summary: '' };
}

function emptyScene(): ExtractedScene {
  return { name: '', description: '', keywords: [] };
}

function emptyOutlinePoint(): ExtractedOutlinePoint {
  return { stage: '', summary: '' };
}

function splitTokens(value: string): string[] {
  return value
    .split(/[，,、\n]/)
    .map((x) => x.trim())
    .filter(Boolean);
}

export function StoryEditorWorkspace() {
  const queryClient = useQueryClient();
  const { currentWorkspace, setCurrentWorkspace, defaultAiModel } = useAppStore();

  const [newTitle, setNewTitle] = useState('');
  const [newContent, setNewContent] = useState('');
  const [selectedExistingStoryId, setSelectedExistingStoryId] = useState('');

  const [title, setTitle] = useState('');
  const [editorContent, setEditorContent] = useState('');
  const [aiModel, setAiModel] = useState(defaultAiModel);
  const [continueInstruction, setContinueInstruction] = useState('');
  const [debugMode, setDebugMode] = useState(false);
  const [debugInfo, setDebugInfo] = useState<AIDebugInfo | null>(null);

  const [extractedCharacters, setExtractedCharacters] = useState<ExtractedCharacter[]>([]);
  const [extractedScenes, setExtractedScenes] = useState<ExtractedScene[]>([]);
  const [extractedOutlinePoints, setExtractedOutlinePoints] = useState<ExtractedOutlinePoint[]>([]);
  const [extractionNotesText, setExtractionNotesText] = useState('');

  const { data: stories = [] } = useQuery({
    queryKey: ['stories'],
    queryFn: storiesApi.getAll,
  });

  const createWorkspaceMutation = useMutation({
    mutationFn: storiesApi.createWorkspace,
    onSuccess: (workspace) => {
      setCurrentWorkspace(workspace);
      setDebugInfo(null);
      queryClient.invalidateQueries({ queryKey: ['stories'] });
    },
  });

  const loadWorkspaceMutation = useMutation({
    mutationFn: (storyId: string) => storiesApi.getWorkspace(storyId),
    onSuccess: (workspace) => {
      setCurrentWorkspace(workspace);
      setDebugInfo(null);
    },
  });

  const saveWorkspaceMutation = useMutation({
    mutationFn: ({ storyId, payload }: { storyId: string; payload: { title?: string; content?: string; ai_model?: string } }) =>
      storiesApi.updateWorkspace(storyId, payload),
    onSuccess: (workspace) => {
      setCurrentWorkspace(workspace);
      queryClient.invalidateQueries({ queryKey: ['stories'] });
    },
  });

  const continueMutation = useMutation({
    mutationFn: ({ storyId, payload }: { storyId: string; payload: { instruction?: string; ai_model?: string; append?: boolean; debug?: boolean } }) =>
      storiesApi.continueWorkspace(storyId, payload),
    onSuccess: async (result) => {
      setEditorContent(result.editor_content);
      setDebugInfo(result.debug_info || null);
      const workspace = await storiesApi.getWorkspace(result.story_id);
      setCurrentWorkspace(workspace);
      queryClient.invalidateQueries({ queryKey: ['stories'] });
    },
  });

  const extractMutation = useMutation({
    mutationFn: ({ storyId, payload }: { storyId: string; payload: { content?: string; ai_model?: string; debug?: boolean } }) =>
      storiesApi.extractWorkspace(storyId, payload),
    onSuccess: (result) => {
      setExtractedCharacters(result.extracted_characters || []);
      setExtractedScenes(result.extracted_scenes || []);
      setExtractedOutlinePoints(result.extracted_outline_points || []);
      setExtractionNotesText((result.extraction_notes || []).join('\n'));
      setDebugInfo(result.debug_info || null);
    },
  });

  const saveExtractionMutation = useMutation({
    mutationFn: ({
      storyId,
      payload,
    }: {
      storyId: string;
      payload: {
        extracted_characters?: ExtractedCharacter[];
        extracted_scenes?: ExtractedScene[];
        extracted_outline_points?: ExtractedOutlinePoint[];
        extraction_notes?: string[];
      };
    }) => storiesApi.updateWorkspaceExtraction(storyId, payload),
    onSuccess: (workspace) => {
      setCurrentWorkspace(workspace);
    },
  });

  useEffect(() => {
    if (!currentWorkspace) {
      return;
    }
    setTitle(currentWorkspace.title || '未命名作品');
    setEditorContent(currentWorkspace.editor_content || '');
    setAiModel((currentWorkspace.ai_model as typeof defaultAiModel) || defaultAiModel);
    setExtractedCharacters(currentWorkspace.extracted_characters || []);
    setExtractedScenes(currentWorkspace.extracted_scenes || []);
    setExtractedOutlinePoints(currentWorkspace.extracted_outline_points || []);
    setExtractionNotesText((currentWorkspace.extraction_notes || []).join('\n'));
  }, [currentWorkspace, defaultAiModel]);

  const storyOptions = useMemo(() => stories as Story[], [stories]);

  const handleCreateWorkspace = async () => {
    await createWorkspaceMutation.mutateAsync({
      title: newTitle.trim() || '未命名作品',
      content: newContent,
      ai_model: defaultAiModel,
    });
  };

  const handleOpenExistingStory = async () => {
    if (!selectedExistingStoryId) return;
    await loadWorkspaceMutation.mutateAsync(selectedExistingStoryId);
  };

  const handleSaveContent = async () => {
    if (!currentWorkspace) return;
    await saveWorkspaceMutation.mutateAsync({
      storyId: currentWorkspace.story_id,
      payload: {
        title: title.trim() || '未命名作品',
        content: editorContent,
        ai_model: aiModel,
      },
    });

    if (editorContent.trim().length >= 60) {
      await extractMutation.mutateAsync({
        storyId: currentWorkspace.story_id,
        payload: {
          content: editorContent,
          ai_model: aiModel,
          debug: debugMode,
        },
      });
    }
  };

  const handleContinue = async () => {
    if (!currentWorkspace) return;
    await continueMutation.mutateAsync({
      storyId: currentWorkspace.story_id,
      payload: {
        instruction: continueInstruction.trim() || undefined,
        ai_model: aiModel,
        append: true,
        debug: debugMode,
      },
    });
    setContinueInstruction('');
  };

  const handleExtract = async () => {
    if (!currentWorkspace) return;
    await extractMutation.mutateAsync({
      storyId: currentWorkspace.story_id,
      payload: {
        content: editorContent,
        ai_model: aiModel,
        debug: debugMode,
      },
    });
  };

  const handleSaveExtraction = async () => {
    if (!currentWorkspace) return;

    const cleanCharacters = extractedCharacters
      .map((item) => ({
        name: item.name.trim(),
        role_hint: item.role_hint?.trim() || undefined,
        traits: (item.traits || []).map((t) => t.trim()).filter(Boolean),
        summary: item.summary?.trim() || undefined,
      }))
      .filter((item) => item.name);

    const cleanScenes = extractedScenes
      .map((item) => ({
        name: item.name.trim(),
        description: item.description?.trim() || undefined,
        keywords: (item.keywords || []).map((k) => k.trim()).filter(Boolean),
      }))
      .filter((item) => item.name);

    const cleanOutline = extractedOutlinePoints
      .map((item) => ({
        stage: item.stage.trim(),
        summary: item.summary.trim(),
      }))
      .filter((item) => item.stage && item.summary);

    const cleanNotes = extractionNotesText
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean);

    await saveExtractionMutation.mutateAsync({
      storyId: currentWorkspace.story_id,
      payload: {
        extracted_characters: cleanCharacters,
        extracted_scenes: cleanScenes,
        extracted_outline_points: cleanOutline,
        extraction_notes: cleanNotes,
      },
    });
  };

  if (!currentWorkspace) {
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
                onClick={handleCreateWorkspace}
                disabled={createWorkspaceMutation.isPending}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-pink-500 text-white hover:bg-pink-600 disabled:opacity-60"
              >
                {createWorkspaceMutation.isPending ? <RefreshCw size={16} className="animate-spin" /> : <Sparkles size={16} />}
                开始写作
              </button>
            </div>
          </section>

          <section className="rounded-2xl border border-pink-100 bg-white p-5">
            <h2 className="text-lg font-semibold text-gray-800">打开已有作品</h2>
            <p className="text-sm text-gray-500 mt-1">把历史故事转成编辑器工作区继续写。</p>

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
                onClick={handleOpenExistingStory}
                disabled={!selectedExistingStoryId || loadWorkspaceMutation.isPending}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-gray-800 text-white hover:bg-black disabled:opacity-60"
              >
                {loadWorkspaceMutation.isPending ? <RefreshCw size={16} className="animate-spin" /> : <FolderOpen size={16} />}
                打开并进入写作
              </button>
            </div>
          </section>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col">
      <div className="px-4 py-3 border-b border-pink-100 bg-white flex items-center gap-3">
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="flex-1 px-3 py-2 rounded-lg border border-gray-200 focus:outline-none focus:ring-2 focus:ring-pink-500"
        />
        <select
          value={aiModel}
          onChange={(e) => setAiModel(e.target.value as typeof defaultAiModel)}
          className="px-2 py-2 rounded-lg border border-gray-200 focus:outline-none focus:ring-2 focus:ring-pink-500"
        >
          {AI_MODEL_OPTIONS.map((item) => (
            <option key={item.value} value={item.value}>{item.label}</option>
          ))}
        </select>
        <button
          onClick={handleSaveContent}
          disabled={saveWorkspaceMutation.isPending}
          className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border border-gray-200 hover:bg-gray-50"
        >
          {saveWorkspaceMutation.isPending ? <RefreshCw size={16} className="animate-spin" /> : <Save size={16} />}
          保存正文
        </button>
        <button
          onClick={() => setCurrentWorkspace(null)}
          className="text-sm px-3 py-2 rounded-lg border border-gray-200 hover:bg-gray-50"
        >
          新建/切换
        </button>
      </div>

      <div className="flex-1 min-h-0 grid grid-cols-1 xl:grid-cols-[1fr_360px]">
        <section className="h-full p-4 flex flex-col gap-3">
          <div className="flex items-center gap-2">
            <input
              value={continueInstruction}
              onChange={(e) => setContinueInstruction(e.target.value)}
              placeholder="可选：告诉 AI 接下来想怎么发展"
              className="flex-1 px-3 py-2 rounded-lg border border-gray-200 focus:outline-none focus:ring-2 focus:ring-pink-500"
            />
            <button
              onClick={handleContinue}
              disabled={continueMutation.isPending || !editorContent.trim()}
              className="inline-flex items-center gap-2 px-3 py-2 rounded-lg bg-pink-500 text-white hover:bg-pink-600 disabled:opacity-60"
            >
              {continueMutation.isPending ? <RefreshCw size={16} className="animate-spin" /> : <Bot size={16} />}
              AI 续写
            </button>
            <button
              onClick={handleExtract}
              disabled={extractMutation.isPending || !editorContent.trim()}
              className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border border-gray-200 hover:bg-gray-50 disabled:opacity-60"
            >
              {extractMutation.isPending ? <RefreshCw size={16} className="animate-spin" /> : <Sparkles size={16} />}
              自动提炼
            </button>
            <label className="inline-flex items-center gap-1 text-xs text-gray-500 pl-1">
              <input
                type="checkbox"
                checked={debugMode}
                onChange={(e) => setDebugMode(e.target.checked)}
                className="rounded"
              />
              <Bug size={14} /> Debug
            </label>
          </div>

          <textarea
            value={editorContent}
            onChange={(e) => setEditorContent(e.target.value)}
            placeholder="开始写作...你可以一直写，也可以随时点 AI 续写。"
            className="flex-1 w-full px-4 py-4 rounded-xl border border-pink-100 focus:outline-none focus:ring-2 focus:ring-pink-500 leading-7 resize-none"
          />

          {debugInfo && (
            <div className="rounded-xl border border-gray-200 bg-white p-3 max-h-72 overflow-auto">
              <AIDebugPanel debugInfo={debugInfo} />
            </div>
          )}
        </section>

        <aside className="h-full border-l border-pink-100 bg-white p-4 overflow-y-auto space-y-5">
          <div className="flex items-center justify-between">
            <h3 className="font-semibold text-gray-800">自动提炼</h3>
            <button
              onClick={handleSaveExtraction}
              disabled={saveExtractionMutation.isPending}
              className="inline-flex items-center gap-1 text-sm px-2 py-1 rounded border border-gray-200 hover:bg-gray-50"
            >
              {saveExtractionMutation.isPending ? <RefreshCw size={14} className="animate-spin" /> : <Save size={14} />}
              保存修改
            </button>
          </div>

          <section className="space-y-2">
            <div className="flex items-center justify-between">
              <h4 className="text-sm font-medium text-gray-700">角色</h4>
              <button
                onClick={() => setExtractedCharacters((prev) => [...prev, emptyCharacter()])}
                className="text-xs inline-flex items-center gap-1 px-2 py-1 rounded border border-gray-200 hover:bg-gray-50"
              >
                <Plus size={12} /> 添加
              </button>
            </div>
            {extractedCharacters.map((item, index) => (
              <div key={`char-${index}`} className="rounded-lg border border-gray-100 p-2 space-y-1">
                <div className="flex gap-1">
                  <input
                    value={item.name}
                    onChange={(e) => {
                      const next = [...extractedCharacters];
                      next[index] = { ...next[index], name: e.target.value };
                      setExtractedCharacters(next);
                    }}
                    placeholder="角色名"
                    className="flex-1 px-2 py-1 text-sm rounded border border-gray-200"
                  />
                  <button
                    onClick={() => setExtractedCharacters((prev) => prev.filter((_, i) => i !== index))}
                    className="p-1 text-gray-400 hover:text-red-500"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
                <input
                  value={item.role_hint || ''}
                  onChange={(e) => {
                    const next = [...extractedCharacters];
                    next[index] = { ...next[index], role_hint: e.target.value };
                    setExtractedCharacters(next);
                  }}
                  placeholder="角色定位"
                  className="w-full px-2 py-1 text-sm rounded border border-gray-200"
                />
                <input
                  value={(item.traits || []).join('，')}
                  onChange={(e) => {
                    const next = [...extractedCharacters];
                    next[index] = { ...next[index], traits: splitTokens(e.target.value) };
                    setExtractedCharacters(next);
                  }}
                  placeholder="特征（逗号分隔）"
                  className="w-full px-2 py-1 text-sm rounded border border-gray-200"
                />
                <textarea
                  value={item.summary || ''}
                  onChange={(e) => {
                    const next = [...extractedCharacters];
                    next[index] = { ...next[index], summary: e.target.value };
                    setExtractedCharacters(next);
                  }}
                  placeholder="备注"
                  rows={2}
                  className="w-full px-2 py-1 text-sm rounded border border-gray-200"
                />
              </div>
            ))}
          </section>

          <section className="space-y-2">
            <div className="flex items-center justify-between">
              <h4 className="text-sm font-medium text-gray-700">场景</h4>
              <button
                onClick={() => setExtractedScenes((prev) => [...prev, emptyScene()])}
                className="text-xs inline-flex items-center gap-1 px-2 py-1 rounded border border-gray-200 hover:bg-gray-50"
              >
                <Plus size={12} /> 添加
              </button>
            </div>
            {extractedScenes.map((item, index) => (
              <div key={`scene-${index}`} className="rounded-lg border border-gray-100 p-2 space-y-1">
                <div className="flex gap-1">
                  <input
                    value={item.name}
                    onChange={(e) => {
                      const next = [...extractedScenes];
                      next[index] = { ...next[index], name: e.target.value };
                      setExtractedScenes(next);
                    }}
                    placeholder="场景名"
                    className="flex-1 px-2 py-1 text-sm rounded border border-gray-200"
                  />
                  <button
                    onClick={() => setExtractedScenes((prev) => prev.filter((_, i) => i !== index))}
                    className="p-1 text-gray-400 hover:text-red-500"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
                <input
                  value={item.description || ''}
                  onChange={(e) => {
                    const next = [...extractedScenes];
                    next[index] = { ...next[index], description: e.target.value };
                    setExtractedScenes(next);
                  }}
                  placeholder="场景描述"
                  className="w-full px-2 py-1 text-sm rounded border border-gray-200"
                />
                <input
                  value={(item.keywords || []).join('，')}
                  onChange={(e) => {
                    const next = [...extractedScenes];
                    next[index] = { ...next[index], keywords: splitTokens(e.target.value) };
                    setExtractedScenes(next);
                  }}
                  placeholder="关键词（逗号分隔）"
                  className="w-full px-2 py-1 text-sm rounded border border-gray-200"
                />
              </div>
            ))}
          </section>

          <section className="space-y-2">
            <div className="flex items-center justify-between">
              <h4 className="text-sm font-medium text-gray-700">情节大纲</h4>
              <button
                onClick={() => setExtractedOutlinePoints((prev) => [...prev, emptyOutlinePoint()])}
                className="text-xs inline-flex items-center gap-1 px-2 py-1 rounded border border-gray-200 hover:bg-gray-50"
              >
                <Plus size={12} /> 添加
              </button>
            </div>
            {extractedOutlinePoints.map((item, index) => (
              <div key={`outline-${index}`} className="rounded-lg border border-gray-100 p-2 space-y-1">
                <div className="flex gap-1">
                  <input
                    value={item.stage}
                    onChange={(e) => {
                      const next = [...extractedOutlinePoints];
                      next[index] = { ...next[index], stage: e.target.value };
                      setExtractedOutlinePoints(next);
                    }}
                    placeholder="阶段"
                    className="w-28 px-2 py-1 text-sm rounded border border-gray-200"
                  />
                  <button
                    onClick={() => setExtractedOutlinePoints((prev) => prev.filter((_, i) => i !== index))}
                    className="p-1 text-gray-400 hover:text-red-500"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
                <textarea
                  value={item.summary}
                  onChange={(e) => {
                    const next = [...extractedOutlinePoints];
                    next[index] = { ...next[index], summary: e.target.value };
                    setExtractedOutlinePoints(next);
                  }}
                  placeholder="该阶段情节"
                  rows={2}
                  className="w-full px-2 py-1 text-sm rounded border border-gray-200"
                />
              </div>
            ))}
          </section>

          <section className="space-y-2">
            <h4 className="text-sm font-medium text-gray-700">一致性提醒</h4>
            <textarea
              value={extractionNotesText}
              onChange={(e) => setExtractionNotesText(e.target.value)}
              placeholder="每行一条提醒"
              rows={5}
              className="w-full px-2 py-1 text-sm rounded border border-gray-200"
            />
          </section>
        </aside>
      </div>
    </div>
  );
}
