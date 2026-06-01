export type Metadata = Record<string, unknown>;

export function metadataString(meta: Metadata, key: string): string {
  const value = meta[key];
  if (typeof value === 'string') return value;
  if (typeof value === 'number') return String(value);
  return '';
}

export function metadataStringArray(meta: Metadata, key: string): string[] {
  const value = meta[key];
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}
