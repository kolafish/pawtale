const BEIJING_OFFSET_MS = 8 * 60 * 60 * 1000;
const TIMEZONE_OFFSET_RE = /(?:[zZ]|[+-]\d{2}:?\d{2})$/;

function pad2(value: number): string {
  return String(value).padStart(2, "0");
}

function parseBackendUtcDate(value: string): Date | null {
  const text = value.trim();
  if (!text) {
    return null;
  }
  const normalized = TIMEZONE_OFFSET_RE.test(text) ? text : `${text}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatBeijingDateTime(value: string): string {
  const date = parseBackendUtcDate(value);
  if (!date) {
    return "";
  }
  const beijing = new Date(date.getTime() + BEIJING_OFFSET_MS);
  const month = pad2(beijing.getUTCMonth() + 1);
  const day = pad2(beijing.getUTCDate());
  const hour = pad2(beijing.getUTCHours());
  const minute = pad2(beijing.getUTCMinutes());
  return `${month}-${day} ${hour}:${minute} 北京时间`;
}

export function formatBeijingDateTimeShort(value: string): string {
  const text = formatBeijingDateTime(value);
  return text.replace(/\s*北京时间$/, "");
}

export function timestampMs(value?: string | null): number {
  if (!value) {
    return 0;
  }
  const timestamp = new Date(value).getTime();
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

export function formatPlaybackClock(seconds: number): string {
  const totalSeconds = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(totalSeconds / 60);
  return `${minutes}:${pad2(totalSeconds % 60)}`;
}
