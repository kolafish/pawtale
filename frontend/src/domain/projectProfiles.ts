import type { ProjectListItem, ProjectType } from '../types';
import { toChineseNumber } from '../utils/numberUtils';

export type ReferenceSectionKey = 'character' | 'outline' | 'notes';

export interface ProjectReferenceSection {
  key: ReferenceSectionKey;
  label: string;
  types: string[];
  createType: string;
}

export interface ProjectProfile {
  type: ProjectType;
  label: string;
  description: string;
  placeholder: string;
  manuscriptLabel: string;
  documentUnit: string;
  groupUnit: string;
  referenceSections: ProjectReferenceSection[];
}

const NOVEL_REFERENCE_SECTIONS: ProjectReferenceSection[] = [
  { key: 'character', label: '人物', types: ['character'], createType: 'character' },
  { key: 'outline', label: '大纲', types: ['outline'], createType: 'outline' },
  { key: 'notes', label: '设定', types: ['location', 'note'], createType: 'note' },
];

const ARTICLE_REFERENCE_SECTIONS: ProjectReferenceSection[] = [
  { key: 'outline', label: '选题', types: ['outline'], createType: 'outline' },
  { key: 'notes', label: '素材', types: ['location', 'note'], createType: 'note' },
];

const MEMOIR_REFERENCE_SECTIONS: ProjectReferenceSection[] = [
  { key: 'character', label: '人物', types: ['character'], createType: 'character' },
  { key: 'notes', label: '素材', types: ['location', 'note'], createType: 'note' },
];

export const PROJECT_PROFILES: Record<ProjectType, ProjectProfile> = {
  novel: {
    type: 'novel',
    label: '小说',
    description: '长篇结构，适合章节、大纲、人物设定。',
    placeholder: '例如：雨夜便利店',
    manuscriptLabel: '手稿',
    documentUnit: '章',
    groupUnit: '卷',
    referenceSections: NOVEL_REFERENCE_SECTIONS,
  },
  article: {
    type: 'article',
    label: '公众号',
    description: '短篇文章，适合选题、正文和发布稿。',
    placeholder: '例如：写给焦虑创作者的一封信',
    manuscriptLabel: '正文',
    documentUnit: '篇',
    groupUnit: '组',
    referenceSections: ARTICLE_REFERENCE_SECTIONS,
  },
  memoir: {
    type: 'memoir',
    label: '回忆录',
    description: '问题驱动，适合记录人生片段和真实经历。',
    placeholder: '例如：外婆家的夏天',
    manuscriptLabel: '回忆',
    documentUnit: '段',
    groupUnit: '组',
    referenceSections: MEMOIR_REFERENCE_SECTIONS,
  },
};

export const PROJECT_TYPE_OPTIONS = Object.values(PROJECT_PROFILES);

export function getProjectProfile(type: ProjectType): ProjectProfile {
  return PROJECT_PROFILES[type];
}

export function formatProjectStructure(project: ProjectListItem): string {
  const profile = getProjectProfile(project.type);
  if (project.type === 'novel') {
    return `${project.group_count} ${profile.groupUnit} · ${project.document_count} ${profile.documentUnit}`;
  }
  return `${project.document_count} ${profile.documentUnit}`;
}

export function formatGroupTitle(type: ProjectType, index: number, name: string): string {
  if (type !== 'novel') return name;

  const prefix = `第${toChineseNumber(index + 1)}卷`;
  return name.startsWith(prefix) ? name : `${prefix} ${name}`;
}

export function formatDocumentTitle(type: ProjectType, index: number, name: string): string {
  if (type !== 'novel') return name;

  const prefix = `第${toChineseNumber(index + 1)}章`;
  return name.startsWith(prefix) ? name : `${prefix} ${name}`;
}

export function defaultGroupName(type: ProjectType, index: number): string {
  if (type === 'novel') return `第${toChineseNumber(index + 1)}卷`;
  if (type === 'article') return index === 0 ? '正文' : `内容组 ${index + 1}`;
  return index === 0 ? '回忆片段' : `回忆组 ${index + 1}`;
}
