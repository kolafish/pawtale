const BEIJING_OFFSET_MS = 8 * 60 * 60 * 1000;
const TIMEZONE_OFFSET_RE = /(?:[zZ]|[+-]\d{2}:?\d{2})$/;

function pad2(value: number): string {
  return String(value).padStart(2, '0');
}

function parseBackendUtcDate(value?: string | number | null): Date | null {
  if (value == null || value === '') return null;
  if (typeof value !== 'string') {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  }
  const text = value.trim();
  if (!text) return null;
  // Calendar dates already name a Beijing day; timezone-less timestamps are UTC.
  const normalized = /^\d{4}-\d{2}-\d{2}$/.test(text)
    ? `${text}T00:00:00+08:00`
    : TIMEZONE_OFFSET_RE.test(text) ? text : `${text}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function timestampMs(value?: string | null): number {
  return parseBackendUtcDate(value)?.getTime() ?? 0;
}

export function formatBeijingDate(value?: string | number | null): string {
  const date = parseBackendUtcDate(value);
  if (!date) return '';
  const beijing = new Date(date.getTime() + BEIJING_OFFSET_MS);
  return `${beijing.getUTCFullYear()}-${pad2(beijing.getUTCMonth() + 1)}-${pad2(beijing.getUTCDate())}`;
}

export function formatBeijingDateTime(
  value?: string | null,
  options: Intl.DateTimeFormatOptions = {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  },
): string {
  const date = parseBackendUtcDate(value);
  return date ? new Intl.DateTimeFormat('zh-CN', {
    ...options, timeZone: 'Asia/Shanghai', hour12: false,
  }).format(date) : '';
}

export function formatBeijingDateTimeShort(value?: string | null): string {
  if (!value) return '';
  const date = parseBackendUtcDate(value);
  if (!date) return '';
  const beijing = new Date(date.getTime() + BEIJING_OFFSET_MS);
  return [
    `${pad2(beijing.getUTCMonth() + 1)}-${pad2(beijing.getUTCDate())}`,
    `${pad2(beijing.getUTCHours())}:${pad2(beijing.getUTCMinutes())}`,
  ].join(' ');
}
