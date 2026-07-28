import type {
  SpeakerTranscriptionConnectionStatus,
  SpeakerTranscriptionGatewayEvent,
} from "../../services/speaker-transcription";
import { readAccessibilitySettings } from "../../services/accessibility-settings";
import { SpeakerTranscriptionClient } from "../../services/speaker-transcription";
import {
  proofreadSpeakerTranscriptLine,
  type SpeakerProofreadContextLine,
} from "../../services/speaker-transcript-proofreading";
import { ensureRecorderPrivacyAuthorization } from "../../services/wechat-privacy";
import {
  applySpeakerTranscriptionProofread,
  initialSpeakerTranscriptionReducerState,
  reduceSpeakerTranscriptionEvent,
  resetSpeakerTranscriptionStream,
  SPEAKER_TRANSCRIPTION_LEVEL_RENDER_INTERVAL_MS,
  speakerTranscriptionHasTranscript,
  speakerTranscriptionPageClass,
  speakerTranscriptionPagePatch,
  speakerTranscriptionViewPatch,
  type SpeakerTranscriptionPageData,
  type SpeakerTranscriptionReducerState,
} from "./speaker-transcription-reducer";

Page<SpeakerTranscriptionPageData, {
  client: SpeakerTranscriptionClient | null;
  pageActive: boolean;
  starting: boolean;
  transcriptionRunSerial: number;
  lastLevelRenderMs: number;
  speakerTranscriptionState: SpeakerTranscriptionReducerState;
  proofreadRequestSerial: number;
  proofreadRequestByLineId: Record<string, number>;
  onLoad(): void;
  onShow(): void;
  onUnload(): void;
  refreshAccessibilitySettings(): void;
  setStatus(status: SpeakerTranscriptionConnectionStatus): void;
  hasTranscript(): boolean;
  shouldRenderAudioLevel(nowMs: number): boolean;
  handleSpeakerEvent(event: SpeakerTranscriptionGatewayEvent): void;
  handleDiarizationStatus(event: SpeakerTranscriptionGatewayEvent): void;
  proofreadFinalLine(
    lineId: string,
    text: string,
    contextLines: SpeakerProofreadContextLine[],
  ): Promise<void>;
  toggleRecording(): Promise<void>;
  clearTranscript(): void;
}>({
  client: null,
  pageActive: true,
  starting: false,
  transcriptionRunSerial: 0,
  lastLevelRenderMs: 0,
  speakerTranscriptionState: initialSpeakerTranscriptionReducerState(),
  proofreadRequestSerial: 0,
  proofreadRequestByLineId: {},

  data: {
    pageClass: "speaker-transcription-page",
    controlPanelClass: "control-panel",
    showFullControls: true,
    statusText: "未开始",
    statusClass: "status-pill",
    primaryButtonText: "开始识别",
    primaryButtonClass: "primary-button",
    recording: false,
    busy: false,
    error: "",
    ...speakerTranscriptionViewPatch(initialSpeakerTranscriptionReducerState()),
    showEmpty: true,
    audioLevelStyle: "width: 0%;",
    clearDisabled: true,
    showDiarizationNotice: false,
    diarizationNotice: "",
    diarizationNoticeClass: "diarization-notice",
  },

  onLoad() {
    this.pageActive = true;
    this.speakerTranscriptionState = initialSpeakerTranscriptionReducerState();
    this.refreshAccessibilitySettings();
  },

  onShow() {
    this.refreshAccessibilitySettings();
  },

  onUnload() {
    this.pageActive = false;
    this.starting = false;
    this.proofreadRequestByLineId = {};
    this.client?.close();
    this.client = null;
  },

  refreshAccessibilitySettings() {
    this.setData({
      pageClass: speakerTranscriptionPageClass(
        readAccessibilitySettings().largeTextEnabled,
      ),
    });
  },

  setStatus(status) {
    this.setData(speakerTranscriptionPagePatch(status, this.hasTranscript()));
  },

  hasTranscript() {
    return speakerTranscriptionHasTranscript(this.speakerTranscriptionState);
  },

  shouldRenderAudioLevel(nowMs) {
    if (!this.data.recording) {
      this.lastLevelRenderMs = nowMs;
      return true;
    }
    if (nowMs - this.lastLevelRenderMs < SPEAKER_TRANSCRIPTION_LEVEL_RENDER_INTERVAL_MS) {
      return false;
    }
    this.lastLevelRenderMs = nowMs;
    return true;
  },

  async toggleRecording() {
    if (this.data.busy || this.starting) {
      return;
    }
    if (this.data.recording) {
      this.client?.stop();
      return;
    }
    if (this.client) {
      return;
    }
    this.starting = true;
    this.lastLevelRenderMs = 0;
    this.setData({
      error: "",
      showDiarizationNotice: false,
      diarizationNotice: "",
      diarizationNoticeClass: "diarization-notice",
    });
    try {
      await ensureRecorderPrivacyAuthorization();
    } catch (error) {
      this.starting = false;
      if (!this.pageActive) {
        return;
      }
      this.setStatus("error");
      this.setData({ error: error instanceof Error ? error.message : "多人转写启动失败" });
      return;
    }
    if (!this.pageActive) {
      this.starting = false;
      return;
    }
    this.transcriptionRunSerial += 1;
    this.speakerTranscriptionState = resetSpeakerTranscriptionStream(
      this.speakerTranscriptionState,
    );
    let client: SpeakerTranscriptionClient;
    client = new SpeakerTranscriptionClient({
      onStatus: (status) => {
        if (!this.pageActive || this.client !== client) {
          return;
        }
        this.setStatus(status);
        if (status === "closed" || status === "error") {
          this.client = null;
        }
      },
      onAudioLevel: (audioLevel) => {
        if (!this.pageActive || this.client !== client || !this.shouldRenderAudioLevel(Date.now())) {
          return;
        }
        const levelPercent = Math.round(Math.max(0, Math.min(1, audioLevel)) * 100);
        this.setData({ audioLevelStyle: `width: ${levelPercent}%;` });
      },
      onSpeakerEvent: (event) => {
        if (this.pageActive && this.client === client) {
          this.handleSpeakerEvent(event);
        }
      },
      onDiarizationStatus: (event) => {
        if (this.pageActive && this.client === client) {
          this.handleDiarizationStatus(event);
        }
      },
      onError: (message) => {
        if (this.pageActive && this.client === client) {
          this.setData({ error: message });
        }
      },
    });
    this.client = client;
    try {
      await client.start();
    } catch (error) {
      if (!this.pageActive || this.client !== client) {
        return;
      }
      client.close();
      this.client = null;
      this.setStatus("error");
      this.setData({ error: error instanceof Error ? error.message : "多人转写启动失败" });
    } finally {
      this.starting = false;
    }
  },

  handleSpeakerEvent(event) {
    const result = reduceSpeakerTranscriptionEvent(
      this.speakerTranscriptionState,
      event,
      { runSerial: this.transcriptionRunSerial },
    );
    this.speakerTranscriptionState = result.state;
    if (!result.patch) {
      return;
    }
    const dataPatch: Partial<SpeakerTranscriptionPageData> = { ...result.patch };
    if (result.hasTranscriptChanged) {
      Object.assign(
        dataPatch,
        speakerTranscriptionPagePatch(
          this.data.recording ? "listening" : "ready",
          result.hasTranscript,
        ),
      );
    }
    this.setData(dataPatch);
    if (result.proofreadRequest) {
      void this.proofreadFinalLine(
        result.proofreadRequest.lineId,
        result.proofreadRequest.text,
        result.proofreadRequest.contextLines,
      );
    }
  },

  handleDiarizationStatus(event) {
    const available = event.status === "available";
    this.setData({
      showDiarizationNotice: true,
      diarizationNotice: event.message || (available
        ? "已收到说话人标签，会按不同人标注显示。"
        : "当前 ASR 返回结果没有说话人标签，暂时只能标成未分辨说话人。"),
      diarizationNoticeClass: available ? "diarization-notice ok" : "diarization-notice warning",
    });
  },

  async proofreadFinalLine(lineId, text, contextLines) {
    this.proofreadRequestSerial += 1;
    const requestId = this.proofreadRequestSerial;
    this.proofreadRequestByLineId[lineId] = requestId;
    try {
      const proofread = await proofreadSpeakerTranscriptLine(text, contextLines);
      if (!this.pageActive || this.proofreadRequestByLineId[lineId] !== requestId) {
        return;
      }
      delete this.proofreadRequestByLineId[lineId];
      const proofreadText = proofread.proofread_text.trim();
      const linePatch = {
        text: proofreadText || text,
        proofreadLoading: false,
        proofreadStatusText: "已校对",
      };
      const reduced = applySpeakerTranscriptionProofread(
        this.speakerTranscriptionState,
        lineId,
        linePatch,
        true,
      );
      this.speakerTranscriptionState = reduced.state;
      this.setData(reduced.patch);
    } catch (_error) {
      if (!this.pageActive || this.proofreadRequestByLineId[lineId] !== requestId) {
        return;
      }
      delete this.proofreadRequestByLineId[lineId];
      const linePatch = {
        proofreadLoading: false,
        proofreadStatusText: "校对失败",
      };
      const reduced = applySpeakerTranscriptionProofread(
        this.speakerTranscriptionState,
        lineId,
        linePatch,
        false,
      );
      this.speakerTranscriptionState = reduced.state;
      this.setData({ conversationLines: reduced.patch.conversationLines });
    }
  },

  clearTranscript() {
    if (this.data.recording || this.data.busy) {
      return;
    }
    this.speakerTranscriptionState = initialSpeakerTranscriptionReducerState();
    this.proofreadRequestByLineId = {};
    this.setData({
      ...speakerTranscriptionViewPatch(this.speakerTranscriptionState),
      audioLevelStyle: "width: 0%;",
      showDiarizationNotice: false,
      diarizationNotice: "",
      diarizationNoticeClass: "diarization-notice",
      ...speakerTranscriptionPagePatch("idle", false),
    });
  },
});
