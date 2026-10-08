import { describe, expect, it } from 'vitest';

import type { MemoirMaterialItem } from '../../types';
import { formatMaterialDate, formatMaterialTime } from './memoirMaterialView';


function material(overrides: Partial<MemoirMaterialItem> = {}): MemoirMaterialItem {
  return {
    id: 'material-1',
    voice_session_id: 'session-1',
    project_id: 'project-1',
    project_title: '余仲尤的往事',
    question_text: '',
    transcript_raw: '原始识别文字',
    transcript_edited: '',
    display_text: '原始识别文字',
    has_manual_correction: false,
    summary_title: '讲述片段',
    summary_text: '讲述片段摘要',
    summary_source: 'fallback',
    transcription_status: 'ready',
    raw_char_count: 6,
    edited_char_count: 0,
    display_char_count: 6,
    is_included_in_book: false,
    included_chapters: [],
    created_at: '2026-07-10T08:00:00Z',
    session_created_at: '2026-07-10T08:00:00Z',
    ...overrides,
  };
}

describe('memoir material interview time', () => {
  it('uses the session start time and displays it in China time', () => {
    const item = material({
      session_started_at: '2026-07-05T17:30:00',
      session_created_at: '2026-07-07T08:00:00Z',
    });

    expect(formatMaterialDate(item)).toBe('2026年7月6日');
    expect(formatMaterialTime(item)).toBe('01:30');
  });
});
