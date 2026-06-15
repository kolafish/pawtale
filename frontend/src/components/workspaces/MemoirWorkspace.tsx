import type { Project } from '../../types';
import { ProjectDraftWorkspace } from './ProjectDraftWorkspace';

interface MemoirWorkspaceProps {
  project: Project;
}

export function MemoirWorkspace({ project }: MemoirWorkspaceProps) {
  return <ProjectDraftWorkspace project={project} />;
}
