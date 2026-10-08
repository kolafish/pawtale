import { formatBeijingDateTimeShort } from '../../utils/time';

export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) return '0 分钟';
  const totalSeconds = Math.round(ms / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours} 小时 ${minutes} 分`;
  if (minutes > 0) return `${minutes} 分 ${seconds} 秒`;
  return `${seconds} 秒`;
}

export function formatDateTime(value?: string | null): string {
  return formatBeijingDateTimeShort(value) || '-';
}
