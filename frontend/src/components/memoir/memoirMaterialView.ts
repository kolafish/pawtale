import type { MemoirMaterialItem } from '../../types';
import { formatBeijingDateTime } from '../../utils/time';

export function materialInterviewTimestamp(material: MemoirMaterialItem) {
  return material.session_started_at || material.session_created_at || material.created_at;
}

export function formatMaterialDate(material: MemoirMaterialItem) {
  return formatBeijingDateTime(materialInterviewTimestamp(material), {
    year: 'numeric', month: 'long', day: 'numeric',
  });
}

export function formatMaterialTime(material: MemoirMaterialItem) {
  return formatBeijingDateTime(materialInterviewTimestamp(material), { hour: '2-digit', minute: '2-digit' });
}

export function formatMaterialDateTime(material: MemoirMaterialItem) {
  return formatBeijingDateTime(materialInterviewTimestamp(material), {
    year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

export function formatDuration(seconds?: number | null) {
  if (!seconds || seconds < 1) return '0:00';
  const rounded = Math.round(seconds);
  return `${Math.floor(rounded / 60)}:${String(rounded % 60).padStart(2, '0')}`;
}

export function compactMaterialText(value: string, limit = 92) {
  const compact = value.replace(/\s+/g, ' ').trim();
  if (compact.length <= limit) return compact;
  return `${compact.slice(0, limit)}...`;
}

export interface MaterialSessionGroup {
  sessionId: string;
  dateLabel: string;
  timeLabel: string;
  items: MemoirMaterialItem[];
}

export function groupMaterialsBySession(items: MemoirMaterialItem[]): MaterialSessionGroup[] {
  const groups: MaterialSessionGroup[] = [];
  for (const material of items) {
    const previous = groups.at(-1);
    if (previous?.sessionId === material.voice_session_id) {
      previous.items.push(material);
      continue;
    }
    groups.push({
      sessionId: material.voice_session_id,
      dateLabel: formatMaterialDate(material),
      timeLabel: formatMaterialTime(material),
      items: [material],
    });
  }
  return groups;
}
