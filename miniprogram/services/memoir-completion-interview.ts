import type {
  MemoirCompletionIssue,
} from "../domain/memoir-completion-issue";
import { trackAuditAction } from "./audit-tracker";
import { createMemoirVoiceSession } from "./memoir-voice-session-api";
import { pageUrl } from "./page-input";
import {
  readVoiceAuthorizationConsent,
  syncVoiceAuthorizationConsent,
  VOICE_AUTHORIZATION_PAGE_URL,
} from "./voice-authorization";

interface CompletionInterviewOptions {
  projectId: string;
  projectTitle: string;
  issue: MemoirCompletionIssue;
  entry: "chapter_workspace" | "book_completion";
  chapterId?: string;
}

function openVoiceChat(voiceSessionId: string, projectTitle: string) {
  wx.navigateTo({
    url: pageUrl("/pages/voice-chat/index", {
      voiceSessionId,
      mode: "guided_interview",
      interviewType: "ai_interview",
      sessionGoal: "book_completion",
      projectTitle,
    }),
  });
}

export async function openMemoirCompletionInterview(
  options: CompletionInterviewOptions,
): Promise<void> {
  const { issue } = options;
  if (issue.status === "in_progress" && issue.active_session_id) {
    openVoiceChat(issue.active_session_id, options.projectTitle);
    return;
  }
  if (!readVoiceAuthorizationConsent()) {
    wx.navigateTo({ url: VOICE_AUTHORIZATION_PAGE_URL });
    return;
  }
  await syncVoiceAuthorizationConsent();
  const detail = await createMemoirVoiceSession({
    guidance_mode: "guided_interview",
    interview_type: "ai_interview",
    session_goal: "book_completion",
    current_completion_issue_id: issue.id,
    provider: "volc_streaming_asr",
    input_format: "pcm_s16le_16000_mono",
    metadata_json: {
      entry: `miniprogram_${options.entry}`,
      ...(options.chapterId ? { chapter_id: options.chapterId } : {}),
      completion_issue_id: issue.id,
    },
  }, { projectId: options.projectId });
  trackAuditAction(
    "voice_chat.start",
    {
      guidance_mode: "guided_interview",
      interview_type: "ai_interview",
      session_goal: "book_completion",
      entry: options.entry,
    },
    { projectId: options.projectId, voiceSessionId: detail.session.id },
  );
  openVoiceChat(detail.session.id, options.projectTitle);
}
