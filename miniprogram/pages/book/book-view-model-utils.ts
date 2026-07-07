export function trimText(value: string): string {
  return value.trim();
}

const TIMEZONE_OFFSET_RE = /(?:[zZ]|[+-]\d{2}:?\d{2})$/;

export function createdAtTime(value: string): number {
  const text = value.trim();
  if (!text) {
    return 0;
  }
  const normalized = TIMEZONE_OFFSET_RE.test(text) ? text : `${text}Z`;
  const time = new Date(normalized).getTime();
  return Number.isFinite(time) ? time : 0;
}
