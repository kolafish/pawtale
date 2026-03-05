import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { MapPin, Check, Plus, Pencil, Trash2, Wand2, Loader2, X } from 'lucide-react';
import { scenesApi } from '../api';
import { useAppStore } from '../stores/appStore';
import type { Scene, SceneCandidate, AIDebugInfo } from '../types';
import { AI_MODEL_OPTIONS } from '../constants/aiModels';
import { AIDebugPanel } from './AIDebugPanel';

const CATEGORY_COLORS: Record<string, string> = {
  '初遇': 'bg-blue-100 text-blue-600',
  '暧昧': 'bg-purple-100 text-purple-600',
  '表白': 'bg-pink-100 text-pink-600',
  '甜蜜': 'bg-orange-100 text-orange-600',
  '虐心': 'bg-gray-100 text-gray-600',
};

export function ScenePanel() {
  const queryClient = useQueryClient();
  const { selectedScene, setSelectedScene, defaultAiModel } = useAppStore();
  const [isCreating, setIsCreating] = useState(false);
  const [editingScene, setEditingScene] = useState<Scene | null>(null);
  const [showAIGenerator, setShowAIGenerator] = useState(false);

  const { data: scenes = [], isLoading } = useQuery({
    queryKey: ['scenes'],
    queryFn: scenesApi.getAll,
  });

  const createMutation = useMutation({
    mutationFn: scenesApi.create,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['scenes'] });
      setIsCreating(false);
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<Scene> }) =>
      scenesApi.update(id, data),
    onSuccess: (scene) => {
      queryClient.invalidateQueries({ queryKey: ['scenes'] });
      setEditingScene(null);
      if (selectedScene?.id === scene.id) {
        setSelectedScene(scene);
      }
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (sceneId: string) => scenesApi.delete(sceneId),
    onSuccess: (_data, sceneId) => {
      queryClient.invalidateQueries({ queryKey: ['scenes'] });
      if (selectedScene?.id === sceneId) {
        setSelectedScene(null);
      }
    },
  });

  // 初始化预设场景，如果场景为空则自动加载
  useEffect(() => {
    if (!isLoading && scenes.length === 0) {
      scenesApi.initPresets().then(() => {
        queryClient.invalidateQueries({ queryKey: ['scenes'] });
      }).catch(() => {});
    }
  }, [isLoading, scenes.length, queryClient]);

  // 按分类分组
  const groupedScenes = scenes.reduce((acc, scene) => {
    if (!acc[scene.category]) {
      acc[scene.category] = [];
    }
    acc[scene.category].push(scene);
    return acc;
  }, {} as Record<string, typeof scenes>);

  const categories = ['初遇', '暧昧', '表白', '甜蜜', '虐心'];

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center justify-between p-4 border-b border-pink-100">
        <div>
          <h2 className="text-lg font-semibold text-gray-800">场景管理</h2>
          <p className="text-sm text-gray-500 mt-1">选择一个场景开始创作</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setShowAIGenerator(true)}
            className="flex items-center gap-1 px-3 py-1.5 bg-gradient-to-r from-purple-500 to-pink-500 text-white rounded-lg hover:from-purple-600 hover:to-pink-600 transition-colors text-sm"
          >
            <Wand2 size={16} />
            AI 生成
          </button>
          <button
            onClick={() => setIsCreating(true)}
            className="flex items-center gap-1 px-3 py-1.5 bg-pink-500 text-white rounded-lg hover:bg-pink-600 transition-colors text-sm"
          >
            <Plus size={16} />
            新建场景
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {showAIGenerator && (
          <SceneAIGenerator
            onSave={(candidate) => {
              createMutation.mutate({
                name: candidate.name,
                category: candidate.category,
                description: candidate.description,
                location: candidate.location,
                time_weather: candidate.time_weather,
                mood_keywords: candidate.mood_keywords,
                dramatic_potential: candidate.dramatic_potential,
                sensory_details: candidate.sensory_details,
                props: candidate.props,
              });
              setShowAIGenerator(false);
            }}
            onClose={() => setShowAIGenerator(false)}
            defaultAiModel={defaultAiModel}
          />
        )}
        {isCreating && (
          <SceneForm
            onSubmit={(data) => createMutation.mutate(data)}
            onCancel={() => setIsCreating(false)}
            isLoading={createMutation.isPending}
          />
        )}
        {isLoading ? (
          <div className="text-center text-gray-500 py-8">加载中...</div>
        ) : scenes.length === 0 && !isCreating ? (
          <div className="text-center text-gray-500 py-8">
            <MapPin size={48} className="mx-auto mb-2 opacity-50" />
            <p>还没有场景，创建一个吧</p>
          </div>
        ) : (
          <div className="space-y-6">
            {categories.map((category) => {
              const categoryScenes = groupedScenes[category] || [];
              if (categoryScenes.length === 0) return null;

              return (
                <div key={category}>
                  <h3 className="text-sm font-medium text-gray-500 mb-3 flex items-center gap-2">
                    <span className={`px-2 py-0.5 rounded-full text-xs ${CATEGORY_COLORS[category]}`}>
                      {category}
                    </span>
                  </h3>
                  <div className="space-y-2">
                    {categoryScenes.map((scene) => (
                      editingScene?.id === scene.id ? (
                        <SceneForm
                          key={scene.id}
                          initialData={scene}
                          onSubmit={(data) => updateMutation.mutate({ id: scene.id, data })}
                          onCancel={() => setEditingScene(null)}
                          isLoading={updateMutation.isPending}
                          isEditing
                        />
                      ) : (
                        <SceneCard
                          key={scene.id}
                          scene={scene}
                          isSelected={selectedScene?.id === scene.id}
                          onSelect={() => setSelectedScene(scene)}
                          onEdit={() => setEditingScene(scene)}
                          onDelete={() => {
                            if (confirm('确定要删除这个场景吗？')) {
                              deleteMutation.mutate(scene.id);
                            }
                          }}
                        />
                      )
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {selectedScene && (
        <div className="p-4 border-t border-pink-100 bg-pink-50">
          <p className="text-sm text-gray-600">
            已选择：<span className="font-medium">{selectedScene.name}</span>
          </p>
        </div>
      )}
    </div>
  );
}

interface SceneCardProps {
  scene: Scene;
  isSelected: boolean;
  onSelect: () => void;
  onEdit: () => void;
  onDelete: () => void;
}

function SceneCard({ scene, isSelected, onSelect, onEdit, onDelete }: SceneCardProps) {
  return (
    <div
      onClick={onSelect}
      className={`p-4 rounded-xl border-2 cursor-pointer transition-all ${
        isSelected
          ? 'border-pink-500 bg-pink-50'
          : 'border-gray-100 bg-white hover:border-pink-200'
      }`}
    >
      <div className="flex items-start justify-between">
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <h4 className="font-medium text-gray-800">{scene.name}</h4>
            {isSelected && <Check size={16} className="text-pink-500" />}
          </div>
          <p className="text-sm text-gray-500 mt-1 line-clamp-2">
            {scene.description}
          </p>
          {scene.location && (
            <div className="flex items-center gap-1 mt-2 text-xs text-gray-400">
              <MapPin size={12} />
              {scene.location}
              {scene.time_weather && ` · ${scene.time_weather}`}
            </div>
          )}
        </div>
        <div className="flex gap-1" onClick={(e) => e.stopPropagation()}>
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
    </div>
  );
}

interface SceneFormProps {
  onSubmit: (data: Partial<Scene>) => void;
  onCancel: () => void;
  isLoading: boolean;
  initialData?: Scene;
  isEditing?: boolean;
}

function SceneForm({ onSubmit, onCancel, isLoading, initialData, isEditing }: SceneFormProps) {
  const [formData, setFormData] = useState({
    name: initialData?.name || '',
    category: initialData?.category || '初遇',
    description: initialData?.description || '',
    location: initialData?.location || '',
    time_weather: initialData?.time_weather || '',
    mood_keywords: (initialData?.mood_keywords || []).join('，'),
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const moodKeywords = formData.mood_keywords
      .split(/[，,]/)
      .map((item) => item.trim())
      .filter(Boolean);

    onSubmit({
      name: formData.name.trim(),
      category: formData.category,
      description: formData.description.trim(),
      location: formData.location.trim() || undefined,
      time_weather: formData.time_weather.trim() || undefined,
      mood_keywords: moodKeywords,
    });
  };

  return (
    <form onSubmit={handleSubmit} className="p-4 bg-white rounded-xl border border-pink-200 space-y-4 mb-4">
      {isEditing && (
        <div className="text-sm text-pink-600 font-medium">编辑场景</div>
      )}

      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">场景名 *</label>
          <input
            type="text"
            value={formData.name}
            onChange={(e) => setFormData({ ...formData, name: e.target.value })}
            className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500"
            required
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">分类 *</label>
          <select
            value={formData.category}
            onChange={(e) => setFormData({ ...formData, category: e.target.value })}
            className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500"
            required
          >
            <option value="初遇">初遇</option>
            <option value="暧昧">暧昧</option>
            <option value="表白">表白</option>
            <option value="甜蜜">甜蜜</option>
            <option value="虐心">虐心</option>
          </select>
        </div>
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">场景描述 *</label>
        <textarea
          value={formData.description}
          onChange={(e) => setFormData({ ...formData, description: e.target.value })}
          className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500"
          rows={2}
          required
        />
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">地点</label>
          <input
            type="text"
            value={formData.location}
            onChange={(e) => setFormData({ ...formData, location: e.target.value })}
            className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">时间/天气</label>
          <input
            type="text"
            value={formData.time_weather}
            onChange={(e) => setFormData({ ...formData, time_weather: e.target.value })}
            className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500"
          />
        </div>
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">氛围关键词</label>
        <input
          type="text"
          value={formData.mood_keywords}
          onChange={(e) => setFormData({ ...formData, mood_keywords: e.target.value })}
          placeholder="如：浪漫，心动，雨天"
          className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500"
        />
        <p className="text-xs text-gray-400 mt-1">使用逗号分隔多个关键词</p>
      </div>

      <div className="flex gap-2 pt-2">
        <button
          type="submit"
          disabled={isLoading || !formData.name.trim() || !formData.description.trim()}
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

// AI 场景生成器组件
interface SceneAIGeneratorProps {
  onSave: (candidate: SceneCandidate & { category: string }) => void;
  onClose: () => void;
  defaultAiModel: 'deepseek' | 'claude-opus-4' | 'claude-sonnet-4';
}

function SceneAIGenerator({ onSave, onClose, defaultAiModel }: SceneAIGeneratorProps) {
  const [category, setCategory] = useState('初遇');
  const [moodKeywords, setMoodKeywords] = useState('');
  const [customPrompt, setCustomPrompt] = useState('');
  const [aiModelOverride, setAiModelOverride] = useState('');
  const [candidates, setCandidates] = useState<SceneCandidate[]>([]);
  const [selectedCandidate, setSelectedCandidate] = useState<SceneCandidate | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [debugEnabled, setDebugEnabled] = useState(false);
  const [debugInfo, setDebugInfo] = useState<AIDebugInfo | null>(null);

  const generateMutation = useMutation({
    mutationFn: () => scenesApi.aiGenerate({
      category,
      mood_keywords: moodKeywords.split(/[，,]/).map(s => s.trim()).filter(Boolean),
      custom_prompt: customPrompt || undefined,
      count: 3,
      ai_model: aiModelOverride || defaultAiModel,
      debug: debugEnabled,
    }),
    onMutate: () => {
      setErrorMessage(null);
      if (!debugEnabled) {
        setDebugInfo(null);
      }
    },
    onSuccess: (data) => {
      setCandidates(data.candidates);
      setErrorMessage(null);
      setDebugInfo(data.debug_info || null);
    },
    onError: (error: unknown) => {
      const detail = (error as any)?.response?.data?.detail;
      const fallback = (error as Error)?.message || '场景生成失败，请稍后重试';
      if (detail && typeof detail === 'object') {
        setErrorMessage((detail as any).message || fallback);
        setDebugInfo(((detail as any).debug_info as AIDebugInfo) || null);
      } else {
        setErrorMessage(typeof detail === 'string' && detail ? detail : fallback);
        setDebugInfo(null);
      }
      setCandidates([]);
      setSelectedCandidate(null);
    },
  });

  const handleSave = () => {
    if (selectedCandidate) {
      onSave({ ...selectedCandidate, category });
    }
  };

  return (
    <div className="p-4 bg-white rounded-xl border border-purple-200 space-y-4 mb-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium text-purple-600">AI 生成场景</h3>
        <button onClick={onClose} className="p-1 text-gray-400 hover:text-gray-600 rounded">
          <X size={16} />
        </button>
      </div>

      {candidates.length === 0 ? (
        <>
          {errorMessage && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600">
              {errorMessage}
            </div>
          )}

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">场景类型</label>
              <select
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-purple-500"
              >
                <option value="初遇">初遇</option>
                <option value="暧昧">暧昧</option>
                <option value="表白">表白</option>
                <option value="甜蜜">甜蜜</option>
                <option value="虐心">虐心</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">氛围关键词</label>
              <input
                type="text"
                value={moodKeywords}
                onChange={(e) => setMoodKeywords(e.target.value)}
                placeholder="如：浪漫，意外"
                className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-purple-500"
              />
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">AI模型</label>
            <select
              value={aiModelOverride}
              onChange={(e) => setAiModelOverride(e.target.value)}
              className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-purple-500"
            >
              <option value="">跟随全局</option>
              {AI_MODEL_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </div>

          <label className="flex items-center gap-2 text-sm text-gray-600 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={debugEnabled}
              onChange={(e) => setDebugEnabled(e.target.checked)}
              className="rounded border-gray-300 text-purple-600 focus:ring-purple-500"
            />
            Debug 模式（查看提示词与模型原始回复）
          </label>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">自定义提示（可选）</label>
            <textarea
              value={customPrompt}
              onChange={(e) => setCustomPrompt(e.target.value)}
              placeholder="描述你想要的场景特点..."
              className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-purple-500"
              rows={2}
            />
          </div>

          <button
            onClick={() => generateMutation.mutate()}
            disabled={generateMutation.isPending}
            className="w-full py-2 bg-gradient-to-r from-purple-500 to-pink-500 text-white rounded-lg hover:from-purple-600 hover:to-pink-600 disabled:opacity-50 transition-all flex items-center justify-center gap-2"
          >
            {generateMutation.isPending ? (
              <>
                <Loader2 size={16} className="animate-spin" />
                生成中...
              </>
            ) : (
              <>
                <Wand2 size={16} />
                生成场景候选
              </>
            )}
          </button>

          <AIDebugPanel debugInfo={debugInfo} title="场景生成 Debug" />
        </>
      ) : (
        <>
          <p className="text-sm text-gray-600">选择一个场景保存：</p>
          <div className="space-y-2">
            {candidates.map((candidate, index) => (
              <div
                key={index}
                onClick={() => setSelectedCandidate(candidate)}
                className={`p-3 rounded-lg border-2 cursor-pointer transition-all ${
                  selectedCandidate === candidate
                    ? 'border-purple-500 bg-purple-50'
                    : 'border-gray-100 hover:border-purple-200'
                }`}
              >
                <div className="flex items-center justify-between">
                  <h4 className="font-medium text-gray-800">{candidate.name}</h4>
                  {selectedCandidate === candidate && <Check size={16} className="text-purple-500" />}
                </div>
                <p className="text-sm text-gray-500 mt-1 line-clamp-2">{candidate.description}</p>
                {candidate.location && (
                  <p className="text-xs text-gray-400 mt-1">
                    <MapPin size={12} className="inline mr-1" />
                    {candidate.location}
                  </p>
                )}
              </div>
            ))}
          </div>

          <div className="flex gap-2">
            <button
              onClick={handleSave}
              disabled={!selectedCandidate}
              className="flex-1 py-2 bg-purple-500 text-white rounded-lg hover:bg-purple-600 disabled:opacity-50 transition-colors"
            >
              保存选中场景
            </button>
            <button
              onClick={() => {
                setCandidates([]);
                setSelectedCandidate(null);
                setErrorMessage(null);
                setDebugInfo(null);
              }}
              className="px-4 py-2 border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors"
            >
              重新生成
            </button>
          </div>

          <AIDebugPanel debugInfo={debugInfo} title="场景生成 Debug" />
        </>
      )}
    </div>
  );
}
