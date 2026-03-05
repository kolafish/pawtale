import { useState, useMemo } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Sparkles, User, Loader2, PenLine, BookOpen, MessageSquare, X, MapPin, Users, ArrowRight, RefreshCw, Lightbulb, Wand2, CheckSquare, Square } from 'lucide-react';
import { storiesApi, scenesApi, charactersApi } from '../api';
import { useAppStore } from '../stores/appStore';
import type { Scene, Character, AIDebugInfo } from '../types';
import { AI_MODEL_OPTIONS } from '../constants/aiModels';
import { AIDebugPanel } from './AIDebugPanel';

// 字符级别的 diff 算法
function computeDiff(oldText: string, newText: string): { type: 'same' | 'add' | 'remove'; text: string }[] {
  // 按字符分割（保留中文和标点）
  const oldChars = [...oldText];
  const newChars = [...newText];

  const m = oldChars.length;
  const n = newChars.length;

  // 对于较长文本，使用简化的贪心算法避免性能问题
  if (m > 1000 || n > 1000) {
    return simpleDiff(oldText, newText);
  }

  // 构建 LCS 表
  const dp: number[][] = Array(m + 1).fill(null).map(() => Array(n + 1).fill(0));

  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      if (oldChars[i - 1] === newChars[j - 1]) {
        dp[i][j] = dp[i - 1][j - 1] + 1;
      } else {
        dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1]);
      }
    }
  }

  // 回溯构建 diff
  const temp: { type: 'same' | 'add' | 'remove'; text: string }[] = [];
  let i = m, j = n;

  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && oldChars[i - 1] === newChars[j - 1]) {
      temp.push({ type: 'same', text: oldChars[i - 1] });
      i--;
      j--;
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      temp.push({ type: 'add', text: newChars[j - 1] });
      j--;
    } else {
      temp.push({ type: 'remove', text: oldChars[i - 1] });
      i--;
    }
  }

  // 反转并合并相邻的相同类型
  const result: { type: 'same' | 'add' | 'remove'; text: string }[] = [];
  for (let k = temp.length - 1; k >= 0; k--) {
    const item = temp[k];
    if (result.length > 0 && result[result.length - 1].type === item.type) {
      result[result.length - 1].text += item.text;
    } else {
      result.push({ ...item });
    }
  }

  return result;
}

// 简化的 diff 算法，用于长文本
function simpleDiff(oldText: string, newText: string): { type: 'same' | 'add' | 'remove'; text: string }[] {
  // 按句子分割比较
  const oldSentences = oldText.split(/([。！？\n])/);
  const newSentences = newText.split(/([。！？\n])/);

  const result: { type: 'same' | 'add' | 'remove'; text: string }[] = [];
  const newSet = new Set(newSentences);
  const oldSet = new Set(oldSentences);

  // 标记旧文本中被删除的
  for (const s of oldSentences) {
    if (s && !newSet.has(s)) {
      result.push({ type: 'remove', text: s });
    }
  }

  // 标记新文本
  for (const s of newSentences) {
    if (s) {
      if (oldSet.has(s)) {
        result.push({ type: 'same', text: s });
      } else {
        result.push({ type: 'add', text: s });
      }
    }
  }

  return result;
}

// Diff 显示组件
function DiffView({ oldText, newText }: { oldText: string; newText: string }) {
  const diff = useMemo(() => computeDiff(oldText, newText), [oldText, newText]);

  return (
    <div className="text-sm leading-relaxed whitespace-pre-wrap">
      {diff.map((part, index) => {
        if (part.type === 'same') {
          return <span key={index} className="text-gray-700">{part.text}</span>;
        } else if (part.type === 'add') {
          return (
            <span key={index} className="bg-green-200 text-green-900">
              {part.text}
            </span>
          );
        } else {
          return (
            <span key={index} className="bg-red-200 text-red-900 line-through">
              {part.text}
            </span>
          );
        }
      })}
    </div>
  );
}

const MOOD_OPTIONS = [
  { value: '甜', label: '甜', emoji: '🍬', desc: '糖分超标' },
  { value: '虐', label: '虐', emoji: '💔', desc: '刀子与眼泪' },
  { value: '搞笑', label: '搞笑', emoji: '😂', desc: '沙雕欢乐' },
  { value: '治愈', label: '治愈', emoji: '🌸', desc: '温暖人心' },
];

const STYLE_OPTIONS = [
  { value: '细腻', label: '细腻', desc: '心理描写丰富' },
  { value: '白描', label: '白描', desc: '简洁留白' },
  { value: '诗意', label: '诗意', desc: '唯美浪漫' },
  { value: '轻松', label: '轻松', desc: '活泼俏皮' },
];

const LENGTH_OPTIONS = [
  { value: '短', label: '短', desc: '200-300字' },
  { value: '中', label: '中', desc: '400-600字' },
  { value: '长', label: '长', desc: '800-1200字' },
];

