import type { Project } from '../../types';
import { MemoirMaterialWorkspace } from '../memoir/MemoirMaterialWorkspace';

interface MemoirWorkspaceProps {
  project: Project;
}

export function MemoirWorkspace({ project }: MemoirWorkspaceProps) {
  return <MemoirMaterialWorkspace project={project} />;
}
