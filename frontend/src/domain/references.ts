export const REFERENCE_TYPES = {
  CHARACTER: 'character',
  LOCATION: 'location',
  NOTE: 'note',
  OUTLINE: 'outline',
} as const;

export type ReferenceType = typeof REFERENCE_TYPES[keyof typeof REFERENCE_TYPES];

export const SETTING_REFERENCE_TYPES = [
  REFERENCE_TYPES.LOCATION,
  REFERENCE_TYPES.NOTE,
] as const;

export type SettingReferenceType = typeof SETTING_REFERENCE_TYPES[number];

export const DEFAULT_REFERENCE_NAMES: Record<ReferenceType, string> = {
  [REFERENCE_TYPES.CHARACTER]: '新角色',
  [REFERENCE_TYPES.LOCATION]: '新地点',
  [REFERENCE_TYPES.NOTE]: '新笔记',
  [REFERENCE_TYPES.OUTLINE]: '新大纲',
};

export const REFERENCE_TYPE_LABELS: Record<ReferenceType, string> = {
  [REFERENCE_TYPES.CHARACTER]: '人物',
  [REFERENCE_TYPES.LOCATION]: '地点',
  [REFERENCE_TYPES.NOTE]: '笔记',
  [REFERENCE_TYPES.OUTLINE]: '大纲',
};