export function StoryPreview() {
  const queryClient = useQueryClient();
  const {
    selectedCharacters,
    selectedScene,
    currentStory,
    currentNode,
    isGenerating,
    defaultAiModel,
    setCurrentStory,
    setCurrentNode,
    setIsGenerating,
  } = useAppStore();

  // 生成参数
  const [selectedMood, setSelectedMood] = useState('甜');
  const [selectedStyle, setSelectedStyle] = useState('细腻');
  const [selectedLength, setSelectedLength] = useState('中');
  const [customDirection, setCustomDirection] = useState('');
  const [aiModelOverride, setAiModelOverride] = useState('');
  const [showFullView, setShowFullView] = useState(false);
  const [showSceneSwitcher, setShowSceneSwitcher] = useState(false);
  const [showCharacterManager, setShowCharacterManager] = useState(false);
  const [debugMode, setDebugMode] = useState(false);
  const [debugInfo, setDebugInfo] = useState<AIDebugInfo | null>(null);
  const resolvedAiModel = aiModelOverride || currentStory?.ai_model || defaultAiModel;

  // 获取所有场景和角色用于切换
  const { data: allScenes = [] } = useQuery({
    queryKey: ['scenes'],
    queryFn: scenesApi.getAll,
  });

  const { data: allCharacters = [] } = useQuery({
    queryKey: ['characters'],
    queryFn: charactersApi.getAll,
  });

  const generateMutation = useMutation({
    mutationFn: storiesApi.generate,
    onSuccess: (data) => {
      setCurrentNode(data);
      setDebugInfo(data.debug_info || null);
      queryClient.invalidateQueries({ queryKey: ['stories'] });
      storiesApi.getById(data.story_id).then(setCurrentStory);
    },
  });

  const chooseMutation = useMutation({
    mutationFn: ({ storyId, request }: { storyId: string; request: { choice_index?: number; custom_direction?: string; ai_model?: string; debug?: boolean } }) =>
      storiesApi.chooseBranch(storyId, request),
    onSuccess: (data) => {
      setCurrentNode(data);
      setDebugInfo(data.debug_info || null);
      setCustomDirection('');
      queryClient.invalidateQueries({ queryKey: ['stories'] });
      storiesApi.getById(data.story_id).then(setCurrentStory);
    },
  });

  const switchSceneMutation = useMutation({
    mutationFn: ({ storyId, sceneId, transitionType }: { storyId: string; sceneId: string; transitionType?: string }) =>
      storiesApi.switchScene(storyId, {
        scene_id: sceneId,
        transition_type: transitionType,
        ai_model: resolvedAiModel,
        debug: debugMode,
      }),
    onSuccess: (data) => {
      setCurrentNode(data);
      setDebugInfo(data.debug_info || null);
      setShowSceneSwitcher(false);
      queryClient.invalidateQueries({ queryKey: ['stories'] });
      storiesApi.getById(data.story_id).then(setCurrentStory);
    },
  });

  const updateCharactersMutation = useMutation({
    mutationFn: ({ storyId, addIds, removeIds }: { storyId: string; addIds: string[]; removeIds: string[] }) =>
      storiesApi.updateCharacters(storyId, { add_character_ids: addIds, remove_character_ids: removeIds }),
    onSuccess: (story) => {
      setCurrentStory(story);
      setShowCharacterManager(false);
      queryClient.invalidateQueries({ queryKey: ['stories'] });
    },
  });

  const regenerateChoicesMutation = useMutation({
    mutationFn: ({ storyId, nodeId, hint }: { storyId: string; nodeId: string; hint?: string }) =>
      storiesApi.regenerateChoices(storyId, nodeId, {
        hint,
        ai_model: resolvedAiModel,
        debug: debugMode,
      }),
    onSuccess: (data) => {
      // 更新当前节点的选项
      if (currentNode) {
        setCurrentNode({
          ...currentNode,
          choices: data.choices,
        });
      }
      setDebugInfo(data.debug_info || null);
      queryClient.invalidateQueries({ queryKey: ['stories'] });
      if (currentStory) {
        storiesApi.getById(currentStory.id).then(setCurrentStory);
      }
      setShowHintInput(false);
      setChoiceHint('');
    },
  });

  const [showHintInput, setShowHintInput] = useState(false);
  const [choiceHint, setChoiceHint] = useState('');

  const handleGenerate = async () => {
    if (!selectedScene || selectedCharacters.length === 0) return;

    setIsGenerating(true);
    try {
      await generateMutation.mutateAsync({
        character_ids: selectedCharacters.map(c => c.id),
        scene_id: selectedScene.id,
        mood: selectedMood,
        writing_style: selectedStyle,
        chapter_length: selectedLength,
        custom_direction: customDirection || undefined,
        ai_model: resolvedAiModel,
        debug: debugMode,
      });
      setCustomDirection('');
    } finally {
      setIsGenerating(false);
    }
  };

  const handleChoose = async (choiceIndex: number) => {
    if (!currentNode) return;

    setIsGenerating(true);
    try {
      await chooseMutation.mutateAsync({
        storyId: currentNode.story_id,
        request: { choice_index: choiceIndex, ai_model: resolvedAiModel, debug: debugMode },
      });
    } finally {
      setIsGenerating(false);
    }
  };

  const handleCustomContinue = async () => {
    if (!currentNode || !customDirection.trim()) return;

    setIsGenerating(true);
    try {
      await chooseMutation.mutateAsync({
        storyId: currentNode.story_id,
        request: { custom_direction: customDirection.trim(), ai_model: resolvedAiModel, debug: debugMode },
      });
    } finally {
      setIsGenerating(false);
    }
  };

  // 如果有当前故事和节点，直接显示故事内容（从故事列表点击进入）
  // 否则检查是否选择了角色和场景
  if (!currentNode && (selectedCharacters.length === 0 || !selectedScene)) {
    return (
      <div className="h-full flex flex-col items-center justify-center text-gray-400 p-8">
        <Sparkles size={64} className="mb-4 opacity-50" />
        <h3 className="text-lg font-medium text-gray-600 mb-2">开始创作</h3>
        <p className="text-center text-sm">
          {selectedCharacters.length === 0 && '请先选择至少一个角色'}
          {selectedCharacters.length > 0 && !selectedScene && '请选择一个场景'}
        </p>
      </div>
    );
  }

  // 全文视图
  if (showFullView && currentStory) {
    return (
      <StoryFullView
        story={currentStory}
        defaultAiModel={resolvedAiModel}
        onClose={() => setShowFullView(false)}
        onNodeClick={(nodeId) => {
          storiesApi.gotoNode(currentStory.id, nodeId).then((data) => {
            setCurrentNode(data);
            storiesApi.getById(data.story_id).then(setCurrentStory);
            setShowFullView(false);
          });
        }}
      />
    );
  }

  // 已选择但未生成
  if (!currentNode) {
    return (
      <div className="h-full flex flex-col p-6 overflow-y-auto">
        <div className="mb-6">
          <h3 className="text-lg font-semibold text-gray-800 mb-2">准备开始</h3>
          <div className="flex items-center gap-2 text-sm text-gray-500">
            <div className="flex -space-x-2">
              {selectedCharacters.map((char) => (
                <div
                  key={char.id}
                  className="w-8 h-8 rounded-full bg-gradient-to-br from-pink-300 to-purple-300 border-2 border-white flex items-center justify-center overflow-hidden"
                >
                  {char.avatar_url ? (
                    <img src={char.avatar_url} alt={char.name} className="w-full h-full object-cover" />
                  ) : (
                    <User size={14} className="text-white" />
                  )}
                </div>
              ))}
            </div>
            <span>{selectedCharacters.map(c => c.name).join(' & ')}</span>
            <span>·</span>
            <span>{selectedScene?.name}</span>
          </div>
        </div>

        <div className="flex-1 space-y-6">
          {/* 情绪选择 */}
          <div>
            <p className="text-sm font-medium text-gray-700 mb-3">故事氛围</p>
            <div className="grid grid-cols-4 gap-2">
              {MOOD_OPTIONS.map((mood) => (
                <button
                  key={mood.value}
                  onClick={() => setSelectedMood(mood.value)}
                  className={`p-3 rounded-xl border-2 transition-all ${
                    selectedMood === mood.value
                      ? 'border-pink-500 bg-pink-50'
                      : 'border-gray-100 bg-white hover:border-pink-200'
                  }`}
                >
                  <div className="text-xl mb-1">{mood.emoji}</div>
                  <div className="text-sm font-medium text-gray-800">{mood.label}</div>
                </button>
              ))}
            </div>
          </div>

          {/* 文字风格 */}
          <div>
            <p className="text-sm font-medium text-gray-700 mb-3">文字风格</p>
            <div className="grid grid-cols-4 gap-2">
              {STYLE_OPTIONS.map((style) => (
                <button
                  key={style.value}
                  onClick={() => setSelectedStyle(style.value)}
                  className={`p-3 rounded-xl border-2 transition-all ${
                    selectedStyle === style.value
                      ? 'border-purple-500 bg-purple-50'
                      : 'border-gray-100 bg-white hover:border-purple-200'
                  }`}
                >
                  <div className="text-sm font-medium text-gray-800">{style.label}</div>
                  <div className="text-xs text-gray-500">{style.desc}</div>
                </button>
              ))}
            </div>
          </div>

          {/* 章节长度 */}
          <div>
            <p className="text-sm font-medium text-gray-700 mb-3">章节长度</p>
            <div className="grid grid-cols-3 gap-2">
              {LENGTH_OPTIONS.map((len) => (
                <button
                  key={len.value}
                  onClick={() => setSelectedLength(len.value)}
                  className={`p-3 rounded-xl border-2 transition-all ${
                    selectedLength === len.value
                      ? 'border-blue-500 bg-blue-50'
                      : 'border-gray-100 bg-white hover:border-blue-200'
                  }`}
                >
                  <div className="text-sm font-medium text-gray-800">{len.label}</div>
                  <div className="text-xs text-gray-500">{len.desc}</div>
                </button>
              ))}
            </div>
          </div>

          {/* AI 模型 */}
          <div>
            <p className="text-sm font-medium text-gray-700 mb-3">AI 模型</p>
            <select
              value={aiModelOverride}
              onChange={(e) => setAiModelOverride(e.target.value)}
              className="w-full px-4 py-2 border border-gray-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-pink-500"
            >
              <option value="">跟随故事/全局</option>
              {AI_MODEL_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </div>

          <label className="flex items-center gap-2 text-sm text-gray-600 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={debugMode}
              onChange={(e) => {
                setDebugMode(e.target.checked);
                if (!e.target.checked) setDebugInfo(null);
              }}
              className="rounded border-gray-300 text-pink-600 focus:ring-pink-500"
            />
            Debug 模式（查看提示词与模型原始回复）
          </label>

          {/* 自定义开篇方向 */}
          <div>
            <p className="text-sm font-medium text-gray-700 mb-3">开篇方向（可选）</p>
            <textarea
              value={customDirection}
              onChange={(e) => setCustomDirection(e.target.value)}
              placeholder="描述你想要的开篇方式，如：他们因为一杯咖啡洒在身上而相遇..."
              className="w-full px-4 py-3 border border-gray-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-pink-500 resize-none"
              rows={2}
            />
          </div>

          {/* 开始按钮 */}
          <button
            onClick={handleGenerate}
            disabled={isGenerating}
            className="w-full py-4 bg-gradient-to-r from-pink-500 to-purple-500 text-white rounded-xl font-medium hover:from-pink-600 hover:to-purple-600 disabled:opacity-50 transition-all flex items-center justify-center gap-2"
          >
            {isGenerating ? (
              <>
                <Loader2 size={20} className="animate-spin" />
                正在创作...
              </>
            ) : (
              <>
                <Sparkles size={20} />
                开始创作
              </>
            )}
          </button>

          <AIDebugPanel debugInfo={debugInfo} title="故事链路 Debug" />
        </div>
      </div>
    );
  }

  // 显示故事内容
  return (
    <div className="h-full flex flex-col">
      {/* 标题栏 */}
      <div className="p-4 border-b border-pink-100">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="font-semibold text-gray-800">{currentStory?.title || '故事'}</h3>
            <div className="flex items-center gap-2 mt-1 text-sm text-gray-500">
              {currentNode.title && (
                <span className="font-medium text-pink-600">{currentNode.title}</span>
              )}
              {currentStory?.mood && (
                <span className="px-2 py-0.5 bg-pink-100 text-pink-600 rounded-full text-xs">
                  {currentStory.mood}
                </span>
              )}
              {currentStory?.writing_style && (
                <span className="px-2 py-0.5 bg-purple-100 text-purple-600 rounded-full text-xs">
                  {currentStory.writing_style}
                </span>
              )}
              {currentNode.word_count && (
                <span className="text-xs text-gray-400">{currentNode.word_count}字</span>
              )}
              {(currentNode.ai_model || currentStory?.ai_model) && (
                <span className="px-2 py-0.5 bg-blue-100 text-blue-600 rounded-full text-xs">
                  {AI_MODEL_OPTIONS.find((m) => m.value === (currentNode.ai_model || currentStory?.ai_model))
                    ?.label || currentNode.ai_model || currentStory?.ai_model}
                </span>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <select
              value={aiModelOverride}
              onChange={(e) => setAiModelOverride(e.target.value)}
              className="text-xs px-2 py-1.5 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500"
              title="AI 模型（留空=跟随故事/全局）"
            >
              <option value="">跟随故事/全局</option>
              {AI_MODEL_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
            <label className="flex items-center gap-1 text-xs text-gray-600 cursor-pointer select-none px-2 py-1.5 border border-gray-200 rounded-lg">
              <input
                type="checkbox"
                checked={debugMode}
                onChange={(e) => {
                  setDebugMode(e.target.checked);
                  if (!e.target.checked) setDebugInfo(null);
                }}
                className="rounded border-gray-300 text-pink-600 focus:ring-pink-500"
              />
              Debug
            </label>
            <button
              onClick={() => setShowFullView(true)}
              className="p-2 text-gray-500 hover:text-pink-500 hover:bg-pink-50 rounded-lg transition-colors"
              title="查看全文"
            >
              <BookOpen size={20} />
            </button>
          </div>
        </div>
      </div>

      {/* 故事上下文栏 - 场景和角色 */}
      <div className="px-4 py-2 bg-gray-50 border-b border-gray-100 flex items-center justify-between text-sm">
        <div className="flex items-center gap-4">
          {/* 当前场景 */}
          <button
            onClick={() => setShowSceneSwitcher(true)}
            className="flex items-center gap-1.5 px-2 py-1 rounded-lg hover:bg-white transition-colors"
            title="切换场景"
          >
            <MapPin size={14} className="text-pink-500" />
            <span className="text-gray-600">
              {currentNode.scene_name || currentStory?.scene_id ? '当前场景' : '无场景'}
            </span>
            <ArrowRight size={12} className="text-gray-400" />
          </button>

          {/* 当前角色 */}
          <button
            onClick={() => setShowCharacterManager(true)}
            className="flex items-center gap-1.5 px-2 py-1 rounded-lg hover:bg-white transition-colors"
            title="管理角色"
          >
            <Users size={14} className="text-purple-500" />
            <span className="text-gray-600">
              {currentStory?.characters.length || 0} 个角色
            </span>
            <ArrowRight size={12} className="text-gray-400" />
          </button>
        </div>
      </div>

      {/* 故事内容 */}
      <div className="flex-1 overflow-y-auto p-6">
        <div className="prose prose-pink max-w-none">
          {currentNode.content.split('\n').map((paragraph, index) => (
            paragraph.trim() && (
              <p key={index} className="text-gray-700 leading-relaxed mb-4">
                {paragraph}
              </p>
            )
          ))}
        </div>
        <div className="mt-4">
          <AIDebugPanel debugInfo={debugInfo} title="故事链路 Debug" />
        </div>
      </div>

      {/* 分支选择 */}
      <div className="p-4 border-t border-pink-100 bg-gradient-to-t from-pink-50 to-white">
        <div className="flex items-center justify-between mb-3">
          <p className="text-sm text-gray-500">接下来...</p>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setShowHintInput(!showHintInput)}
              disabled={regenerateChoicesMutation.isPending}
              className="flex items-center gap-1 px-2 py-1 text-xs text-purple-600 bg-purple-50 hover:bg-purple-100 rounded-lg transition-colors disabled:opacity-50"
              title="给点提示，生成新选项"
            >
              <Lightbulb size={14} />
              给点提示
            </button>
            <button
              onClick={() => currentNode && regenerateChoicesMutation.mutate({
                storyId: currentNode.story_id,
                nodeId: currentNode.node_id,
              })}
              disabled={regenerateChoicesMutation.isPending}
              className="flex items-center gap-1 px-2 py-1 text-xs text-pink-600 bg-pink-50 hover:bg-pink-100 rounded-lg transition-colors disabled:opacity-50"
              title="换一批选项"
            >
              {regenerateChoicesMutation.isPending ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <RefreshCw size={14} />
              )}
              换一批
            </button>
          </div>
        </div>

        {/* 提示输入框 */}
        {showHintInput && (
          <div className="mb-3 p-3 bg-purple-50 rounded-xl">
            <p className="text-xs text-purple-600 mb-2">输入你想要的方向，AI 会据此生成新选项：</p>
            <div className="flex gap-2">
              <input
                type="text"
                value={choiceHint}
                onChange={(e) => setChoiceHint(e.target.value)}
                placeholder="如：更甜一点、来点误会、加入第三者..."
                className="flex-1 px-3 py-2 text-sm border border-purple-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-purple-500"
                disabled={regenerateChoicesMutation.isPending}
              />
              <button
                onClick={() => currentNode && regenerateChoicesMutation.mutate({
                  storyId: currentNode.story_id,
                  nodeId: currentNode.node_id,
                  hint: choiceHint,
                })}
                disabled={regenerateChoicesMutation.isPending || !choiceHint.trim()}
                className="px-4 py-2 text-sm bg-purple-500 text-white rounded-lg hover:bg-purple-600 disabled:opacity-50 transition-colors"
              >
                生成
              </button>
            </div>
          </div>
        )}

        <div className="space-y-2">
          {currentNode.choices.map((choice, index) => (
            <button
              key={index}
              onClick={() => handleChoose(index)}
              disabled={isGenerating}
              className="w-full p-4 text-left bg-white rounded-xl border-2 border-gray-100 hover:border-pink-300 hover:bg-pink-50 transition-all disabled:opacity-50"
            >
              <span className="text-gray-800">{choice.label}</span>
              {choice.next_node && (
                <span className="ml-2 text-xs text-gray-400">(已生成)</span>
              )}
            </button>
          ))}

          {/* 自定义方向 */}
          <div className="mt-4 pt-4 border-t border-gray-100">
            <p className="text-sm text-gray-500 mb-2 flex items-center gap-1">
              <PenLine size={14} />
              或者，自己决定剧情走向
            </p>
            <div className="flex gap-2">
              <input
                type="text"
                value={customDirection}
                onChange={(e) => setCustomDirection(e.target.value)}
                placeholder="输入你想要的剧情发展..."
                className="flex-1 px-4 py-3 border border-gray-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-pink-500"
                disabled={isGenerating}
              />
              <button
                onClick={handleCustomContinue}
                disabled={isGenerating || !customDirection.trim()}
                className="px-6 py-3 bg-gradient-to-r from-pink-500 to-purple-500 text-white rounded-xl font-medium hover:from-pink-600 hover:to-purple-600 disabled:opacity-50 transition-all"
              >
                继续
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* 加载状态 */}
      {isGenerating && (
        <div className="absolute inset-0 bg-white/80 flex items-center justify-center">
          <div className="flex flex-col items-center">
            <Loader2 size={32} className="text-pink-500 animate-spin mb-2" />
            <p className="text-gray-600">正在生成...</p>
          </div>
        </div>
      )}

      {/* 场景切换器 */}
      {showSceneSwitcher && currentStory && (
        <SceneSwitcherModal
          scenes={allScenes}
          currentSceneId={currentStory.scene_id}
          onSwitch={(sceneId, transitionType) => {
            switchSceneMutation.mutate({
              storyId: currentStory.id,
              sceneId,
              transitionType,
            });
          }}
          onClose={() => setShowSceneSwitcher(false)}
          isLoading={switchSceneMutation.isPending}
        />
      )}

      {/* 角色管理器 */}
      {showCharacterManager && currentStory && (
        <CharacterManagerModal
          allCharacters={allCharacters}
          storyCharacters={currentStory.characters}
          onUpdate={(addIds, removeIds) => {
            updateCharactersMutation.mutate({
              storyId: currentStory.id,
              addIds,
              removeIds,
            });
          }}
          onClose={() => setShowCharacterManager(false)}
          isLoading={updateCharactersMutation.isPending}
        />
      )}
    </div>
  );
}

// 场景切换器模态框
interface SceneSwitcherModalProps {
  scenes: Scene[];
  currentSceneId?: string;
  onSwitch: (sceneId: string, transitionType?: string) => void;
  onClose: () => void;
  isLoading: boolean;
}

function SceneSwitcherModal({ scenes, currentSceneId, onSwitch, onClose, isLoading }: SceneSwitcherModalProps) {
  const [selectedSceneId, setSelectedSceneId] = useState<string | null>(null);
  const [transitionType, setTransitionType] = useState('自然过渡');

  const availableScenes = scenes.filter(s => s.id !== currentSceneId);

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-2xl w-full max-w-lg shadow-xl max-h-[80vh] flex flex-col">
        <div className="p-4 border-b border-gray-100 flex items-center justify-between">
          <h3 className="font-semibold text-gray-800">切换场景</h3>
          <button onClick={onClose} className="p-1 text-gray-400 hover:text-gray-600 rounded">
            <X size={20} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">过渡方式</label>
            <select
              value={transitionType}
              onChange={(e) => setTransitionType(e.target.value)}
              className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500"
            >
              <option value="自然过渡">自然过渡</option>
              <option value="时间跳跃">时间跳跃</option>
              <option value="空间转换">空间转换</option>
              <option value="回忆闪回">回忆闪回</option>
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">选择新场景</label>
            <div className="space-y-2">
              {availableScenes.map((scene) => (
                <div
                  key={scene.id}
                  onClick={() => setSelectedSceneId(scene.id)}
                  className={`p-3 rounded-lg border-2 cursor-pointer transition-all ${
                    selectedSceneId === scene.id
                      ? 'border-pink-500 bg-pink-50'
                      : 'border-gray-100 hover:border-pink-200'
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <h4 className="font-medium text-gray-800">{scene.name}</h4>
                    <span className="text-xs px-2 py-0.5 bg-gray-100 text-gray-600 rounded-full">
                      {scene.category}
                    </span>
                  </div>
                  <p className="text-sm text-gray-500 mt-1 line-clamp-2">{scene.description}</p>
                </div>
              ))}
              {availableScenes.length === 0 && (
                <p className="text-center text-gray-400 py-4">没有其他可用场景</p>
              )}
            </div>
          </div>
        </div>

        <div className="p-4 border-t border-gray-100 flex gap-2">
          <button
            onClick={() => selectedSceneId && onSwitch(selectedSceneId, transitionType)}
            disabled={!selectedSceneId || isLoading}
            className="flex-1 py-3 bg-gradient-to-r from-pink-500 to-purple-500 text-white rounded-xl font-medium hover:from-pink-600 hover:to-purple-600 disabled:opacity-50 transition-all flex items-center justify-center gap-2"
          >
            {isLoading ? (
              <>
                <Loader2 size={18} className="animate-spin" />
                切换中...
              </>
            ) : (
              <>
                <MapPin size={18} />
                切换场景
              </>
            )}
          </button>
          <button
            onClick={onClose}
            className="px-6 py-3 border border-gray-200 rounded-xl hover:bg-gray-50 transition-colors"
          >
            取消
          </button>
        </div>
      </div>
    </div>
  );
}

// 角色管理器模态框
interface CharacterManagerModalProps {
  allCharacters: Character[];
  storyCharacters: Character[];
  onUpdate: (addIds: string[], removeIds: string[]) => void;
  onClose: () => void;
  isLoading: boolean;
}

function CharacterManagerModal({ allCharacters, storyCharacters, onUpdate, onClose, isLoading }: CharacterManagerModalProps) {
  const [selectedIds, setSelectedIds] = useState<Set<string>>(
    new Set(storyCharacters.map(c => c.id))
  );

  const toggleCharacter = (id: string) => {
    const newSet = new Set(selectedIds);
    if (newSet.has(id)) {
      newSet.delete(id);
    } else {
      newSet.add(id);
    }
    setSelectedIds(newSet);
  };

  const handleSave = () => {
    const currentIds = new Set(storyCharacters.map(c => c.id));
    const addIds = [...selectedIds].filter(id => !currentIds.has(id));
    const removeIds = [...currentIds].filter(id => !selectedIds.has(id));
    onUpdate(addIds, removeIds);
  };

  const hasChanges = () => {
    const currentIds = new Set(storyCharacters.map(c => c.id));
    if (currentIds.size !== selectedIds.size) return true;
    for (const id of currentIds) {
      if (!selectedIds.has(id)) return true;
    }
    return false;
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-2xl w-full max-w-lg shadow-xl max-h-[80vh] flex flex-col">
        <div className="p-4 border-b border-gray-100 flex items-center justify-between">
          <h3 className="font-semibold text-gray-800">管理故事角色</h3>
          <button onClick={onClose} className="p-1 text-gray-400 hover:text-gray-600 rounded">
            <X size={20} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          <p className="text-sm text-gray-500 mb-3">选择参与故事的角色：</p>
          <div className="space-y-2">
            {allCharacters.map((char) => (
              <div
                key={char.id}
                onClick={() => toggleCharacter(char.id)}
                className={`p-3 rounded-lg border-2 cursor-pointer transition-all ${
                  selectedIds.has(char.id)
                    ? 'border-purple-500 bg-purple-50'
                    : 'border-gray-100 hover:border-purple-200'
                }`}
              >
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-full bg-gradient-to-br from-pink-300 to-purple-300 flex items-center justify-center overflow-hidden flex-shrink-0">
                    {char.avatar_url ? (
                      <img src={char.avatar_url} alt={char.name} className="w-full h-full object-cover" />
                    ) : (
                      <User size={18} className="text-white" />
                    )}
                  </div>
                  <div className="flex-1">
                    <h4 className="font-medium text-gray-800">{char.name}</h4>
                    {char.identity && (
                      <p className="text-sm text-gray-500">{char.identity}</p>
                    )}
                  </div>
                  {selectedIds.has(char.id) && (
                    <div className="w-5 h-5 bg-purple-500 rounded-full flex items-center justify-center">
                      <svg className="w-3 h-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                      </svg>
                    </div>
                  )}
                </div>
              </div>
            ))}
            {allCharacters.length === 0 && (
              <p className="text-center text-gray-400 py-4">没有可用角色</p>
            )}
          </div>
        </div>

        <div className="p-4 border-t border-gray-100 flex gap-2">
          <button
            onClick={handleSave}
            disabled={!hasChanges() || isLoading}
            className="flex-1 py-3 bg-gradient-to-r from-purple-500 to-pink-500 text-white rounded-xl font-medium hover:from-purple-600 hover:to-pink-600 disabled:opacity-50 transition-all flex items-center justify-center gap-2"
          >
            {isLoading ? (
              <>
                <Loader2 size={18} className="animate-spin" />
                保存中...
              </>
            ) : (
              <>
                <Users size={18} />
                保存更改
              </>
            )}
          </button>
          <button
            onClick={onClose}
            className="px-6 py-3 border border-gray-200 rounded-xl hover:bg-gray-50 transition-colors"
          >
            取消
          </button>
        </div>
      </div>
    </div>
  );
}

// 全文视图组件
interface StoryFullViewProps {
  story: {
    id: string;
    title: string;
    nodes: Record<string, { title?: string; content: string; word_count?: number; choices: { label: string; next_node: string | null }[] }>;
    current_path: string[];
    mood?: string;
    writing_style?: string;
  };
  defaultAiModel: string;
  onClose: () => void;
  onNodeClick: (nodeId: string) => void;
}

function StoryFullView({ story, defaultAiModel, onClose, onNodeClick }: StoryFullViewProps) {
  const [editingNode, setEditingNode] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState('');
  const [editContent, setEditContent] = useState('');
  const [aiModelOverride, setAiModelOverride] = useState('');
  const [debugMode, setDebugMode] = useState(false);
  const [actionDebugInfo, setActionDebugInfo] = useState<AIDebugInfo | null>(null);
  const [revisingNode, setRevisingNode] = useState<string | null>(null);
  const [reviseInstruction, setReviseInstruction] = useState('');
  const [isPolishMode, setIsPolishMode] = useState(false);
  const [selectedForPolish, setSelectedForPolish] = useState<Set<string>>(new Set());
  const [polishInstruction, setPolishInstruction] = useState('');
  const [polishPreview, setPolishPreview] = useState<{ node_id: string; content: string; ai_model?: string }[] | null>(null);
  const [acceptedPolish, setAcceptedPolish] = useState<Set<string>>(new Set());
  const [showDiffView, setShowDiffView] = useState(true);
  const resolvedActionAiModel = aiModelOverride || defaultAiModel;
  const queryClient = useQueryClient();
  const { setCurrentStory, setCurrentNode } = useAppStore();

  const updateNodeMutation = useMutation({
    mutationFn: ({ nodeId, data }: { nodeId: string; data: { title?: string; content?: string } }) =>
      storiesApi.updateNode(story.id, nodeId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['stories'] });
      storiesApi.getById(story.id).then(setCurrentStory);
      setEditingNode(null);
    },
  });

  const regenerateNodeMutation = useMutation({
    mutationFn: (nodeId: string) =>
      storiesApi.regenerateNode(story.id, nodeId, {
        ai_model: resolvedActionAiModel,
        debug: debugMode,
      }),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['stories'] });
      storiesApi.getById(story.id).then(setCurrentStory);
      setCurrentNode(data);
      setActionDebugInfo(data.debug_info || null);
    },
  });

  const reviseNodeMutation = useMutation({
    mutationFn: ({ nodeId, instruction }: { nodeId: string; instruction: string }) =>
      storiesApi.reviseNode(story.id, nodeId, instruction, resolvedActionAiModel, debugMode),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['stories'] });
      storiesApi.getById(story.id).then(setCurrentStory);
      setCurrentNode(data);
      setActionDebugInfo(data.debug_info || null);
      setRevisingNode(null);
      setReviseInstruction('');
    },
  });

  const deleteNodeMutation = useMutation({
    mutationFn: (nodeId: string) => storiesApi.deleteNode(story.id, nodeId),
    onSuccess: async () => {
      queryClient.invalidateQueries({ queryKey: ['stories'] });
      // 重新获取故事并更新当前节点
      const updatedStory = await storiesApi.getById(story.id);
      setCurrentStory(updatedStory);
      // 更新 currentNode 为新的当前节点
      if (updatedStory.current_node_id && updatedStory.nodes[updatedStory.current_node_id]) {
        const node = updatedStory.nodes[updatedStory.current_node_id];
        setCurrentNode({
          story_id: updatedStory.id,
          node_id: updatedStory.current_node_id,
          title: node.title,
          content: node.content,
          word_count: node.word_count,
          ai_model: node.ai_model || updatedStory.ai_model,
          choices: node.choices,
        });
      }
    },
  });

  const exportMutation = useMutation({
    mutationFn: () => storiesApi.export(story.id),
    onSuccess: (data) => {
      // 生成文本并下载
      let text = `${data.title}\n${'='.repeat(40)}\n\n`;
      data.chapters.forEach((chapter) => {
        text += `${chapter.title}\n${'-'.repeat(20)}\n\n${chapter.content}\n\n`;
      });
      text += `\n${'='.repeat(40)}\n总字数：${data.total_words}`;

      const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${data.title}.txt`;
      a.click();
      URL.revokeObjectURL(url);
    },
  });

  const polishMutation = useMutation({
    mutationFn: ({ nodeIds, instruction }: { nodeIds: string[]; instruction?: string }) =>
      storiesApi.polishChapters(story.id, {
        node_ids: nodeIds,
        instruction,
        ai_model: resolvedActionAiModel,
        debug: debugMode,
      }),
    onSuccess: (data) => {
      setPolishPreview(data.polished_contents);
      setActionDebugInfo(data.debug_info || null);
      // 默认全部采纳
      setAcceptedPolish(new Set(data.polished_contents.map(p => p.node_id)));
    },
  });

  const applyPolishMutation = useMutation({
    mutationFn: (polishedContents: { node_id: string; content: string; ai_model?: string }[]) =>
      storiesApi.applyPolish(story.id, { polished_contents: polishedContents }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['stories'] });
      storiesApi.getById(story.id).then(setCurrentStory);
      setPolishPreview(null);
      setIsPolishMode(false);
      setSelectedForPolish(new Set());
      setPolishInstruction('');
      setAcceptedPolish(new Set());
    },
  });

  const path = story.current_path || Object.keys(story.nodes);
  const totalWords = path.reduce((sum, nodeId) => {
    const node = story.nodes[nodeId];
    return sum + (node?.word_count || node?.content.length || 0);
  }, 0);

  const startEdit = (nodeId: string) => {
    const node = story.nodes[nodeId];
    setEditingNode(nodeId);
    setEditTitle(node.title || '');
    setEditContent(node.content);
  };

  const saveEdit = () => {
    if (!editingNode) return;
    updateNodeMutation.mutate({
      nodeId: editingNode,
      data: { title: editTitle, content: editContent },
    });
  };

  const startRevise = (nodeId: string) => {
    setRevisingNode(nodeId);
    setReviseInstruction('');
  };

  const submitRevise = () => {
    if (!revisingNode || !reviseInstruction.trim()) return;
    reviseNodeMutation.mutate({
      nodeId: revisingNode,
      instruction: reviseInstruction.trim(),
    });
  };

  const togglePolishSelection = (nodeId: string) => {
    const newSet = new Set(selectedForPolish);
    if (newSet.has(nodeId)) {
      newSet.delete(nodeId);
    } else {
      newSet.add(nodeId);
    }
    setSelectedForPolish(newSet);
  };

  const selectAllForPolish = () => {
    if (selectedForPolish.size === path.length) {
      setSelectedForPolish(new Set());
    } else {
      setSelectedForPolish(new Set(path));
    }
  };

  const handleStartPolish = () => {
    if (selectedForPolish.size < 1) return;
    // 按路径顺序排列选中的节点
    const orderedNodeIds = path.filter(nodeId => selectedForPolish.has(nodeId));
    polishMutation.mutate({
      nodeIds: orderedNodeIds,
      instruction: polishInstruction.trim() || undefined,
    });
  };

  const toggleAcceptPolish = (nodeId: string) => {
    const newSet = new Set(acceptedPolish);
    if (newSet.has(nodeId)) {
      newSet.delete(nodeId);
    } else {
      newSet.add(nodeId);
    }
    setAcceptedPolish(newSet);
  };

  const handleApplyPolish = () => {
    if (!polishPreview) return;
    // 只应用被采纳的章节
    const acceptedContents = polishPreview.filter(p => acceptedPolish.has(p.node_id));
    if (acceptedContents.length === 0) {
      setPolishPreview(null);
      return;
    }
    applyPolishMutation.mutate(acceptedContents);
  };

  return (
    <div className="h-full flex flex-col bg-white">
      {/* 头部 */}
      <div className="p-4 border-b border-pink-100 flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-gray-800">{story.title}</h2>
          <div className="flex items-center gap-2 mt-1 text-sm text-gray-500">
            <span>{path.length} 章</span>
            <span>·</span>
            <span>{totalWords} 字</span>
            {story.mood && (
              <span className="px-2 py-0.5 bg-pink-100 text-pink-600 rounded-full text-xs">
                {story.mood}
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={aiModelOverride}
            onChange={(e) => setAiModelOverride(e.target.value)}
            className="text-xs px-2 py-1.5 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500"
            title="AI 模型（留空=跟随上一级）"
          >
            <option value="">跟随上一级</option>
            {AI_MODEL_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
          <label className="flex items-center gap-1 text-xs text-gray-600 cursor-pointer select-none px-2 py-1.5 border border-gray-200 rounded-lg">
            <input
              type="checkbox"
              checked={debugMode}
              onChange={(e) => {
                setDebugMode(e.target.checked);
                if (!e.target.checked) setActionDebugInfo(null);
              }}
              className="rounded border-gray-300 text-pink-600 focus:ring-pink-500"
            />
            Debug
          </label>
          {isPolishMode ? (
            <>
              <button
                onClick={selectAllForPolish}
                className="px-3 py-2 text-sm border border-gray-200 text-gray-600 rounded-lg hover:bg-gray-50 transition-colors"
              >
                {selectedForPolish.size === path.length ? '取消全选' : '全选'}
              </button>
              <button
                onClick={handleStartPolish}
                disabled={selectedForPolish.size < 1 || polishMutation.isPending}
                className="px-4 py-2 text-sm bg-gradient-to-r from-purple-500 to-pink-500 text-white rounded-lg hover:from-purple-600 hover:to-pink-600 disabled:opacity-50 transition-colors flex items-center gap-1"
              >
                {polishMutation.isPending ? (
                  <>
                    <Loader2 size={16} className="animate-spin" />
                    润色中...
                  </>
                ) : (
                  <>
                    <Wand2 size={16} />
                    润色 ({selectedForPolish.size})
                  </>
                )}
              </button>
              <button
                onClick={() => {
                  setIsPolishMode(false);
                  setSelectedForPolish(new Set());
                  setPolishInstruction('');
                }}
                className="px-3 py-2 text-sm border border-gray-200 text-gray-600 rounded-lg hover:bg-gray-50 transition-colors"
              >
                取消
              </button>
            </>
          ) : (
            <>
              <button
                onClick={() => setIsPolishMode(true)}
                className="px-4 py-2 text-sm bg-purple-100 text-purple-700 rounded-lg hover:bg-purple-200 transition-colors flex items-center gap-1"
                title="选择章节进行整体润色"
              >
                <Wand2 size={16} />
                整体润色
              </button>
              <button
                onClick={() => exportMutation.mutate()}
                disabled={exportMutation.isPending}
                className="px-4 py-2 text-sm bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 transition-colors"
              >
                {exportMutation.isPending ? '导出中...' : '导出全文'}
              </button>
              <button
                onClick={onClose}
                className="px-4 py-2 text-sm bg-pink-500 text-white rounded-lg hover:bg-pink-600 transition-colors"
              >
                返回阅读
              </button>
            </>
          )}
        </div>
      </div>

      {/* 润色意见输入（润色模式下显示） */}
      {isPolishMode && (
        <div className="px-6 py-3 bg-purple-50 border-b border-purple-100">
          <div className="flex items-center gap-3">
            <span className="text-sm text-purple-700 whitespace-nowrap">润色意见（可选）:</span>
            <input
              type="text"
              value={polishInstruction}
              onChange={(e) => setPolishInstruction(e.target.value)}
              placeholder="如：让对话更幽默、增加环境描写、加强情感冲突..."
              className="flex-1 px-3 py-1.5 text-sm border border-purple-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-purple-500 bg-white"
            />
          </div>
        </div>
      )}

      <div className="px-6 pt-4">
        <AIDebugPanel debugInfo={actionDebugInfo} title="故事动作 Debug" />
      </div>

      {/* 章节列表 */}
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        {path.map((nodeId, index) => {
          const node = story.nodes[nodeId];
          if (!node) return null;

          const isEditing = editingNode === nodeId;
          const isSelectedForPolish = selectedForPolish.has(nodeId);

          return (
            <div
              key={nodeId}
              className={`bg-gray-50 rounded-xl p-6 transition-all ${
                isPolishMode
                  ? isSelectedForPolish
                    ? 'ring-2 ring-purple-500 bg-purple-50'
                    : 'cursor-pointer hover:bg-gray-100'
                  : ''
              }`}
              onClick={isPolishMode && !isEditing ? () => togglePolishSelection(nodeId) : undefined}
            >
              {isEditing ? (
                <div className="space-y-4">
                  <input
                    type="text"
                    value={editTitle}
                    onChange={(e) => setEditTitle(e.target.value)}
                    placeholder="章节标题"
                    className="w-full px-4 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500 font-medium"
                  />
                  <textarea
                    value={editContent}
                    onChange={(e) => setEditContent(e.target.value)}
                    className="w-full px-4 py-3 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500 min-h-[200px]"
                  />
                  <div className="flex gap-2">
                    <button
                      onClick={saveEdit}
                      disabled={updateNodeMutation.isPending}
                      className="px-4 py-2 bg-pink-500 text-white rounded-lg hover:bg-pink-600 disabled:opacity-50"
                    >
                      {updateNodeMutation.isPending ? '保存中...' : '保存'}
                    </button>
                    <button
                      onClick={() => setEditingNode(null)}
                      className="px-4 py-2 border border-gray-200 rounded-lg hover:bg-gray-100"
                    >
                      取消
                    </button>
                  </div>
                </div>
              ) : (
                <>
                  <div className="flex items-center justify-between mb-4">
                    <div className="flex items-center gap-2">
                      {isPolishMode && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            togglePolishSelection(nodeId);
                          }}
                          className="text-purple-500"
                        >
                          {isSelectedForPolish ? <CheckSquare size={20} /> : <Square size={20} />}
                        </button>
                      )}
                      <h3 className="text-lg font-semibold text-gray-800">
                        第{index + 1}章 {node.title || ''}
                      </h3>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-sm text-gray-400">
                        {node.word_count || node.content.length}字
                      </span>
                      {!isPolishMode && (
                        <>
                          <button
                            onClick={() => onNodeClick(nodeId)}
                            className="px-2 py-1 text-xs text-pink-600 bg-pink-50 hover:bg-pink-100 rounded-md transition-colors"
                            title="从此节点继续创作"
                          >
                            从此继续
                          </button>
                          <button
                            onClick={() => startEdit(nodeId)}
                            className="p-1.5 text-gray-400 hover:text-blue-500 hover:bg-blue-50 rounded-lg transition-colors"
                            title="直接编辑"
                          >
                            <PenLine size={16} />
                          </button>
                          <button
                            onClick={() => startRevise(nodeId)}
                            className="p-1.5 text-gray-400 hover:text-green-500 hover:bg-green-50 rounded-lg transition-colors"
                            title="AI 修改"
                          >
                            <MessageSquare size={16} />
                          </button>
                          <button
                            onClick={() => {
                              if (confirm('确定要重新生成这一章吗？')) {
                                regenerateNodeMutation.mutate(nodeId);
                              }
                            }}
                            disabled={regenerateNodeMutation.isPending}
                            className="p-1.5 text-gray-400 hover:text-purple-500 hover:bg-purple-50 rounded-lg transition-colors"
                            title="重新生成"
                          >
                            <Sparkles size={16} />
                          </button>
                          {nodeId !== 'node_1' && (
                            <button
                              onClick={() => {
                                if (confirm('确定要删除这一章及其后续章节吗？')) {
                                  deleteNodeMutation.mutate(nodeId);
                                }
                              }}
                              disabled={deleteNodeMutation.isPending}
                              className="p-1.5 text-gray-400 hover:text-red-500 hover:bg-red-50 rounded-lg transition-colors"
                              title="删除"
                            >
                              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <path d="M3 6h18" /><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6" /><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2" />
                              </svg>
                            </button>
                          )}
                        </>
                      )}
                    </div>
                  </div>
                  <div className="prose prose-pink max-w-none">
                    {node.content.split('\n').map((paragraph, pIndex) => (
                      paragraph.trim() && (
                        <p key={pIndex} className="text-gray-700 leading-relaxed mb-3">
                          {paragraph}
                        </p>
                      )
                    ))}
                  </div>
                  {node.choices.length > 0 && index === path.length - 1 && (
                    <div className="mt-4 pt-4 border-t border-gray-200">
                      <p className="text-sm text-gray-500 mb-2">可选分支：</p>
                      <div className="flex flex-wrap gap-2">
                        {node.choices.map((choice, cIndex) => (
                          <span
                            key={cIndex}
                            className="px-3 py-1 text-sm bg-white border border-gray-200 rounded-full text-gray-600"
                          >
                            {choice.label}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>
          );
        })}
      </div>

      {/* AI 修改对话框 */}
      {revisingNode && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-2xl w-full max-w-lg shadow-xl">
            <div className="p-4 border-b border-gray-100 flex items-center justify-between">
              <h3 className="font-semibold text-gray-800">AI 修改章节</h3>
              <button
                onClick={() => setRevisingNode(null)}
                className="p-1 text-gray-400 hover:text-gray-600 rounded"
              >
                <X size={20} />
              </button>
            </div>
            <div className="p-4 space-y-4">
              <div className="bg-gray-50 rounded-lg p-3 max-h-40 overflow-y-auto">
                <p className="text-sm text-gray-500 mb-1">当前内容预览：</p>
                <p className="text-sm text-gray-700 line-clamp-4">
                  {story.nodes[revisingNode]?.content.slice(0, 200)}...
                </p>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  修改意见
                </label>
                <textarea
                  value={reviseInstruction}
                  onChange={(e) => setReviseInstruction(e.target.value)}
                  placeholder="描述你想要的修改，例如：&#10;- 让对话更加幽默&#10;- 增加更多心理描写&#10;- 让男主的态度更加温柔&#10;- 把场景改成下雨天"
                  className="w-full px-4 py-3 border border-gray-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-pink-500 min-h-[120px] resize-none"
                  autoFocus
                />
              </div>
              <div className="flex gap-2">
                <button
                  onClick={submitRevise}
                  disabled={reviseNodeMutation.isPending || !reviseInstruction.trim()}
                  className="flex-1 py-3 bg-gradient-to-r from-pink-500 to-purple-500 text-white rounded-xl font-medium hover:from-pink-600 hover:to-purple-600 disabled:opacity-50 transition-all flex items-center justify-center gap-2"
                >
                  {reviseNodeMutation.isPending ? (
                    <>
                      <Loader2 size={18} className="animate-spin" />
                      修改中...
                    </>
                  ) : (
                    <>
                      <Sparkles size={18} />
                      AI 修改
                    </>
                  )}
                </button>
                <button
                  onClick={() => setRevisingNode(null)}
                  className="px-6 py-3 border border-gray-200 rounded-xl hover:bg-gray-50 transition-colors"
                >
                  取消
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 润色预览对话框 */}
      {polishPreview && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-2xl w-full max-w-4xl shadow-xl max-h-[90vh] flex flex-col">
            <div className="p-4 border-b border-gray-100 flex items-center justify-between">
              <div>
                <h3 className="font-semibold text-gray-800">润色预览</h3>
                <p className="text-sm text-gray-500 mt-1">
                  共 {polishPreview.length} 个章节，已选择采纳 {acceptedPolish.size} 个
                </p>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => {
                    if (acceptedPolish.size === polishPreview.length) {
                      setAcceptedPolish(new Set());
                    } else {
                      setAcceptedPolish(new Set(polishPreview.map(p => p.node_id)));
                    }
                  }}
                  className="px-3 py-1.5 text-sm border border-gray-200 text-gray-600 rounded-lg hover:bg-gray-50 transition-colors"
                >
                  {acceptedPolish.size === polishPreview.length ? '取消全选' : '全部采纳'}
                </button>
                <button
                  onClick={() => setShowDiffView(!showDiffView)}
                  className={`px-3 py-1.5 text-sm rounded-lg transition-colors ${
                    showDiffView
                      ? 'bg-purple-100 text-purple-700'
                      : 'bg-gray-100 text-gray-600'
                  }`}
                >
                  {showDiffView ? '差异视图' : '对比视图'}
                </button>
                <button
                  onClick={() => setPolishPreview(null)}
                  className="p-1 text-gray-400 hover:text-gray-600 rounded"
                >
                  <X size={20} />
                </button>
              </div>
            </div>
            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              {polishPreview.map((item) => {
                const originalNode = story.nodes[item.node_id];
                const isAccepted = acceptedPolish.has(item.node_id);
                return (
                  <div
                    key={item.node_id}
                    className={`border rounded-xl overflow-hidden transition-all ${
                      isAccepted
                        ? 'border-green-300 ring-2 ring-green-100'
                        : 'border-gray-200 opacity-60'
                    }`}
                  >
                    <div
                      className={`px-4 py-2 border-b flex items-center justify-between cursor-pointer ${
                        isAccepted ? 'bg-green-50 border-green-200' : 'bg-gray-50 border-gray-200'
                      }`}
                      onClick={() => toggleAcceptPolish(item.node_id)}
                    >
                      <div className="flex items-center gap-2">
                        <button className={isAccepted ? 'text-green-600' : 'text-gray-400'}>
                          {isAccepted ? <CheckSquare size={18} /> : <Square size={18} />}
                        </button>
                        <span className="font-medium text-gray-700">
                          第{path.indexOf(item.node_id) + 1}章 {originalNode?.title || ''}
                        </span>
                      </div>
                      <span className={`text-xs px-2 py-0.5 rounded-full ${
                        isAccepted
                          ? 'bg-green-100 text-green-700'
                          : 'bg-gray-100 text-gray-500'
                      }`}>
                        {isAccepted ? '采纳' : '跳过'}
                      </span>
                    </div>
                    {showDiffView ? (
                      /* 差异视图 - 高亮显示变化 */
                      <div className="p-4">
                        <div className="flex items-center gap-4 mb-3 text-xs">
                          <span className="flex items-center gap-1">
                            <span className="w-3 h-3 bg-green-200 rounded"></span>
                            <span className="text-gray-500">新增</span>
                          </span>
                          <span className="flex items-center gap-1">
                            <span className="w-3 h-3 bg-red-200 rounded"></span>
                            <span className="text-gray-500">删除</span>
                          </span>
                        </div>
                        <div className="max-h-64 overflow-y-auto">
                          <DiffView
                            oldText={originalNode?.content || ''}
                            newText={item.content}
                          />
                        </div>
                      </div>
                    ) : (
                      /* 对比视图 - 左右对照 */
                      <div className="grid grid-cols-2 divide-x divide-gray-200">
                        <div className="p-4">
                          <p className="text-xs text-gray-400 mb-2">原文</p>
                          <div className="text-sm text-gray-600 max-h-48 overflow-y-auto">
                            {originalNode?.content.split('\n').map((p, i) => (
                              p.trim() && <p key={i} className="mb-2">{p}</p>
                            ))}
                          </div>
                        </div>
                        <div className="p-4 bg-purple-50/50">
                          <p className="text-xs text-purple-600 mb-2">润色后</p>
                          <div className="text-sm text-gray-700 max-h-48 overflow-y-auto">
                            {item.content.split('\n').map((p, i) => (
                              p.trim() && <p key={i} className="mb-2">{p}</p>
                            ))}
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
            <div className="p-4 border-t border-gray-100 flex gap-2">
              <button
                onClick={handleApplyPolish}
                disabled={applyPolishMutation.isPending || acceptedPolish.size === 0}
                className="flex-1 py-3 bg-gradient-to-r from-purple-500 to-pink-500 text-white rounded-xl font-medium hover:from-purple-600 hover:to-pink-600 disabled:opacity-50 transition-all flex items-center justify-center gap-2"
              >
                {applyPolishMutation.isPending ? (
                  <>
                    <Loader2 size={18} className="animate-spin" />
                    应用中...
                  </>
                ) : (
                  <>
                    <Wand2 size={18} />
                    应用选中的 {acceptedPolish.size} 个章节
                  </>
                )}
              </button>
              <button
                onClick={() => setPolishPreview(null)}
                className="px-6 py-3 border border-gray-200 rounded-xl hover:bg-gray-50 transition-colors"
              >
                取消
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
