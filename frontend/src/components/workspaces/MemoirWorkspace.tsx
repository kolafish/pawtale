import type { Project } from '../../types';
import { MemoirInterviewEditor } from '../memoir/MemoirInterviewEditor';

interface MemoirWorkspaceProps {
  project: Project;
}

export function MemoirWorkspace({ project }: MemoirWorkspaceProps) {
  return <MemoirInterviewEditor project={project} />;
}
