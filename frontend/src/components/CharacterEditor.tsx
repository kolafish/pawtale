import { Link2, RefreshCw, Save, Upload, Loader2 } from 'lucide-react';
import type { ReferenceItem } from '../types';
import type { RefSaveStatus } from '../hooks/useReferenceEditor';
import { metadataString, metadataStringArray } from '../utils/metadata';

const PERSONALITY_PRESETS = ['温柔', '高冷', '傲娇', '腹黑', '阳光', '毒舌', '忠犬', '闷骚', '软萌', '御姐', '元气', '知性'];

const ROLE_OPTIONS = [
  { value: 'protagonist', label: '主角' },
  { value: 'deuteragonist', label: '第二主角' },
  { value: 'supporting', label: '配角' },
  { value: 'antagonist', label: '反派' },
];

interface CharacterEditorProps {
  data: ReferenceItem;
  saveStatus: RefSaveStatus;
  onUpdate: (patch: Partial<ReferenceItem>) => void;
  onUpdateMeta: (key: string, value: unknown) => void;
  onSave: () => void;
  onSyncGlobal: () => void;
  onExportGlobal: () => Promise<void>;
  isExporting?: boolean;
}

export function CharacterEditor({ data, saveStatus, onUpdate, onUpdateMeta, onSave, onSyncGlobal, onExportGlobal, isExporting }: CharacterEditorProps) {
  const meta = data.metadata_json || {};
  const hasGlobalLink = !!meta.global_character_id;
  const personality = metadataStringArray(meta, 'personality');

  const togglePersonality = (tag: string) => {
    const next = personality.includes(tag)
      ? personality.filter((t) => t !== tag)
      : [...personality, tag];
    onUpdateMeta('personality', next);
  };

  const addCustomPersonality = () => {
    const tag = prompt('输入性格标签');
    if (tag?.trim() && !personality.includes(tag.trim())) {
      onUpdateMeta('personality', [...personality, tag.trim()]);
    }
  };

  const statusLabel = saveStatus === 'saved' ? '已保存' : saveStatus === 'saving' ? '保存中...' : '未保存';

  return (
    <div className="h-full flex flex-col bg-white">
      {/* Header */}
      <div className="flex items-center gap-3 px-6 py-3 border-b border-gray-200">
        <h2 className="text-lg font-semibold text-gray-800 flex-1 truncate">{data.name}</h2>
        {hasGlobalLink ? (
          <button
            onClick={onSyncGlobal}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border border-blue-200 text-blue-600 hover:bg-blue-50"
          >
            <Link2 size={12} />
            已关联全局角色
            <RefreshCw size={12} />
          </button>
        ) : null}
        <button
          onClick={onExportGlobal}
          disabled={isExporting}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border border-purple-200 text-purple-600 hover:bg-purple-50 disabled:opacity-50"
          title={hasGlobalLink ? '更新全局角色' : '导出到全局资料库'}
        >
          {isExporting ? <Loader2 size={12} className="animate-spin" /> : <Upload size={12} />}
          {hasGlobalLink ? '同步到全局' : '导出到全局库'}
        </button>
        <span className={`text-xs ${saveStatus === 'saved' ? 'text-gray-400' : 'text-amber-500'}`}>
          {statusLabel}
        </span>
        <button
          onClick={onSave}
          className="inline-flex items-center gap-1 px-3 py-1.5 text-xs rounded-lg bg-pink-500 text-white hover:bg-pink-600"
        >
          <Save size={12} />
          保存
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-6">
        {/* 基本信息 */}
        <section>
          <h3 className="text-sm font-semibold text-gray-600 mb-3 border-b border-gray-100 pb-1">基本信息</h3>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-xs font-medium text-gray-500 mb-1 block">名称</label>
              <input
                value={data.name}
                onChange={(e) => onUpdate({ name: e.target.value })}
                className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 focus:outline-none focus:ring-2 focus:ring-pink-400"
              />
            </div>
            <div>
              <label className="text-xs font-medium text-gray-500 mb-1 block">性别</label>
              <select
                value={metadataString(meta, 'gender')}
                onChange={(e) => onUpdateMeta('gender', e.target.value || null)}
                className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 focus:outline-none focus:ring-2 focus:ring-pink-400"
              >
                <option value="">未设定</option>
                <option value="男">男</option>
                <option value="女">女</option>
                <option value="其他">其他</option>
              </select>
            </div>
            <div>
              <label className="text-xs font-medium text-gray-500 mb-1 block">年龄</label>
              <input
                type="number"
                value={metadataString(meta, 'age')}
                onChange={(e) => onUpdateMeta('age', e.target.value ? Number(e.target.value) : null)}
                placeholder="年龄"
                className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 focus:outline-none focus:ring-2 focus:ring-pink-400"
              />
            </div>
            <div>
              <label className="text-xs font-medium text-gray-500 mb-1 block">身份/定位</label>
              <input
                value={metadataString(meta, 'identity')}
                onChange={(e) => onUpdateMeta('identity', e.target.value)}
                placeholder="如：CEO、大学生..."
                className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 focus:outline-none focus:ring-2 focus:ring-pink-400"
              />
            </div>
          </div>
          <div className="mt-4">
            <label className="text-xs font-medium text-gray-500 mb-1 block">外貌描述</label>
            <textarea
              value={metadataString(meta, 'appearance')}
              onChange={(e) => onUpdateMeta('appearance', e.target.value)}
              placeholder="描述角色的外貌特征..."
              rows={2}
              className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 focus:outline-none focus:ring-2 focus:ring-pink-400 resize-none"
            />
          </div>
          <div className="mt-4">
            <label className="text-xs font-medium text-gray-500 mb-1 block">说话风格</label>
            <input
              value={metadataString(meta, 'speaking_style')}
              onChange={(e) => onUpdateMeta('speaking_style', e.target.value)}
              placeholder="如：温柔低语、快言快语..."
              className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 focus:outline-none focus:ring-2 focus:ring-pink-400"
            />
          </div>
        </section>

        {/* 性格与内在 */}
        <section>
          <h3 className="text-sm font-semibold text-gray-600 mb-3 border-b border-gray-100 pb-1">性格与内在</h3>
          <div>
            <label className="text-xs font-medium text-gray-500 mb-2 block">性格标签</label>
            <div className="flex flex-wrap gap-2">
              {PERSONALITY_PRESETS.map((tag) => (
                <button
                  key={tag}
                  onClick={() => togglePersonality(tag)}
                  className={`px-2.5 py-1 text-xs rounded-full border transition-colors ${
                    personality.includes(tag)
                      ? 'bg-pink-500 text-white border-pink-500'
                      : 'bg-white text-gray-600 border-gray-200 hover:border-pink-300'
                  }`}
                >
                  {tag}
                </button>
              ))}
              {personality.filter((t) => !PERSONALITY_PRESETS.includes(t)).map((tag) => (
                <button
                  key={tag}
                  onClick={() => togglePersonality(tag)}
                  className="px-2.5 py-1 text-xs rounded-full bg-pink-500 text-white border border-pink-500"
                >
                  {tag}
                </button>
              ))}
              <button
                onClick={addCustomPersonality}
                className="px-2.5 py-1 text-xs rounded-full border border-dashed border-gray-300 text-gray-400 hover:border-pink-300 hover:text-pink-500"
              >
                + 自定义
              </button>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4 mt-4">
            <div>
              <label className="text-xs font-medium text-gray-500 mb-1 block">核心特质</label>
              <input
                value={metadataString(meta, 'core_trait')}
                onChange={(e) => onUpdateMeta('core_trait', e.target.value)}
                placeholder="核心性格驱动力..."
                className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 focus:outline-none focus:ring-2 focus:ring-pink-400"
              />
            </div>
            <div>
              <label className="text-xs font-medium text-gray-500 mb-1 block">人生目标</label>
              <input
                value={metadataString(meta, 'life_goal')}
                onChange={(e) => onUpdateMeta('life_goal', e.target.value)}
                placeholder="核心追求..."
                className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 focus:outline-none focus:ring-2 focus:ring-pink-400"
              />
            </div>
            <div>
              <label className="text-xs font-medium text-gray-500 mb-1 block">致命弱点</label>
              <input
                value={metadataString(meta, 'fatal_flaw')}
                onChange={(e) => onUpdateMeta('fatal_flaw', e.target.value)}
                placeholder="角色的弱点..."
                className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 focus:outline-none focus:ring-2 focus:ring-pink-400"
              />
            </div>
            <div>
              <label className="text-xs font-medium text-gray-500 mb-1 block">成长弧线</label>
              <input
                value={metadataString(meta, 'growth_hint')}
                onChange={(e) => onUpdateMeta('growth_hint', e.target.value)}
                placeholder="角色如何成长..."
                className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 focus:outline-none focus:ring-2 focus:ring-pink-400"
              />
            </div>
          </div>
          <div className="mt-4">
            <label className="text-xs font-medium text-gray-500 mb-1 block">秘密</label>
            <textarea
              value={metadataString(meta, 'secret')}
              onChange={(e) => onUpdateMeta('secret', e.target.value)}
              placeholder="角色的秘密，可作为剧情转折点..."
              rows={2}
              className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 focus:outline-none focus:ring-2 focus:ring-pink-400 resize-none"
            />
          </div>
        </section>

        {/* 本作设定 */}
        <section>
          <h3 className="text-sm font-semibold text-gray-600 mb-3 border-b border-gray-100 pb-1">本作设定</h3>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-xs font-medium text-gray-500 mb-1 block">作品中称呼</label>
              <input
                value={metadataString(meta, 'story_name')}
                onChange={(e) => onUpdateMeta('story_name', e.target.value)}
                placeholder="在本作中的称呼..."
                className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 focus:outline-none focus:ring-2 focus:ring-pink-400"
              />
            </div>
            <div>
              <label className="text-xs font-medium text-gray-500 mb-1 block">角色定位</label>
              <select
                value={metadataString(meta, 'role_in_story')}
                onChange={(e) => onUpdateMeta('role_in_story', e.target.value || null)}
                className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 focus:outline-none focus:ring-2 focus:ring-pink-400"
              >
                <option value="">未设定</option>
                {ROLE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>
          </div>
          <div className="mt-4">
            <label className="text-xs font-medium text-gray-500 mb-1 block">人物关系</label>
            <textarea
              value={metadataString(meta, 'relationships')}
              onChange={(e) => onUpdateMeta('relationships', e.target.value)}
              placeholder="与其他角色的关系，如：苏远 — 青梅竹马..."
              rows={3}
              className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 focus:outline-none focus:ring-2 focus:ring-pink-400 resize-none"
            />
          </div>
          <div className="mt-4">
            <label className="text-xs font-medium text-gray-500 mb-1 block">角色弧线</label>
            <textarea
              value={metadataString(meta, 'character_arc')}
              onChange={(e) => onUpdateMeta('character_arc', e.target.value)}
              placeholder="角色在本作中的成长变化..."
              rows={3}
              className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 focus:outline-none focus:ring-2 focus:ring-pink-400 resize-none"
            />
          </div>
        </section>

        {/* 自由描述 */}
        <section>
          <h3 className="text-sm font-semibold text-gray-600 mb-3 border-b border-gray-100 pb-1">自由描述</h3>
          <textarea
            value={data.content}
            onChange={(e) => onUpdate({ content: e.target.value })}
            placeholder="背景故事、补充说明等..."
            rows={8}
            className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 focus:outline-none focus:ring-2 focus:ring-pink-400 resize-none leading-relaxed"
          />
        </section>
      </div>
    </div>
  );
}
