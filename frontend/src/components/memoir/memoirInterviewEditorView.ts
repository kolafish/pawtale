import type { MemoirInterviewEditorSessionSummary } from '../../types';

export function sessionSummary(session: MemoirInterviewEditorSessionSummary) {
  return (session.summary_text || session.last_message_text || '').replace(/\s+/g, ' ').trim();
}

export function sessionTitle(session: MemoirInterviewEditorSessionSummary) {
  const summary = sessionSummary(session);
  if (!summary) return '未命名素材';
  const firstSentence = summary.split(/[。！？!?；;]/).map((item) => item.trim()).find(Boolean);
  const title = firstSentence || summary;
  return title.length > 30 ? `${title.slice(0, 30)}...` : title;
}
