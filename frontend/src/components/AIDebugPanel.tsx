import { useMemo, useState } from 'react';
import { Bug, ChevronDown, ChevronRight, Copy, Check } from 'lucide-react';
import type { AIDebugInfo } from '../types';

interface AIDebugPanelProps {
  debugInfo?: AIDebugInfo | null;
  title?: string;
}

function formatJSON(data: unknown): string {
  try {
    return JSON.stringify(data, null, 2);
  } catch {
    return String(data);
  }
}

export function AIDebugPanel({ debugInfo, title = 'AI Debug' }: AIDebugPanelProps) {
  const [open, setOpen] = useState(false);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  const requestText = useMemo(
    () => (debugInfo ? formatJSON(debugInfo.request) : ''),
    [debugInfo]
  );
  const responseText = useMemo(
    () => (debugInfo ? formatJSON(debugInfo.response) : ''),
    [debugInfo]
  );
  const metaText = useMemo(
    () => (debugInfo ? formatJSON(debugInfo.meta) : ''),
    [debugInfo]
  );

  if (!debugInfo) return null;

  const copy = async (key: string, content: string) => {
    try {
      await navigator.clipboard.writeText(content);
      setCopiedKey(key);
      setTimeout(() => setCopiedKey(null), 1200);
    } catch {
      setCopiedKey(null);
    }
  };

  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50/70">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-3 py-2 text-left text-sm text-amber-700 hover:bg-amber-100/60 rounded-lg transition-colors"
      >
        <span className="flex items-center gap-2">
          <Bug size={14} />
          {title}
        </span>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
      </button>

      {open && (
        <div className="px-3 pb-3 space-y-3">
          <div>
            <div className="flex items-center justify-between mb-1">
              <div className="text-xs font-medium text-amber-800">request</div>
              <button
                type="button"
                onClick={() => copy('request', requestText)}
                className="text-xs px-2 py-1 rounded border border-amber-300 text-amber-700 hover:bg-amber-100 flex items-center gap-1"
              >
                {copiedKey === 'request' ? <Check size={12} /> : <Copy size={12} />}
                复制
              </button>
            </div>
            <pre className="text-xs bg-white border border-amber-200 rounded p-2 overflow-auto max-h-44 whitespace-pre-wrap">
              {requestText}
            </pre>
          </div>

          <div>
            <div className="flex items-center justify-between mb-1">
              <div className="text-xs font-medium text-amber-800">response</div>
              <button
                type="button"
                onClick={() => copy('response', responseText)}
                className="text-xs px-2 py-1 rounded border border-amber-300 text-amber-700 hover:bg-amber-100 flex items-center gap-1"
              >
                {copiedKey === 'response' ? <Check size={12} /> : <Copy size={12} />}
                复制
              </button>
            </div>
            <pre className="text-xs bg-white border border-amber-200 rounded p-2 overflow-auto max-h-44 whitespace-pre-wrap">
              {responseText}
            </pre>
          </div>

          <div>
            <div className="flex items-center justify-between mb-1">
              <div className="text-xs font-medium text-amber-800">meta</div>
              <button
                type="button"
                onClick={() => copy('meta', metaText)}
                className="text-xs px-2 py-1 rounded border border-amber-300 text-amber-700 hover:bg-amber-100 flex items-center gap-1"
              >
                {copiedKey === 'meta' ? <Check size={12} /> : <Copy size={12} />}
                复制
              </button>
            </div>
            <pre className="text-xs bg-white border border-amber-200 rounded p-2 overflow-auto max-h-32 whitespace-pre-wrap">
              {metaText}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}
