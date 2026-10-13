import { listMemoirCompletionIssues } from "./memoir-completion-issue-api";
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
  entry: "chapter_workspace" | "book_completion" | "voice_opening";
  replacePage?: boolean;
  chapterId?: string;
}

function openVoiceChat(voiceSessionId: string, projectTitle: string, replacePage = false) {
  const options = {
    url: pageUrl("/pages/voice-chat/index", {
      voiceSessionId,
      mode: "guided_interview",
      interviewType: "ai_interview",
      sessionGoal: "book_completion",
      projectTitle,
    }),
  };
  if (replacePage) wx.redirectTo(options);
  else wx.navigateTo(options);
}

export async function openMemoirCompletionInterview(
  options: CompletionInterviewOptions,
): Promise<void> {
  const { issue } = options;
  if (issue.status === "in_progress" && issue.active_session_id) {
    openVoiceChat(issue.active_session_id, options.projectTitle, options.replacePage);
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
  openVoiceChat(detail.session.id, options.projectTitle, options.replacePage);
}


export async function openOfferedCompletionInterview(options: {
  projectId: string; projectTitle: string; issueId: string; replacePage?: boolean;
}): Promise<void> {
  const response = await listMemoirCompletionIssues(options.projectId);
  const issue = response.issues.find((item) => item.id === options.issueId && item.channel === "interview" && item.status !== "closed");
  if (!issue) throw new Error("这项已经处理，可以继续讲别的故事");
  await openMemoirCompletionInterview({ ...options, issue, entry: "voice_opening" });
}
