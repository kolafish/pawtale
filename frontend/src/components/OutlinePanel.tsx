import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Plus, FileText, Trash2, Pencil, Users, MapPin, Play, ChevronDown, ChevronRight,
  X, Check, Loader2, Sparkles, LayoutTemplate, Wand2
} from 'lucide-react';
import { outlinesApi, charactersApi, scenesApi, storiesApi } from '../api';
import { useAppStore } from '../stores/appStore';
import { OutlineGuidedWizard } from './OutlineGuidedWizard';
import type { StoryOutline, Character, Scene, OutlineTemplate } from '../types';
import { AI_MODEL_OPTIONS } from '../constants/aiModels';

const MOOD_OPTIONS = ['甜', '虐', '搞笑', '治愈'];
const STYLE_OPTIONS = ['细腻', '白描', '诗意', '轻松'];
const LENGTH_OPTIONS = ['短', '中', '长'];
const ENDING_OPTIONS = ['HE', 'BE', 'OE'];
const ROLE_TYPE_OPTIONS = [
  { value: 'protagonist', label: '主角' },
  { value: 'deuteragonist', label: '第二主角' },
  { value: 'supporting', label: '配角' },
  { value: 'antagonist', label: '反派' },
];

export function OutlinePanel() {
  const queryClient = useQueryClient();
  const { setCurrentStory, setCurrentNode, defaultAiModel } = useAppStore();
  const [isCreating, setIsCreating] = useState(false);
  const [showGuidedWizard, setShowGuidedWizard] = useState(false);
  const [editingOutline, setEditingOutline] = useState<StoryOutline | null>(null);
  const [expandedOutlineId, setExpandedOutlineId] = useState<string | null>(null);
  const [showTemplates, setShowTemplates] = useState(false);

  const { data: outlines = [], isLoading } = useQuery({
    queryKey: ['outlines'],
    queryFn: outlinesApi.getAll,
  });

  const { data: characters = [] } = useQuery({
    queryKey: ['characters'],
    queryFn: charactersApi.getAll,
  });

  const { data: scenes = [] } = useQuery({
    queryKey: ['scenes'],
    queryFn: scenesApi.getAll,
  });

  const { data: templatesData } = useQuery({
    queryKey: ['outline-templates'],
    queryFn: outlinesApi.getTemplates,
  });

  const templates = templatesData?.templates || [];

  const createMutation = useMutation({
    mutationFn: outlinesApi.create,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['outlines'] });
      setIsCreating(false);
      setShowTemplates(false);
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<StoryOutline> }) =>
      outlinesApi.update(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['outlines'] });
      setEditingOutline(null);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: outlinesApi.delete,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['outlines'] });
    },
  });

  const generateMutation = useMutation({
    mutationFn: ({ outlineId, request }: { outlineId: string; request: { custom_direction?: string; ai_model?: string } }) =>
      outlinesApi.generateStory(outlineId, request),
    onSuccess: async (data) => {
      setCurrentNode(data);
      const story = await storiesApi.getById(data.story_id);
      setCurrentStory(story);
      queryClient.invalidateQueries({ queryKey: ['stories'] });
    },
  });

  const handleUseTemplate = (template: OutlineTemplate) => {
    createMutation.mutate({
      title: template.name,
      mood: template.mood,
      writing_style: template.writing_style,
      central_conflict: template.central_conflict,
      ending_type: template.ending_type,
      plot_hints: template.plot_hints,
      ai_model: defaultAiModel,
    });
  };

  const toggleExpand = (outlineId: string) => {
    setExpandedOutlineId(expandedOutlineId === outlineId ? null : outlineId);
  };

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center justify-between p-4 border-b border-pink-100">
        <div>
          <h2 className="text-lg font-semibold text-gray-800">故事大纲</h2>
          <p className="text-sm text-gray-500 mt-1">管理角色、场景和故事配置</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setShowTemplates(!showTemplates)}
            className="flex items-center gap-1 px-3 py-1.5 border border-pink-300 text-pink-600 rounded-lg hover:bg-pink-50 transition-colors text-sm"
          >
            <LayoutTemplate size={16} />
            模板
          </button>
          <button
            onClick={() => setShowGuidedWizard(true)}
            className="flex items-center gap-1 px-3 py-1.5 bg-gradient-to-r from-pink-500 to-purple-500 text-white rounded-lg hover:from-pink-600 hover:to-purple-600 transition-colors text-sm"
          >
            <Wand2 size={16} />
            引导创建
          </button>
          <button
            onClick={() => setIsCreating(true)}
            className="flex items-center gap-1 px-3 py-1.5 bg-pink-500 text-white rounded-lg hover:bg-pink-600 transition-colors text-sm"
          >
            <Plus size={16} />
            新建大纲
          </button>
        </div>
      </div>

      {/* 模板选择区 */}
      {showTemplates && (
        <div className="p-4 border-b border-pink-100 bg-pink-50/50">
          <p className="text-sm font-medium text-gray-700 mb-3">选择模板快速开始</p>
          <div className="grid grid-cols-1 gap-2">
            {templates.map((template) => (
              <div
                key={template.id}
                className="flex items-center justify-between p-3 bg-white rounded-lg border border-gray-100 hover:border-pink-300 transition-colors cursor-pointer"
                onClick={() => handleUseTemplate(template)}
              >
                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-gray-800">{template.name}</span>
                    <span className="px-2 py-0.5 text-xs bg-pink-100 text-pink-600 rounded-full">
                      {template.mood}
                    </span>
                    <span className="px-2 py-0.5 text-xs bg-purple-100 text-purple-600 rounded-full">
                      {template.ending_type}
                    </span>
                  </div>
                  <p className="text-sm text-gray-500 mt-1">{template.description}</p>
                </div>
                <Plus size={18} className="text-gray-400" />
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {isCreating && (
          <OutlineForm
            onSubmit={(data) => createMutation.mutate(data as StoryOutline)}
            onCancel={() => setIsCreating(false)}
            isLoading={createMutation.isPending}
            defaultAiModel={defaultAiModel}
          />
        )}

        {isLoading ? (
          <div className="text-center text-gray-500 py-8">加载中...</div>
        ) : outlines.length === 0 && !isCreating ? (
          <div className="text-center text-gray-500 py-8">
            <FileText size={48} className="mx-auto mb-2 opacity-50" />
            <p>还没有大纲，创建一个吧</p>
          </div>
        ) : (
          outlines.map((outline) => (
            editingOutline?.id === outline.id ? (
              <OutlineForm
                key={outline.id}
                initialData={outline}
                onSubmit={(data) => updateMutation.mutate({ id: outline.id, data })}
                onCancel={() => setEditingOutline(null)}
                isLoading={updateMutation.isPending}
                isEditing
                defaultAiModel={defaultAiModel}
              />
            ) : (
              <OutlineCard
                key={outline.id}
                outline={outline}
                isExpanded={expandedOutlineId === outline.id}
                onToggleExpand={() => toggleExpand(outline.id)}
                onEdit={() => setEditingOutline(outline)}
                onDelete={() => {
                  if (confirm('确定要删除这个大纲吗？')) {
                    deleteMutation.mutate(outline.id);
                  }
                }}
                onGenerate={(aiModel) => generateMutation.mutate({ outlineId: outline.id, request: { ai_model: aiModel } })}
                isGenerating={generateMutation.isPending}
                characters={characters}
                scenes={scenes}
                defaultAiModel={defaultAiModel}
              />
            )
          ))
        )}
      </div>

      {/* 引导式创建向导 */}
      {showGuidedWizard && (
        <OutlineGuidedWizard
          onClose={() => setShowGuidedWizard(false)}
          onSuccess={(outlineId) => {
            setExpandedOutlineId(outlineId);
          }}
        />
      )}
    </div>
  );
}

interface OutlineCardProps {
  outline: StoryOutline;
  isExpanded: boolean;
  onToggleExpand: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onGenerate: (aiModel?: string) => void;
  isGenerating: boolean;
  characters: Character[];
  scenes: Scene[];
  defaultAiModel: 'deepseek' | 'claude-opus-4' | 'claude-sonnet-4';
}

function OutlineCard({
  outline,
  isExpanded,
  onToggleExpand,
  onEdit,
  onDelete,
  onGenerate,
  isGenerating,
  characters,
  scenes,
  defaultAiModel,
}: OutlineCardProps) {
  const queryClient = useQueryClient();
  const [actionAiModel, setActionAiModel] = useState('');
  const resolvedAiModel = actionAiModel || outline.ai_model || defaultAiModel;

  const addCharacterMutation = useMutation({
    mutationFn: (data: { character_id: string; role_type: string }) =>
      outlinesApi.addCharacter(outline.id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['outlines'] });
    },
  });

  const removeCharacterMutation = useMutation({
    mutationFn: (characterId: string) =>
      outlinesApi.removeCharacter(outline.id, characterId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['outlines'] });
    },
  });

  const addSceneMutation = useMutation({
    mutationFn: (data: { scene_id: string; is_starting_scene?: boolean }) =>
      outlinesApi.addScene(outline.id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['outlines'] });
    },
  });

  const removeSceneMutation = useMutation({
    mutationFn: (sceneId: string) =>
      outlinesApi.removeScene(outline.id, sceneId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['outlines'] });
    },
  });

  const updateSceneMutation = useMutation({
    mutationFn: ({ sceneId, data }: { sceneId: string; data: { is_starting_scene: boolean } }) =>
      outlinesApi.updateScene(outline.id, sceneId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['outlines'] });
    },
  });

  const aiEnhanceMutation = useMutation({
    mutationFn: () => outlinesApi.aiEnhance(outline.id, { ai_model: resolvedAiModel }),
    onSuccess: (data) => {
      // 更新大纲
      outlinesApi.update(outline.id, {
        central_conflict: data.central_conflict,
        plot_hints: data.plot_hints,
        mood: data.suggested_mood || outline.mood,
        ending_type: data.suggested_ending || outline.ending_type,
      }).then(() => {
        queryClient.invalidateQueries({ queryKey: ['outlines'] });
      });
    },
  });

  const availableCharacters = characters.filter(
    c => !outline.outline_characters.some(oc => oc.character_id === c.id)
  );

  const availableScenes = scenes.filter(
    s => !outline.outline_scenes.some(os => os.scene_id === s.id)
  );

  const canGenerate = outline.outline_characters.length > 0 && outline.outline_scenes.length > 0;

  return (
    <div className="bg-white rounded-xl border border-gray-100 overflow-hidden">
      {/* 头部 */}
      <div
        className="p-4 cursor-pointer hover:bg-gray-50 transition-colors"
        onClick={onToggleExpand}
      >
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-2">
            {isExpanded ? <ChevronDown size={20} /> : <ChevronRight size={20} />}
            <div>
              <h3 className="font-semibold text-gray-800">{outline.title}</h3>
              <div className="flex items-center gap-2 mt-1 text-sm text-gray-500">
                <span className="flex items-center gap-1">
                  <Users size={14} />
                  {outline.outline_characters.length} 角色
                </span>
                <span className="flex items-center gap-1">
                  <MapPin size={14} />
                  {outline.outline_scenes.length} 场景
                </span>
                {outline.mood && (
                  <span className="px-2 py-0.5 bg-pink-100 text-pink-600 rounded-full text-xs">
                    {outline.mood}
                  </span>
                )}
                {outline.ending_type && (
                  <span className="px-2 py-0.5 bg-purple-100 text-purple-600 rounded-full text-xs">
                    {outline.ending_type}
                  </span>
                )}
                {outline.ai_model && (
                  <span className="px-2 py-0.5 bg-blue-100 text-blue-600 rounded-full text-xs">
                    {AI_MODEL_OPTIONS.find((m) => m.value === outline.ai_model)?.label || outline.ai_model}
                  </span>
                )}
              </div>
            </div>
          </div>
          <div className="flex gap-1" onClick={(e) => e.stopPropagation()}>
            <button
              onClick={() => aiEnhanceMutation.mutate()}
              disabled={aiEnhanceMutation.isPending}
              className="p-1.5 text-gray-400 hover:text-purple-500 hover:bg-purple-50 rounded-lg transition-colors disabled:opacity-50"
              title="AI 生成核心冲突和剧情提示"
            >
              {aiEnhanceMutation.isPending ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />}
            </button>
            <button
              onClick={() => onGenerate(resolvedAiModel)}
              disabled={isGenerating || !canGenerate}
              className="p-1.5 text-gray-400 hover:text-green-500 hover:bg-green-50 rounded-lg transition-colors disabled:opacity-50"
              title={canGenerate ? "生成故事" : "请先添加角色和场景"}
            >
              {isGenerating ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />}
            </button>
            <button
              onClick={onEdit}
              className="p-1.5 text-gray-400 hover:text-blue-500 hover:bg-blue-50 rounded-lg transition-colors"
              title="编辑"
            >
              <Pencil size={16} />
            </button>
            <button
              onClick={onDelete}
              className="p-1.5 text-gray-400 hover:text-red-500 hover:bg-red-50 rounded-lg transition-colors"
              title="删除"
            >
              <Trash2 size={16} />
            </button>
          </div>
        </div>
        <div className="mt-2 pl-7 pr-2" onClick={(e) => e.stopPropagation()}>
          <select
            value={actionAiModel}
            onChange={(e) => setActionAiModel(e.target.value)}
            className="text-xs px-2 py-1 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500"
            title="本大纲 AI 动作模型（留空=按大纲/全局）"
          >
            <option value="">跟随大纲/全局</option>
            {AI_MODEL_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </div>
      </div>

      {/* 展开内容 */}
      {isExpanded && (
        <div className="border-t border-gray-100 p-4 space-y-4">
          {/* 核心冲突 */}
          {outline.central_conflict && (
            <div className="bg-gray-50 rounded-lg p-3">
              <p className="text-sm font-medium text-gray-600 mb-1">核心冲突</p>
              <p className="text-sm text-gray-800">{outline.central_conflict}</p>
            </div>
          )}

          {/* 角色列表 */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <p className="text-sm font-medium text-gray-600">角色配置</p>
              {availableCharacters.length > 0 && (
                <select
                  className="text-sm border border-gray-200 rounded-lg px-2 py-1"
                  onChange={(e) => {
                    if (e.target.value) {
                      addCharacterMutation.mutate({
                        character_id: e.target.value,
                        role_type: 'supporting',
                      });
                      e.target.value = '';
                    }
                  }}
                  defaultValue=""
                >
                  <option value="">+ 添加角色</option>
                  {availableCharacters.map(c => (
                    <option key={c.id} value={c.id}>{c.name}</option>
                  ))}
                </select>
              )}
            </div>
            <div className="space-y-2">
              {outline.outline_characters.map((oc) => (
                <div key={oc.id} className="flex items-center justify-between bg-gray-50 rounded-lg p-2">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-gray-800">
                      {oc.character?.name || oc.story_name}
                    </span>
                    <span className={`px-2 py-0.5 text-xs rounded-full ${
                      oc.role_type === 'protagonist' ? 'bg-pink-100 text-pink-600' :
                      oc.role_type === 'deuteragonist' ? 'bg-purple-100 text-purple-600' :
                      oc.role_type === 'antagonist' ? 'bg-red-100 text-red-600' :
                      'bg-gray-100 text-gray-600'
                    }`}>
                      {ROLE_TYPE_OPTIONS.find(r => r.value === oc.role_type)?.label || oc.role_type}
                    </span>
                  </div>
                  <button
                    onClick={() => removeCharacterMutation.mutate(oc.character_id)}
                    className="p-1 text-gray-400 hover:text-red-500 rounded"
                  >
                    <X size={14} />
                  </button>
                </div>
              ))}
              {outline.outline_characters.length === 0 && (
                <p className="text-sm text-gray-400 text-center py-2">暂无角色</p>
              )}
            </div>
          </div>

          {/* 场景列表 */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <p className="text-sm font-medium text-gray-600">场景配置</p>
              {availableScenes.length > 0 && (
                <select
                  className="text-sm border border-gray-200 rounded-lg px-2 py-1"
                  onChange={(e) => {
                    if (e.target.value) {
                      addSceneMutation.mutate({
                        scene_id: e.target.value,
                        is_starting_scene: outline.outline_scenes.length === 0,
                      });
                      e.target.value = '';
                    }
                  }}
                  defaultValue=""
                >
                  <option value="">+ 添加场景</option>
                  {availableScenes.map(s => (
                    <option key={s.id} value={s.id}>{s.name}</option>
                  ))}
                </select>
              )}
            </div>
            <div className="space-y-2">
              {outline.outline_scenes.map((os) => (
                <div key={os.id} className="flex items-center justify-between bg-gray-50 rounded-lg p-2">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-gray-800">
                      {os.scene?.name}
                    </span>
                    {os.is_starting_scene && (
                      <span className="px-2 py-0.5 text-xs bg-green-100 text-green-600 rounded-full">
                        起始
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-1">
                    {!os.is_starting_scene && (
                      <button
                        onClick={() => updateSceneMutation.mutate({
                          sceneId: os.scene_id,
                          data: { is_starting_scene: true },
                        })}
                        className="p-1 text-gray-400 hover:text-green-500 rounded text-xs"
                        title="设为起始场景"
                      >
                        <Check size={14} />
                      </button>
                    )}
                    <button
                      onClick={() => removeSceneMutation.mutate(os.scene_id)}
                      className="p-1 text-gray-400 hover:text-red-500 rounded"
                    >
                      <X size={14} />
                    </button>
                  </div>
                </div>
              ))}
              {outline.outline_scenes.length === 0 && (
                <p className="text-sm text-gray-400 text-center py-2">暂无场景</p>
              )}
            </div>
          </div>

          {/* 剧情提示 */}
          {outline.plot_hints.length > 0 && (
            <div>
              <p className="text-sm font-medium text-gray-600 mb-2">剧情提示</p>
              <div className="flex flex-wrap gap-2">
                {outline.plot_hints.map((hint, i) => (
                  <span key={i} className="px-2 py-1 text-xs bg-blue-50 text-blue-600 rounded-full">
                    {hint}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

interface OutlineFormProps {
  onSubmit: (data: Partial<StoryOutline>) => void;
  onCancel: () => void;
  isLoading: boolean;
  initialData?: StoryOutline;
  isEditing?: boolean;
  defaultAiModel: 'deepseek' | 'claude-opus-4' | 'claude-sonnet-4';
}

function OutlineForm({ onSubmit, onCancel, isLoading, initialData, isEditing, defaultAiModel }: OutlineFormProps) {
  const [formData, setFormData] = useState({
    title: initialData?.title || '',
    mood: initialData?.mood || '',
    writing_style: initialData?.writing_style || '',
    chapter_length: initialData?.chapter_length || '',
    central_conflict: initialData?.central_conflict || '',
    ending_type: initialData?.ending_type || '',
    plot_hints: (initialData?.plot_hints || []).join('，'),
    forbidden_elements: (initialData?.forbidden_elements || []).join('，'),
    ai_model: initialData?.ai_model || defaultAiModel,
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const plotHints = formData.plot_hints
      .split(/[，,]/)
      .map(s => s.trim())
      .filter(Boolean);
    const forbiddenElements = formData.forbidden_elements
      .split(/[，,]/)
      .map(s => s.trim())
      .filter(Boolean);

    onSubmit({
      title: formData.title.trim(),
      mood: formData.mood || undefined,
      writing_style: formData.writing_style || undefined,
      chapter_length: formData.chapter_length || undefined,
      central_conflict: formData.central_conflict.trim() || undefined,
      ending_type: formData.ending_type || undefined,
      plot_hints: plotHints,
      forbidden_elements: forbiddenElements,
      ai_model: formData.ai_model || undefined,
    });
  };

  return (
    <form onSubmit={handleSubmit} className="p-4 bg-white rounded-xl border border-pink-200 space-y-4">
      {isEditing && (
        <div className="text-sm text-pink-600 font-medium">编辑大纲</div>
      )}

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">大纲标题 *</label>
        <input
          type="text"
          value={formData.title}
          onChange={(e) => setFormData({ ...formData, title: e.target.value })}
          placeholder="如：陆辰与林晚的故事"
          className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500"
          required
        />
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">情绪基调</label>
          <select
            value={formData.mood}
            onChange={(e) => setFormData({ ...formData, mood: e.target.value })}
            className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500"
          >
            <option value="">选择...</option>
            {MOOD_OPTIONS.map(m => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">结局类型</label>
          <select
            value={formData.ending_type}
            onChange={(e) => setFormData({ ...formData, ending_type: e.target.value })}
            className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500"
          >
            <option value="">选择...</option>
            {ENDING_OPTIONS.map(e => (
              <option key={e} value={e}>{e}</option>
            ))}
          </select>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">文字风格</label>
          <select
            value={formData.writing_style}
            onChange={(e) => setFormData({ ...formData, writing_style: e.target.value })}
            className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500"
          >
            <option value="">选择...</option>
            {STYLE_OPTIONS.map(s => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">章节长度</label>
          <select
            value={formData.chapter_length}
            onChange={(e) => setFormData({ ...formData, chapter_length: e.target.value })}
            className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500"
          >
            <option value="">选择...</option>
            {LENGTH_OPTIONS.map(l => (
              <option key={l} value={l}>{l}</option>
            ))}
          </select>
        </div>
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">默认 AI 模型</label>
        <select
          value={formData.ai_model}
          onChange={(e) => setFormData({ ...formData, ai_model: e.target.value })}
          className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500"
        >
          {AI_MODEL_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>{option.label}</option>
          ))}
        </select>
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">核心冲突</label>
        <textarea
          value={formData.central_conflict}
          onChange={(e) => setFormData({ ...formData, central_conflict: e.target.value })}
          placeholder="如：身份悬殊、家族恩怨、误会重重..."
          className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500"
          rows={2}
        />
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">剧情提示</label>
        <input
          type="text"
          value={formData.plot_hints}
          onChange={(e) => setFormData({ ...formData, plot_hints: e.target.value })}
          placeholder="如：先婚后爱，日久生情，欢喜冤家"
          className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500"
        />
        <p className="text-xs text-gray-400 mt-1">使用逗号分隔多个提示</p>
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">禁止元素</label>
        <input
          type="text"
          value={formData.forbidden_elements}
          onChange={(e) => setFormData({ ...formData, forbidden_elements: e.target.value })}
          placeholder="如：出轨，狗血，三角恋"
          className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500"
        />
        <p className="text-xs text-gray-400 mt-1">AI 生成时会避免这些元素</p>
      </div>

      <div className="flex gap-2 pt-2">
        <button
          type="submit"
          disabled={isLoading || !formData.title.trim()}
          className="flex-1 py-2 bg-pink-500 text-white rounded-lg hover:bg-pink-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {isLoading ? '保存中...' : isEditing ? '更新' : '保存'}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="px-4 py-2 border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors"
        >
          取消
        </button>
      </div>
    </form>
  );
}

// 在OutlinePanel的return末尾添加引导式向导
// 找到OutlinePanel函数的return语句末尾，在最后的</div>之前添加：
// {showGuidedWizard && (
//   <OutlineGuidedWizard
//     onClose={() => setShowGuidedWizard(false)}
//     onSuccess={(outlineId) => {
//       setExpandedOutlineId(outlineId);
//     }}
//   />
// )}
