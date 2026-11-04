import { definePage } from "../../services/page-definition";
import { pageUrl, queryParam, queryChoice, type PageQuery } from "../../services/page-input";
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
    if (detail.destination === "story") {
      returnToStory(detail.projectId, detail.narrationSaved);
    } else if (detail.destination === "material") {
      wx.redirectTo({ url: pageUrl("/pages/transcript-detail/index", { id: detail.utteranceId }) });
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
