import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Plus, User, Sparkles, Trash2, Check, Wand2, Loader2, Pencil, ChevronDown, ChevronRight } from 'lucide-react';
import { charactersApi } from '../api';
import { useAppStore } from '../stores/appStore';
import type { Character } from '../types';
import { AI_MODEL_OPTIONS } from '../constants/aiModels';

// 通用性格标签
const COMMON_PERSONALITY = ['温柔', '高冷', '傲娇', '腹黑', '阳光', '毒舌'];

// 男性偏向标签
const MALE_PERSONALITY = ['忠犬', '闷骚', '霸总', '奶狗', '狼狗', '禁欲系'];

// 女性偏向标签
const FEMALE_PERSONALITY = ['软萌', '御姐', '小恶魔', '清冷', '元气', '知性'];

// 根据性别获取性格标签
const getPersonalityOptions = (gender: string) => {
  if (gender === '女') {
    return [...COMMON_PERSONALITY, ...FEMALE_PERSONALITY];
  } else if (gender === '男') {
    return [...COMMON_PERSONALITY, ...MALE_PERSONALITY];
  }
  // 未选择性别时显示所有
  return [...COMMON_PERSONALITY, ...MALE_PERSONALITY, ...FEMALE_PERSONALITY];
};

const GENDER_OPTIONS = ['男', '女'];

export function CharacterPanel() {
  const queryClient = useQueryClient();
  const { selectedCharacters, addSelectedCharacter, removeSelectedCharacter, defaultAiModel } = useAppStore();
  const [isCreating, setIsCreating] = useState(false);
  const [editingCharacter, setEditingCharacter] = useState<Character | null>(null);

  const { data: characters = [], isLoading } = useQuery({
    queryKey: ['characters'],
    queryFn: charactersApi.getAll,
  });

  const createMutation = useMutation({
    mutationFn: charactersApi.create,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['characters'] });
      setIsCreating(false);
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<Character> }) =>
      charactersApi.update(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['characters'] });
      setEditingCharacter(null);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: charactersApi.delete,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['characters'] });
    },
  });

  const avatarMutation = useMutation({
    mutationFn: ({ id, style }: { id: string; style: string }) =>
      charactersApi.generateAvatar(id, style),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['characters'] });
    },
  });

  const isSelected = (id: string) => selectedCharacters.some(c => c.id === id);

  const handleToggleSelect = (character: Character) => {
    if (isSelected(character.id)) {
      removeSelectedCharacter(character.id);
    } else {
      addSelectedCharacter(character);
    }
  };

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center justify-between p-4 border-b border-pink-100">
        <h2 className="text-lg font-semibold text-gray-800">角色管理</h2>
        <button
          onClick={() => setIsCreating(true)}
          className="flex items-center gap-1 px-3 py-1.5 bg-pink-500 text-white rounded-lg hover:bg-pink-600 transition-colors text-sm"
        >
          <Plus size={16} />
          新建角色
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {isCreating && (
          <CharacterForm
            onSubmit={(data) => createMutation.mutate(data)}
            onCancel={() => setIsCreating(false)}
            isLoading={createMutation.isPending}
            defaultAiModel={defaultAiModel}
          />
        )}

        {isLoading ? (
          <div className="text-center text-gray-500 py-8">加载中...</div>
        ) : characters.length === 0 && !isCreating ? (
          <div className="text-center text-gray-500 py-8">
            <User size={48} className="mx-auto mb-2 opacity-50" />
            <p>还没有角色，创建一个吧</p>
          </div>
        ) : (
          characters.map((character) => (
            editingCharacter?.id === character.id ? (
              <CharacterForm
                key={character.id}
                initialData={character}
                onSubmit={(data) => updateMutation.mutate({ id: character.id, data })}
                onCancel={() => setEditingCharacter(null)}
                isLoading={updateMutation.isPending}
                isEditing
                defaultAiModel={defaultAiModel}
              />
            ) : (
              <CharacterCard
                key={character.id}
                character={character}
                isSelected={isSelected(character.id)}
                onToggleSelect={() => handleToggleSelect(character)}
                onEdit={() => setEditingCharacter(character)}
                onDelete={() => deleteMutation.mutate(character.id)}
                onGenerateAvatar={(style) => avatarMutation.mutate({ id: character.id, style })}
                isGeneratingAvatar={avatarMutation.isPending}
              />
            )
          ))
        )}
      </div>

      {selectedCharacters.length > 0 && (
        <div className="p-4 border-t border-pink-100 bg-pink-50">
          <p className="text-sm text-gray-600">
            已选择 {selectedCharacters.length} 个角色：
            {selectedCharacters.map(c => c.name).join('、')}
          </p>
        </div>
      )}
    </div>
  );
}

