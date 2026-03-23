import { Save } from 'lucide-react';
import type { ReferenceItem } from '../types';
import type { RefSaveStatus } from '../hooks/useReferenceEditor';

interface OutlineEditorProps {
  data: ReferenceItem;
  saveStatus: RefSaveStatus;
  onUpdate: (patch: Partial<ReferenceItem>) => void;
  onSave: () => void;
}

export function OutlineEditor({ data, saveStatus, onUpdate, onSave }: OutlineEditorProps) {
  const statusLabel = saveStatus === 'saved' ? '已保存' : saveStatus === 'saving' ? '保存中...' : '未保存';

  return (
    <div className="h-full flex flex-col bg-white">
      {/* Header */}
      <div className="flex items-center gap-3 px-6 py-3 border-b border-gray-200">
        <input
          value={data.name}
          onChange={(e) => onUpdate({ name: e.target.value })}
          className="text-lg font-semibold text-gray-800 flex-1 bg-transparent border-none outline-none focus:ring-0 placeholder-gray-300"
          placeholder="大纲标题..."
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

      {/* Editor */}
      <div className="flex-1 overflow-y-auto">
        <textarea
          value={data.content}
          onChange={(e) => onUpdate({ content: e.target.value })}
          placeholder="在这里写下你的故事大纲、剧情走向、时间线..."
          className="w-full h-full px-8 py-6 text-base text-gray-800 leading-relaxed bg-transparent border-none outline-none resize-none placeholder-gray-300"
          style={{ minHeight: '100%' }}
        />
      </div>
    </div>
  );
}
