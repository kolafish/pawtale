import { definePage } from "../../services/page-definition";
import { queryParam, queryChoice, type PageQuery } from "../../services/page-input";
import { redirectToTranscripts, returnToStory } from "../../services/navigation";
import type { VoiceSessionEntry, VoiceSessionExitEvent } from "../../components/voice-session/events";

definePage<{ entry: VoiceSessionEntry | null }>()({
  data: { entry: null },
  onLoad(query: PageQuery = {}) {
    this.setData({ entry: {
      voiceSessionId: queryParam(query, "voiceSessionId"),
      projectTitle: queryParam(query, "projectTitle"),
      module: queryParam(query, "module"),
      mode: queryChoice(query, "mode", ["free_narration", "guided_interview"] as const, "free_narration"),
      interviewType: queryChoice(query, "interviewType", ["ai_interview", "human_interview"] as const, "ai_interview"),
      sessionGoal: queryChoice(query, "sessionGoal", ["open_narration", "book_completion"] as const, "open_narration"),
    } });
  },
  exitSession({ detail }: VoiceSessionExitEvent) {
    if (detail.destination === "story") {
      returnToStory(detail.projectId, detail.narrationSaved);
    } else if (detail.destination === "transcripts") {
      redirectToTranscripts();
    } else if (detail.explainSaved) {
      wx.showModal({
        title: "本次补充已保存",
        content: "回答已经直接放回相关小章节。返回后重新整理本章，就能生成更新后的正文。",
        confirmText: "返回本章",
        showCancel: false,
        success: () => { wx.navigateBack({ delta: 1 }); },
      });
    } else {
      wx.navigateBack({ delta: 1 });
    }
  },
});
