import type { ComponentType } from 'react';
import { useAppStore } from '../stores/appStore';
import type { Project, ProjectType } from '../types';
import { ProjectHome } from './ProjectHome';
import { NovelWorkspace } from './workspaces/NovelWorkspace';

interface ProjectWorkspaceProps {
  project: Project;
}

const PRODUCT_WORKSPACES: Partial<Record<ProjectType, ComponentType<ProjectWorkspaceProps>>> = {
  novel: NovelWorkspace,
};

export function WritingWorkspace() {
  const currentProject = useAppStore((state) => state.currentProject);

  if (!currentProject) {
    return <ProjectHome />;
  }

  const Workspace = PRODUCT_WORKSPACES[currentProject.type] ?? NovelWorkspace;
  return <Workspace project={currentProject} />;
}
