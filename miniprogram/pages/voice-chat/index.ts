import { openOfferedCompletionInterview } from "../../services/memoir-completion-interview";
import { definePage } from "../../services/page-definition";
import { queryParam, queryChoice, type PageQuery } from "../../services/page-input";
import { redirectToTranscripts, returnToStory } from "../../services/navigation";
import type { VoiceSessionEntry, VoiceSessionExitEvent } from "../../components/voice-session/events";

definePage<{ entry: VoiceSessionEntry | null }>()({
  data: { entry: null },
  onLoad(query: PageQuery = {}) {
    this.setData({ entry: {
      voiceSessionId: queryParam(query, "voiceSessionId"),
      projectId: queryParam(query, "projectId"),
      projectTitle: queryParam(query, "projectTitle"),
      module: queryParam(query, "module"),
      mode: queryChoice(query, "mode", ["free_narration", "guided_interview"] as const, "guided_interview"),
      interviewType: queryChoice(query, "interviewType", ["ai_interview", "human_interview"] as const, "ai_interview"),
      sessionGoal: queryChoice(query, "sessionGoal", ["open_narration", "book_completion"] as const, "open_narration"),
    } });
  },
  async exitSession({ detail }: VoiceSessionExitEvent) {
    if (detail.destination === "completion_issue") {
      try { await openOfferedCompletionInterview({ ...detail, replacePage: true }); }
      catch (error) {
        // The original session was already saved and stopped before this handoff.
        // A created supplement can be resumed from the existing issue list.
        this.setData({ entry: null });
        returnToStory(detail.projectId);
        wx.showToast({ title: error instanceof Error ? error.message : "补充问题暂时不可用", icon: "none" });
      }
    } else if (detail.destination === "story") {
      returnToStory(detail.projectId, detail.narrationSaved);
    } else if (detail.destination === "transcripts") {
      redirectToTranscripts();
    } else {
      wx.navigateBack({ delta: 1 });
      if (detail.destination === "completion") {
        wx.showToast({ title: "本次补充已保存", icon: "success" });
      }
    }
  },
});
