import { describe, expect, it } from 'vitest';

import { formatBeijingDate, formatBeijingDateTime, formatBeijingDateTimeShort, timestampMs } from './time';

describe('formatBeijingDateTimeShort', () => {
  it('treats backend timestamps without timezone as UTC before showing Beijing time', () => {
    expect(formatBeijingDateTimeShort('2026-07-01T07:38:00')).toBe('07-01 15:38');
  });

  it('keeps explicit timezone offsets authoritative', () => {
    expect(formatBeijingDateTimeShort('2026-07-01T07:38:00+00:00')).toBe('07-01 15:38');
    expect(formatBeijingDateTimeShort('2026-07-01T15:38:00+08:00')).toBe('07-01 15:38');
  });

  it('uses Beijing calendar days across midnight and year boundaries', () => {
    expect(formatBeijingDate('2026-10-07T20:30:00')).toBe('2026-10-08');
    expect(formatBeijingDate('2026-12-31T16:00:00Z')).toBe('2027-01-01');
    expect(formatBeijingDate('2026-10-08')).toBe('2026-10-08');
    expect(formatBeijingDate(Date.parse('2026-10-07T20:30:00Z'))).toBe('2026-10-08');
  });

  it('normalizes legacy UTC for comparisons and full display', () => {
    expect(timestampMs('2026-10-01T10:00:00')).toBe(Date.parse('2026-10-01T10:00:00Z'));
    expect(formatBeijingDateTime('2026-10-07T20:30:00')).toBe('2026/10/08 04:30:00');
    expect(formatBeijingDateTimeShort('2026-12-31T16:30:00')).toBe('01-01 00:30');
    expect(formatBeijingDateTime('invalid')).toBe('');
    expect(formatBeijingDate(null)).toBe('');
  });
});
