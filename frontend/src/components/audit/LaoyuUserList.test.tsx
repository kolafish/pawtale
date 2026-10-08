// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import type { LaoyuUserUsage } from '../../types';
import { LaoyuUserList } from './LaoyuUserList';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it('counts recent UTC activity and shows Beijing dates before opening the user audit', () => {
  vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-10-08T06:00:00Z'));
  const row: LaoyuUserUsage = {
    user: { id: 'review-user', email: 'review@example.test', display_name: '讲述者', role: 'user' },
    created_at: '2026-10-07T20:30:00',
    wechat_bound: true, memoir_project_count: 1, voice_session_count: 1,
    utterance_count: 1, recorded_seconds: 42, last_voice_at: null,
    miniprogram_active_ms: 1000, miniprogram_active_days: 1,
    miniprogram_last_seen_at: '2026-10-01T10:00:00', last_active_at: '2026-10-01T10:00:00',
  };
  const client = new QueryClient();
  client.setQueryData(['audit', 'laoyu-users'], { users: [row] });
  const onOpenAudit = vi.fn();
  render(<QueryClientProvider client={client}><LaoyuUserList onOpenAudit={onOpenAudit} /></QueryClientProvider>);

  expect(screen.getByText('近 7 天活跃 1 人')).toBeTruthy();
  expect(screen.getByText('2026-10-08')).toBeTruthy();
  expect(screen.getByText('10-01 18:00')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '查看审计' }));
  expect(onOpenAudit).toHaveBeenCalledWith('review-user');
  client.clear();
});
