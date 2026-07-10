import type { MemoirMaterialItem } from '../../types';


const CHINA_TIME_ZONE = 'Asia/Shanghai';

const dateFormatter = new Intl.DateTimeFormat('zh-CN', {
  timeZone: CHINA_TIME_ZONE,
  year: 'numeric',
  month: 'long',
  day: 'numeric',
});

const timeFormatter = new Intl.DateTimeFormat('zh-CN', {
  timeZone: CHINA_TIME_ZONE,
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

const dateTimeFormatter = new Intl.DateTimeFormat('zh-CN', {
  timeZone: CHINA_TIME_ZONE,
  year: 'numeric',
  month: 'long',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

export function materialInterviewTimestamp(material: MemoirMaterialItem) {
  return material.session_started_at || material.session_created_at || material.created_at;
}

export function formatMaterialDate(material: MemoirMaterialItem) {
  return dateFormatter.format(new Date(materialInterviewTimestamp(material)));
}

export function formatMaterialTime(material: MemoirMaterialItem) {
  return timeFormatter.format(new Date(materialInterviewTimestamp(material)));
}

export function formatMaterialDateTime(material: MemoirMaterialItem) {
  return dateTimeFormatter.format(new Date(materialInterviewTimestamp(material)));
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
