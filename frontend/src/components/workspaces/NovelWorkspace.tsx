import type { Project } from '../../types';
import { ProjectDraftWorkspace } from './ProjectDraftWorkspace';

interface NovelWorkspaceProps {
  project: Project;
}

export function NovelWorkspace({ project }: NovelWorkspaceProps) {
  return (
    <ProjectDraftWorkspace
      project={project}
      showInspector
      enableAnalyze
    />
  );
}
