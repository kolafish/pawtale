import { ArrowLeft, Save } from 'lucide-react';
import type { ReferenceItem } from '../types';
import { metadataString, metadataStringArray } from '../utils/metadata';

interface ReferenceSheetProps {
  ref_item: ReferenceItem;
  onChange: (ref: ReferenceItem) => void;
  onSave: () => void;
  onCancel: () => void;
}

export function ReferenceSheet({ ref_item, onChange, onSave, onCancel }: ReferenceSheetProps) {
  const update = (patch: Partial<ReferenceItem>) => onChange({ ...ref_item, ...patch });
  const updateMeta = (key: string, value: unknown) =>
    onChange({ ...ref_item, metadata_json: { ...ref_item.metadata_json, [key]: value } });

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <button onClick={onCancel} className="p-1 rounded hover:bg-gray-100">
          <ArrowLeft size={14} />
        </button>
        <span className="text-xs px-1.5 py-0.5 rounded bg-gray-100 text-gray-500">
          {ref_item.type === 'character' ? '角色' : ref_item.type === 'location' ? '地点' : '笔记'}
        </span>
        <button
          onClick={onSave}
          className="ml-auto inline-flex items-center gap-1 text-xs px-2 py-1 rounded bg-pink-500 text-white hover:bg-pink-600"
        >
          <Save size={12} />
          保存
        </button>
      </div>

      <div>
        <label className="text-xs font-medium text-gray-500 mb-1 block">名称</label>
        <input
          value={ref_item.name}
          onChange={(e) => update({ name: e.target.value })}
          className="w-full px-2 py-1.5 text-sm rounded border border-gray-200 focus:outline-none focus:ring-1 focus:ring-pink-400"
        />
      </div>

      {ref_item.type === 'character' && (
        <>
          <div>
            <label className="text-xs font-medium text-gray-500 mb-1 block">定位</label>
            <input
              value={metadataString(ref_item.metadata_json, 'identity')}
              onChange={(e) => updateMeta('identity', e.target.value)}
              placeholder="如：CEO、大学生..."
              className="w-full px-2 py-1.5 text-sm rounded border border-gray-200 focus:outline-none focus:ring-1 focus:ring-pink-400"
            />
          </div>
          <div>
            <label className="text-xs font-medium text-gray-500 mb-1 block">性格特征</label>
            <input
              value={metadataStringArray(ref_item.metadata_json, 'personality').join(', ')}
              onChange={(e) => updateMeta('personality', e.target.value.split(/[,，]/).map((s: string) => s.trim()).filter(Boolean))}
              placeholder="用逗号分隔"
              className="w-full px-2 py-1.5 text-sm rounded border border-gray-200 focus:outline-none focus:ring-1 focus:ring-pink-400"
            />
          </div>
        </>
      )}

      {ref_item.type === 'location' && (
        <>
          <div>
            <label className="text-xs font-medium text-gray-500 mb-1 block">地点</label>
            <input
              value={metadataString(ref_item.metadata_json, 'location')}
              onChange={(e) => updateMeta('location', e.target.value)}
              className="w-full px-2 py-1.5 text-sm rounded border border-gray-200 focus:outline-none focus:ring-1 focus:ring-pink-400"
            />
          </div>
          <div>
            <label className="text-xs font-medium text-gray-500 mb-1 block">氛围关键词</label>
            <input
              value={metadataStringArray(ref_item.metadata_json, 'mood_keywords').join(', ')}
              onChange={(e) => updateMeta('mood_keywords', e.target.value.split(/[,，]/).map((s: string) => s.trim()).filter(Boolean))}
              placeholder="用逗号分隔"
              className="w-full px-2 py-1.5 text-sm rounded border border-gray-200 focus:outline-none focus:ring-1 focus:ring-pink-400"
            />
          </div>
        </>
      )}

      <div>
        <label className="text-xs font-medium text-gray-500 mb-1 block">内容</label>
        <textarea
          value={ref_item.content}
          onChange={(e) => update({ content: e.target.value })}
          placeholder="详细描述..."
          rows={6}
          className="w-full px-2 py-1.5 text-sm rounded border border-gray-200 focus:outline-none focus:ring-1 focus:ring-pink-400 resize-none"
        />
      </div>
    </div>
  );
}
