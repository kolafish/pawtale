import type { Project } from '../../types';
import { ProjectDraftWorkspace } from './ProjectDraftWorkspace';

interface ArticleWorkspaceProps {
  project: Project;
}

export function ArticleWorkspace({ project }: ArticleWorkspaceProps) {
  return <ProjectDraftWorkspace project={project} />;
}
