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
