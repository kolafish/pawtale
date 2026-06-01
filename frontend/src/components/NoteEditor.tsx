import { Save } from 'lucide-react';
import { REFERENCE_TYPES, REFERENCE_TYPE_LABELS } from '../domain/references';
import type { ReferenceItem } from '../types';
import type { RefSaveStatus } from '../hooks/useReferenceEditor';
import { metadataString, metadataStringArray } from '../utils/metadata';

interface NoteEditorProps {
  data: ReferenceItem;
  saveStatus: RefSaveStatus;
  onUpdate: (patch: Partial<ReferenceItem>) => void;
  onUpdateMeta: (key: string, value: unknown) => void;
  onSave: () => void;
}

export function NoteEditor({ data, saveStatus, onUpdate, onUpdateMeta, onSave }: NoteEditorProps) {
  const meta = data.metadata_json || {};
  const isLocation = data.type === REFERENCE_TYPES.LOCATION;
  const typeLabel = REFERENCE_TYPE_LABELS[data.type];
  const statusLabel = saveStatus === 'saved' ? '已保存' : saveStatus === 'saving' ? '保存中...' : '未保存';

  return (
    <div className="h-full flex flex-col bg-white">
      {/* Header */}
      <div className="flex items-center gap-3 px-6 py-3 border-b border-gray-200">
        <span className="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-500">{typeLabel}</span>
        <input
          value={data.name}
          onChange={(e) => onUpdate({ name: e.target.value })}
          className="text-lg font-semibold text-gray-800 flex-1 bg-transparent border-none outline-none focus:ring-0 placeholder-gray-300"
          placeholder="标题..."
        />
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

      {/* Location-specific fields */}
      {isLocation && (
        <div className="px-6 py-3 border-b border-gray-100 space-y-3">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-xs font-medium text-gray-500 mb-1 block">地点</label>
              <input
                value={metadataString(meta, 'location')}
                onChange={(e) => onUpdateMeta('location', e.target.value)}
                placeholder="具体位置..."
                className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 focus:outline-none focus:ring-2 focus:ring-pink-400"
              />
            </div>
            <div>
              <label className="text-xs font-medium text-gray-500 mb-1 block">时间/天气</label>
              <input
                value={metadataString(meta, 'time_weather')}
                onChange={(e) => onUpdateMeta('time_weather', e.target.value)}
                placeholder="如：深夜、暴雨..."
                className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 focus:outline-none focus:ring-2 focus:ring-pink-400"
              />
            </div>
          </div>
          <div>
            <label className="text-xs font-medium text-gray-500 mb-1 block">氛围关键词</label>
            <input
              value={metadataStringArray(meta, 'mood_keywords').join(', ')}
              onChange={(e) => onUpdateMeta('mood_keywords', e.target.value.split(/[,，]/).map((s: string) => s.trim()).filter(Boolean))}
              placeholder="用逗号分隔，如：阴森、寂静..."
              className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 focus:outline-none focus:ring-2 focus:ring-pink-400"
            />
          </div>
        </div>
      )}

      {/* Main content */}
      <div className="flex-1 overflow-y-auto">
        <textarea
          value={data.content}
          onChange={(e) => onUpdate({ content: e.target.value })}
          placeholder={isLocation ? '描述这个地点的详细信息...' : '在这里自由记录...'}
          className="w-full h-full px-8 py-6 text-base text-gray-800 leading-relaxed bg-transparent border-none outline-none resize-none placeholder-gray-300"
          style={{ minHeight: '100%' }}
        />
      </div>
    </div>
  );
}
