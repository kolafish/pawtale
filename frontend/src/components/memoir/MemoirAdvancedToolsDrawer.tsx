import { BookOpenText, Languages, Sparkles, X } from 'lucide-react';
import { useState } from 'react';

import { useDialogFocus } from '../../hooks/useDialogFocus';

import type { Project } from '../../types';
import { MemoirBatchCleanupTool } from './MemoirBatchCleanupTool';
import { MemoirDialectGlossaryTool } from './MemoirDialectGlossaryTool';
import { MemoirInterviewRelationsTool } from './MemoirInterviewRelationsTool';


interface MemoirAdvancedToolsDrawerProps {
  open: boolean;
  project: Project;
  selectedMaterialId: string | null;
  onClose: () => void;
}

type AdvancedTool = 'cleanup' | 'relations' | 'glossary';

const TOOLS: Array<{
  value: AdvancedTool;
  label: string;
  icon: typeof Sparkles;
}> = [
  { value: 'cleanup', label: '批量校正', icon: Sparkles },
  { value: 'relations', label: '问答关系', icon: BookOpenText },
  { value: 'glossary', label: '方言词表', icon: Languages },
];

export function MemoirAdvancedToolsDrawer({
  open,
  project,
  selectedMaterialId,
  onClose,
}: MemoirAdvancedToolsDrawerProps) {
  const [activeTool, setActiveTool] = useState<AdvancedTool>('cleanup');
  const dialogRef = useDialogFocus({ open, onClose });

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <button
        type="button"
        aria-label="关闭高级工具"
        tabIndex={-1}
        onClick={onClose}
        className="absolute inset-0 bg-black/25 backdrop-blur-[1px]"
      />
      <section
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label="回忆录高级工具"
        className="relative flex h-full w-full flex-col bg-[#f7f8f7] shadow-[-12px_0_36px_rgba(21,35,29,0.16)] sm:max-w-[640px]"
      >
        <header className="flex min-h-16 items-center gap-3 border-b border-black/6 bg-white px-4 sm:px-5">
          <div className="min-w-0 flex-1">
            <h2 className="text-base font-semibold text-[#202824]">高级工具</h2>
            <p className="mt-0.5 truncate text-xs text-[#69736e]">{project.title}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-10 w-10 items-center justify-center rounded-md text-[#69736e] transition-[background-color,color,transform] hover:bg-[#f1f3f2] hover:text-[#202824] active:scale-[0.96]"
            title="关闭"
            aria-label="关闭"
          >
            <X size={18} />
          </button>
        </header>

        <div role="tablist" aria-label="高级工具类型" className="flex gap-1 border-b border-black/6 bg-white px-4 sm:px-5">
          {TOOLS.map((tool) => {
            const Icon = tool.icon;
            const selected = activeTool === tool.value;
            return (
              <button
                key={tool.value}
                type="button"
                role="tab"
                aria-selected={selected}
                onClick={() => setActiveTool(tool.value)}
                className={`relative inline-flex h-12 flex-1 items-center justify-center gap-2 text-sm font-medium transition-[color,background-color,transform] active:scale-[0.98] ${
                  selected
                    ? 'text-[#285f4e] after:absolute after:inset-x-3 after:bottom-0 after:h-0.5 after:bg-[#3c7a67]'
                    : 'text-[#69736e] hover:bg-[#fafbfa] hover:text-[#46504b]'
                }`}
              >
                <Icon size={16} aria-hidden="true" />
                {tool.label}
              </button>
            );
          })}
        </div>

        <div className="min-h-0 flex-1 overflow-hidden">
          {activeTool === 'cleanup' && (
            <MemoirBatchCleanupTool
              projectId={project.id}
              selectedMaterialId={selectedMaterialId}
              canEdit={project.can_edit_memoir_materials !== false}
            />
          )}
          {activeTool === 'relations' && (
            <MemoirInterviewRelationsTool
              projectId={project.id}
              canEdit={project.can_edit_memoir_materials !== false}
            />
          )}
          {activeTool === 'glossary' && (
            <MemoirDialectGlossaryTool
              projectId={project.id}
              canEdit={project.can_edit_memoir_materials !== false}
            />
          )}
        </div>
      </section>
    </div>
  );
}
