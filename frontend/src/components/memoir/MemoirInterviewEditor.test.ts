import { describe, expect, it } from 'vitest';

import { sessionTitle } from './memoirInterviewEditorView';
import type { MemoirInterviewEditorSessionSummary } from '../../types';

function session(overrides: Partial<MemoirInterviewEditorSessionSummary> = {}): MemoirInterviewEditorSessionSummary {
  return {
    id: 'session-1',
    project_id: 'project-1',
    status: 'stopped',
    guidance_mode: 'free_narration',
    interview_type: 'human_interview',
    current_guidance_module: null,
    started_at: '2026-07-08T08:20:00.000Z',
    stopped_at: '2026-07-08T08:25:00.000Z',
    created_at: '2026-07-08T08:20:00.000Z',
    updated_at: '2026-07-08T08:25:00.000Z',
    question_count: 1,
    answer_count: 2,
    unassigned_answer_count: 0,
    total_audio_seconds: 32,
    summary_text: '',
    last_message_text: '',
    last_message_role: null,
    last_message_at: null,
    ...overrides,
  };
}

describe('sessionTitle', () => {
  it('uses the first compact summary sentence as the material title', () => {
    expect(sessionTitle(session({
      summary_text: '靠这些借款有关系。贷款这些都贷得到贷款。',
      last_message_text: '最后一句',
    }))).toBe('靠这些借款有关系');
  });

  it('falls back to the last message and trims long titles', () => {
    const title = sessionTitle(session({
      last_message_text: '这是一段很长很长很长很长很长很长很长很长很长很长很长的讲述内容。',
    }));

    expect(title.endsWith('...')).toBe(true);
    expect(title.length).toBe(33);
  });
});
