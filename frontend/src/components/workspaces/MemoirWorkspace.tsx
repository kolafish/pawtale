import { MessageCircleQuestion, PenLine } from 'lucide-react';
import type { Project } from '../../types';
import { ProjectDraftWorkspace } from './ProjectDraftWorkspace';

const MEMOIR_PROMPTS = [
  '这段回忆发生在什么地方？',
  '当时你最清楚记得谁在场？',
  '那个时刻让你产生了什么感受？',
  '多年以后，你怎么看那件事？',
];

interface MemoirWorkspaceProps {
  project: Project;
}

export function MemoirWorkspace({ project }: MemoirWorkspaceProps) {
  return (
    <ProjectDraftWorkspace
      project={project}
      workspacePanel={<MemoirPromptPanel />}
    />
  );
}

function MemoirPromptPanel() {
  return (
    <div className="px-3 py-3 md:px-4">
      <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-gray-700">
        <MessageCircleQuestion size={16} className="text-pink-500" />
        <span>回忆问题</span>
      </div>
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        {MEMOIR_PROMPTS.map((prompt) => (
          <div
            key={prompt}
            className="flex items-start gap-2 rounded-lg border border-pink-100 bg-pink-50/40 px-3 py-2 text-sm text-gray-700"
          >
            <PenLine size={14} className="mt-0.5 flex-shrink-0 text-pink-400" />
            <span>{prompt}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
