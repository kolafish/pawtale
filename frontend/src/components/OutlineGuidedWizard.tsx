import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, ArrowRight, Sparkles, X, Loader2 } from 'lucide-react';
import { outlinesApi, charactersApi } from '../api';
import { useAppStore } from '../stores/appStore';

interface OutlineGuidedWizardProps {
  onClose: () => void;
  onSuccess: (outlineId: string) => void;
}

const AI_MODEL_OPTIONS = [
  { value: 'deepseek', label: 'DeepSeek' },
  { value: 'claude-opus-4', label: 'Claude Opus 4.6' },
  { value: 'claude-sonnet-4', label: 'Claude Sonnet 4.6' },
];

const MOOD_OPTIONS = ['甜', '虐', '搞笑', '治愈'];

export function OutlineGuidedWizard({ onClose, onSuccess }: OutlineGuidedWizardProps) {
  const queryClient = useQueryClient();
  const { defaultAiModel } = useAppStore();
  const [step, setStep] = useState(1);
  const [formData, setFormData] = useState({
    story_goal: '',
    character_tension: '',
    central_conflict: '',
    character_ids: [] as string[],
    mood: '',
    ai_model: defaultAiModel,
  });
  const [generatedOutline, setGeneratedOutline] = useState<any>(null);

  const { data: characters = [] } = useQuery({
    queryKey: ['characters'],
    queryFn: charactersApi.getAll,
  });

  const generateMutation = useMutation({
    mutationFn: outlinesApi.guidedCreate,
    onSuccess: (data) => {
      setGeneratedOutline(data);
      setStep(4);
    },
  });

  const createMutation = useMutation({
    mutationFn: outlinesApi.create,
    onSuccess: (outline) => {
      queryClient.invalidateQueries({ queryKey: ['outlines'] });
      onSuccess(outline.id);
      onClose();
    },
  });

  const handleNext = () => {
    if (step === 3) {
      // 调用AI生成大纲
      generateMutation.mutate(formData);
    } else {
      setStep(step + 1);
    }
  };

  const handleBack = () => {
    if (step > 1) {
      setStep(step - 1);
    }
  };

  const handleConfirm = () => {
    if (generatedOutline) {
      createMutation.mutate({
        title: generatedOutline.title,
        mood: generatedOutline.mood,
        writing_style: generatedOutline.writing_style,
        central_conflict: generatedOutline.central_conflict,
        ending_type: generatedOutline.ending_type,
        plot_hints: generatedOutline.plot_hints,
        forbidden_elements: generatedOutline.forbidden_elements,
        ai_model: formData.ai_model,
        story_goal: formData.story_goal,
        character_tension: formData.character_tension,
        creation_stage: 'confirmed',
      });
    }
  };

  const canProceed = () => {
    switch (step) {
      case 1:
        return formData.story_goal.trim().length > 0;
      case 2:
        return formData.character_tension.trim().length > 0;
      case 3:
        return formData.central_conflict.trim().length > 0;
      default:
        return true;
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-2xl max-w-2xl w-full max-h-[90vh] overflow-hidden flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-gray-200">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-gradient-to-br from-pink-500 to-purple-500 rounded-lg flex items-center justify-center">
              <Sparkles className="text-white" size={20} />
            </div>
            <div>
              <h2 className="text-xl font-semibold text-gray-800">引导式创建故事大纲</h2>
              <p className="text-sm text-gray-500">步骤 {step}/4</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 transition-colors"
          >
            <X size={24} />
          </button>
        </div>

        {/* Progress Bar */}
        <div className="px-6 pt-4">
          <div className="flex items-center gap-2">
            {[1, 2, 3, 4].map((s) => (
              <div
                key={s}
                className={`flex-1 h-2 rounded-full transition-colors ${
                  s <= step ? 'bg-pink-500' : 'bg-gray-200'
                }`}
              />
            ))}
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6">
          {step === 1 && (
            <div className="space-y-4">
              <div>
                <h3 className="text-lg font-semibold text-gray-800 mb-2">
                  第一步：故事目标与读者感受
                </h3>
                <p className="text-sm text-gray-600 mb-4">
                  这个故事想让读者感受到什么？甜蜜、心痛、燃？
                </p>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  故事目标 <span className="text-red-500">*</span>
                </label>
                <textarea
                  value={formData.story_goal}
                  onChange={(e) => setFormData({ ...formData, story_goal: e.target.value })}
                  placeholder="例如：我想写一个山城静谧的慢热故事，让读者感受到温暖治愈的氛围..."
                  className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-pink-500 focus:border-transparent resize-none"
                  rows={6}
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  期望的情绪基调（可选）
                </label>
                <div className="flex flex-wrap gap-2">
                  {MOOD_OPTIONS.map((mood) => (
                    <button
                      key={mood}
                      onClick={() => setFormData({ ...formData, mood })}
                      className={`px-4 py-2 rounded-lg border transition-colors ${
                        formData.mood === mood
                          ? 'bg-pink-500 text-white border-pink-500'
                          : 'bg-white text-gray-700 border-gray-300 hover:border-pink-300'
                      }`}
                    >
                      {mood}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  选择AI模型
                </label>
                <select
                  value={formData.ai_model}
                  onChange={(e) => setFormData({
                    ...formData,
                    ai_model: e.target.value as 'deepseek' | 'claude-opus-4' | 'claude-sonnet-4',
                  })}
                  className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-pink-500 focus:border-transparent"
                >
                  {AI_MODEL_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          )}

          {step === 2 && (
            <div className="space-y-4">
              <div>
                <h3 className="text-lg font-semibold text-gray-800 mb-2">
                  第二步：角色关系与核心张力
                </h3>
                <p className="text-sm text-gray-600 mb-4">
                  CP之间的核心张力是什么？哪些东西绝对不能写？
                </p>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  角色关系与张力 <span className="text-red-500">*</span>
                </label>
                <textarea
                  value={formData.character_tension}
                  onChange={(e) => setFormData({ ...formData, character_tension: e.target.value })}
                  placeholder="例如：高冷总裁与平凡女孩，身份地位的悬殊让女主自卑退缩，男主需要打破她的心防..."
                  className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-pink-500 focus:border-transparent resize-none"
                  rows={6}
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  选择已有角色（可选）
                </label>
                <div className="space-y-2 max-h-48 overflow-y-auto">
                  {characters.map((char) => (
                    <label
                      key={char.id}
                      className="flex items-center gap-3 p-3 border border-gray-200 rounded-lg hover:bg-gray-50 cursor-pointer"
                    >
                      <input
                        type="checkbox"
                        checked={formData.character_ids.includes(char.id)}
                        onChange={(e) => {
                          if (e.target.checked) {
                            setFormData({
                              ...formData,
                              character_ids: [...formData.character_ids, char.id],
                            });
                          } else {
                            setFormData({
                              ...formData,
                              character_ids: formData.character_ids.filter((id) => id !== char.id),
                            });
                          }
                        }}
                        className="w-4 h-4 text-pink-500 rounded focus:ring-pink-500"
                      />
                      <div className="flex-1">
                        <div className="font-medium text-gray-800">{char.name}</div>
                        <div className="text-sm text-gray-500">{char.identity}</div>
                      </div>
                    </label>
                  ))}
                </div>
              </div>
            </div>
          )}

          {step === 3 && (
            <div className="space-y-4">
              <div>
                <h3 className="text-lg font-semibold text-gray-800 mb-2">
                  第三步：剧情核心冲突
                </h3>
                <p className="text-sm text-gray-600 mb-4">
                  故事的主要矛盾是什么？
                </p>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  核心冲突 <span className="text-red-500">*</span>
                </label>
                <textarea
                  value={formData.central_conflict}
                  onChange={(e) => setFormData({ ...formData, central_conflict: e.target.value })}
                  placeholder="例如：两人因误会而分离，需要跨越重重障碍才能重逢..."
                  className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-pink-500 focus:border-transparent resize-none"
                  rows={6}
                />
              </div>
            </div>
          )}

          {step === 4 && generatedOutline && (
            <div className="space-y-4">
              <div>
                <h3 className="text-lg font-semibold text-gray-800 mb-2">
                  第四步：大纲预览与确认
                </h3>
                <p className="text-sm text-gray-600 mb-4">
                  AI已根据你的输入生成大纲草稿，请确认或调整
                </p>
              </div>

              <div className="space-y-4 bg-gray-50 p-4 rounded-lg">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">故事标题</label>
                  <div className="text-lg font-semibold text-gray-800">{generatedOutline.title}</div>
                </div>

                <div className="grid grid-cols-3 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">情绪基调</label>
                    <div className="px-3 py-1.5 bg-pink-100 text-pink-700 rounded-lg inline-block">
                      {generatedOutline.mood}
                    </div>
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">文字风格</label>
                    <div className="px-3 py-1.5 bg-purple-100 text-purple-700 rounded-lg inline-block">
                      {generatedOutline.writing_style}
                    </div>
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">结局类型</label>
                    <div className="px-3 py-1.5 bg-blue-100 text-blue-700 rounded-lg inline-block">
                      {generatedOutline.ending_type}
                    </div>
                  </div>
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">核心冲突</label>
                  <div className="text-gray-800">{generatedOutline.central_conflict}</div>
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">剧情提示</label>
                  <ul className="list-disc list-inside space-y-1">
                    {generatedOutline.plot_hints.map((hint: string, index: number) => (
                      <li key={index} className="text-gray-700">{hint}</li>
                    ))}
                  </ul>
                </div>

                {generatedOutline.forbidden_elements.length > 0 && (
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">禁止元素</label>
                    <div className="flex flex-wrap gap-2">
                      {generatedOutline.forbidden_elements.map((element: string, index: number) => (
                        <span key={index} className="px-2 py-1 bg-red-100 text-red-700 rounded text-sm">
                          {element}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between p-6 border-t border-gray-200">
          <button
            onClick={handleBack}
            disabled={step === 1 || generateMutation.isPending}
            className="flex items-center gap-2 px-4 py-2 text-gray-600 hover:text-gray-800 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            <ArrowLeft size={20} />
            上一步
          </button>

          {step < 4 ? (
            <button
              onClick={handleNext}
              disabled={!canProceed() || generateMutation.isPending}
              className="flex items-center gap-2 px-6 py-2 bg-pink-500 text-white rounded-lg hover:bg-pink-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {generateMutation.isPending ? (
                <>
                  <Loader2 size={20} className="animate-spin" />
                  AI生成中...
                </>
              ) : step === 3 ? (
                <>
                  <Sparkles size={20} />
                  生成大纲
                </>
              ) : (
                <>
                  下一步
                  <ArrowRight size={20} />
                </>
              )}
            </button>
          ) : (
            <button
              onClick={handleConfirm}
              disabled={createMutation.isPending}
              className="flex items-center gap-2 px-6 py-2 bg-green-500 text-white rounded-lg hover:bg-green-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {createMutation.isPending ? (
                <>
                  <Loader2 size={20} className="animate-spin" />
                  创建中...
                </>
              ) : (
                '确认创建'
              )}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