interface CharacterCardProps {
  character: Character;
  isSelected: boolean;
  onToggleSelect: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onGenerateAvatar: (style: string) => void;
  isGeneratingAvatar: boolean;
}

function CharacterCard({
  character,
  isSelected,
  onToggleSelect,
  onEdit,
  onDelete,
  onGenerateAvatar,
  isGeneratingAvatar,
}: CharacterCardProps) {
  return (
    <div
      className={`p-4 rounded-xl border-2 transition-all cursor-pointer ${
        isSelected
          ? 'border-pink-500 bg-pink-50'
          : 'border-gray-100 bg-white hover:border-pink-200'
      }`}
      onClick={onToggleSelect}
    >
      <div className="flex items-start gap-3">
        <div className="w-12 h-12 rounded-full bg-gradient-to-br from-pink-300 to-purple-300 flex items-center justify-center overflow-hidden flex-shrink-0">
          {character.avatar_url ? (
            <img src={character.avatar_url} alt={character.name} className="w-full h-full object-cover" />
          ) : (
            <User size={24} className="text-white" />
          )}
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="font-semibold text-gray-800">{character.name}</h3>
            {character.gender && (
              <span className={`text-xs px-1.5 py-0.5 rounded ${
                character.gender === '男' ? 'bg-blue-100 text-blue-600' : 'bg-pink-100 text-pink-600'
              }`}>
                {character.gender}
              </span>
            )}
            {isSelected && <Check size={16} className="text-pink-500" />}
          </div>
          {character.identity && (
            <p className="text-sm text-gray-500">{character.identity}</p>
          )}
          {character.personality.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-2">
              {character.personality.slice(0, 4).map((tag) => (
                <span
                  key={tag}
                  className="px-2 py-0.5 text-xs bg-pink-100 text-pink-600 rounded-full"
                >
                  {tag}
                </span>
              ))}
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
            onClick={() => onGenerateAvatar('日漫')}
            disabled={isGeneratingAvatar}
            className="p-1.5 text-gray-400 hover:text-pink-500 hover:bg-pink-50 rounded-lg transition-colors"
            title="生成头像"
          >
            <Sparkles size={16} />
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

interface CharacterFormProps {
  onSubmit: (data: Partial<Character>) => void;
  onCancel: () => void;
  isLoading: boolean;
  initialData?: Character;
  isEditing?: boolean;
  defaultAiModel: 'deepseek' | 'claude-opus-4' | 'claude-sonnet-4';
}

function CharacterForm({ onSubmit, onCancel, isLoading, initialData, isEditing, defaultAiModel }: CharacterFormProps) {
  const [formData, setFormData] = useState({
    name: initialData?.name || '',
    gender: initialData?.gender || '',
    age: initialData?.age?.toString() || '',
    identity: initialData?.identity || '',
    appearance: initialData?.appearance || '',
    personality: initialData?.personality || [],
    speaking_style: initialData?.speaking_style || '',
    background: initialData?.background || '',
    inspiration: initialData?.inspiration || '',
    // 深度字段
    role_type: initialData?.role_type || '',
    core_trait: initialData?.core_trait || '',
    life_goal: initialData?.life_goal || '',
    fatal_flaw: initialData?.fatal_flaw || '',
    growth_hint: initialData?.growth_hint || '',
    habits: (initialData?.habits || []).join('，'),
    secret: initialData?.secret || '',
  });
  const [isAutoCompleting, setIsAutoCompleting] = useState(false);
  const [showDepthFields, setShowDepthFields] = useState(false);
  const [autoCompleteModel, setAutoCompleteModel] = useState('');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const habits = formData.habits
      .split(/[，,]/)
      .map(s => s.trim())
      .filter(Boolean);

    onSubmit({
      ...formData,
      age: formData.age ? Number(formData.age) : undefined,
      role_type: formData.role_type || undefined,
      core_trait: formData.core_trait || undefined,
      life_goal: formData.life_goal || undefined,
      fatal_flaw: formData.fatal_flaw || undefined,
      growth_hint: formData.growth_hint || undefined,
      habits,
      secret: formData.secret || undefined,
    });
  };

  const togglePersonality = (tag: string) => {
    setFormData((prev) => ({
      ...prev,
      personality: prev.personality.includes(tag)
        ? prev.personality.filter((t) => t !== tag)
        : [...prev.personality, tag],
    }));
  };

  const handleAutoComplete = async () => {
    if (!formData.name && !formData.inspiration && !formData.gender) {
      alert('请至少填写角色名、性别或灵感来源');
      return;
    }

    setIsAutoCompleting(true);
    try {
      const result = await charactersApi.autoComplete({
        name: formData.name || undefined,
        gender: formData.gender || undefined,
        inspiration: formData.inspiration || undefined,
        ai_model: autoCompleteModel || defaultAiModel,
      });

      setFormData((prev) => ({
        ...prev,
        gender: prev.gender || result.gender || '',
        age: prev.age || result.age?.toString() || '',
        identity: prev.identity || result.identity || '',
        appearance: prev.appearance || result.appearance || '',
        personality: prev.personality.length > 0 ? prev.personality : result.personality || [],
        speaking_style: prev.speaking_style || result.speaking_style || '',
        background: prev.background || result.background || '',
        // 深度字段
        core_trait: prev.core_trait || result.core_trait || '',
        life_goal: prev.life_goal || result.life_goal || '',
        fatal_flaw: prev.fatal_flaw || result.fatal_flaw || '',
        growth_hint: prev.growth_hint || result.growth_hint || '',
        habits: prev.habits || (result.habits || []).join('，'),
        secret: prev.secret || result.secret || '',
      }));
    } catch (error) {
      console.error('Auto complete failed:', error);
      alert('AI 补全失败，请稍后重试');
    } finally {
      setIsAutoCompleting(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="p-4 bg-white rounded-xl border border-pink-200 space-y-4">
      {isEditing && (
        <div className="text-sm text-pink-600 font-medium">编辑角色</div>
      )}

      {/* 角色名和性别 */}
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">角色名 *</label>
          <input
            type="text"
            value={formData.name}
            onChange={(e) => setFormData({ ...formData, name: e.target.value })}
            className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500"
            required
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">性别</label>
          <div className="flex gap-2">
            {GENDER_OPTIONS.map((g) => (
              <button
                key={g}
                type="button"
                onClick={() => setFormData({ ...formData, gender: formData.gender === g ? '' : g })}
                className={`flex-1 py-2 rounded-lg border-2 transition-colors ${
                  formData.gender === g
                    ? g === '男' ? 'border-blue-500 bg-blue-50 text-blue-600' : 'border-pink-500 bg-pink-50 text-pink-600'
                    : 'border-gray-200 text-gray-600 hover:border-gray-300'
                }`}
              >
                {g}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* 灵感来源 + AI 补全按钮 */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">灵感来源</label>
        <div className="flex gap-2">
          <input
            type="text"
            value={formData.inspiration}
            onChange={(e) => setFormData({ ...formData, inspiration: e.target.value })}
            placeholder="如：何以琛、肖奈、顾漫笔下的男主"
            className="flex-1 px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500"
          />
          <select
            value={autoCompleteModel}
            onChange={(e) => setAutoCompleteModel(e.target.value)}
            className="px-2 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500"
            title="AI模型（留空=跟随全局）"
          >
            <option value="">跟随全局</option>
            {AI_MODEL_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
          <button
            type="button"
            onClick={handleAutoComplete}
            disabled={isAutoCompleting}
            className="px-4 py-2 bg-gradient-to-r from-purple-500 to-pink-500 text-white rounded-lg hover:from-purple-600 hover:to-pink-600 disabled:opacity-50 flex items-center gap-1.5 transition-all"
            title="AI 智能补全"
          >
            {isAutoCompleting ? (
              <Loader2 size={16} className="animate-spin" />
            ) : (
              <Wand2 size={16} />
            )}
            AI 补全
          </button>
        </div>
        <p className="text-xs text-gray-400 mt-1">填写灵感来源后点击「AI 补全」，模型默认跟随全局，也可临时覆盖</p>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">年龄</label>
          <input
            type="number"
            value={formData.age}
            onChange={(e) => setFormData({ ...formData, age: e.target.value })}
            className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">身份</label>
          <input
            type="text"
            value={formData.identity}
            onChange={(e) => setFormData({ ...formData, identity: e.target.value })}
            placeholder="如：CEO、医生"
            className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500"
          />
        </div>
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">性格标签</label>
        <div className="flex flex-wrap gap-2">
          {getPersonalityOptions(formData.gender).map((tag) => (
            <button
              key={tag}
              type="button"
              onClick={() => togglePersonality(tag)}
              className={`px-3 py-1 text-sm rounded-full transition-colors ${
                formData.personality.includes(tag)
                  ? 'bg-pink-500 text-white'
                  : 'bg-gray-100 text-gray-600 hover:bg-pink-100'
              }`}
            >
              {tag}
            </button>
          ))}
        </div>
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">外貌描述</label>
        <textarea
          value={formData.appearance}
          onChange={(e) => setFormData({ ...formData, appearance: e.target.value })}
          placeholder="如：身高185，短发，剑眉星目"
          className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500"
          rows={2}
        />
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">说话风格</label>
        <input
          type="text"
          value={formData.speaking_style}
          onChange={(e) => setFormData({ ...formData, speaking_style: e.target.value })}
          placeholder="如：语气冷淡，偶尔毒舌"
          className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500"
        />
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">背景故事</label>
        <textarea
          value={formData.background}
          onChange={(e) => setFormData({ ...formData, background: e.target.value })}
          placeholder="简短的角色背景"
          className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500"
          rows={2}
        />
      </div>

      {/* 深度字段折叠区 */}
      <div className="border-t border-gray-100 pt-4">
        <button
          type="button"
          onClick={() => setShowDepthFields(!showDepthFields)}
          className="flex items-center gap-2 text-sm font-medium text-gray-600 hover:text-pink-600 transition-colors"
        >
          {showDepthFields ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
          角色深度设定（可选）
        </button>

        {showDepthFields && (
          <div className="mt-4 space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">角色类型</label>
              <select
                value={formData.role_type}
                onChange={(e) => setFormData({ ...formData, role_type: e.target.value })}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500"
              >
                <option value="">选择...</option>
                <option value="protagonist">主角</option>
                <option value="supporting">配角</option>
                <option value="antagonist">反派</option>
              </select>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">底色（核心性格驱动力）</label>
              <textarea
                value={formData.core_trait}
                onChange={(e) => setFormData({ ...formData, core_trait: e.target.value })}
                placeholder="如：童年被抛弃让他极度害怕失去"
                className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500"
                rows={2}
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">目标追求</label>
              <textarea
                value={formData.life_goal}
                onChange={(e) => setFormData({ ...formData, life_goal: e.target.value })}
                placeholder="如：找到能让他卸下防备的人"
                className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500"
                rows={2}
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">致命弱点</label>
              <input
                type="text"
                value={formData.fatal_flaw}
                onChange={(e) => setFormData({ ...formData, fatal_flaw: e.target.value })}
                placeholder="如：过度骄傲、不善表达"
                className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">成长弧线提示</label>
              <input
                type="text"
                value={formData.growth_hint}
                onChange={(e) => setFormData({ ...formData, growth_hint: e.target.value })}
                placeholder="如：学会信任他人"
                className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">行为习惯</label>
              <input
                type="text"
                value={formData.habits}
                onChange={(e) => setFormData({ ...formData, habits: e.target.value })}
                placeholder="如：紧张时摸耳朵，思考时转笔"
                className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500"
              />
              <p className="text-xs text-gray-400 mt-1">使用逗号分隔多个习惯</p>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">秘密</label>
              <textarea
                value={formData.secret}
                onChange={(e) => setFormData({ ...formData, secret: e.target.value })}
                placeholder="如：其实是失散多年的兄妹（可作为剧情转折点）"
                className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500"
                rows={2}
              />
            </div>
          </div>
        )}
      </div>

      <div className="flex gap-2 pt-2">
        <button
          type="submit"
          disabled={isLoading || !formData.name}
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
