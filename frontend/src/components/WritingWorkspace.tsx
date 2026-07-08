import type { ComponentType } from 'react';
import { useAppStore } from '../stores/appStore';
import type { Project, ProjectType } from '../types';
import { ProjectHome } from './ProjectHome';
import { ArticleWorkspace } from './workspaces/ArticleWorkspace';
import { MemoirWorkspace } from './workspaces/MemoirWorkspace';
import { NovelWorkspace } from './workspaces/NovelWorkspace';

interface ProjectWorkspaceProps {
  project: Project;
}

const PRODUCT_WORKSPACES: Partial<Record<ProjectType, ComponentType<ProjectWorkspaceProps>>> = {
  novel: NovelWorkspace,
  article: ArticleWorkspace,
  memoir: MemoirWorkspace,
};

export function WritingWorkspace() {
  const currentProject = useAppStore((state) => state.currentProject);

  if (!currentProject) {
    return <ProjectHome />;
  }

  const Workspace = PRODUCT_WORKSPACES[currentProject.type];
  if (!Workspace) {
    return (
      <div className="flex h-full flex-1 items-center justify-center bg-white text-sm text-gray-500">
        不支持的项目类型
      </div>
    );
  }

  return <Workspace project={currentProject} />;
}
