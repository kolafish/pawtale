import { definePage } from "../../services/page-definition";
import type { PageQuery } from "../../services/page-input";
import { redirectToTranscripts, returnToStory } from "../../services/navigation";
import type { VoiceSessionExitEvent } from "../../components/voice-session/events";

definePage<{ entry: PageQuery | null }>()({
  data: { entry: null },
  onLoad(entry: PageQuery = {}) { this.setData({ entry }); },
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
