import type { MemoirStoryCard, MemoirStoryDetail } from "../../domain/memoir";
import { getMemoirStories, getMemoirStoryDetail } from "../../services/memoir";
import { writeMemoirDashboardSnapshot } from "../../stores/memoir-cache";

interface DatasetEvent {
  currentTarget: {
    dataset: {
      id?: string;
    };
  };
}

interface QaRecord {
  id: string;
  question: string;
  sessionTitle: string;
  answerPreview: string;
  transcriptRaw: string;
  transcriptEdited: string;
  audioFileId: string;
  audioCloudFileId: string;
  audioDurationText: string;
  audioLabel: string;
  hasAudio: boolean;
  createdAtText: string;
  expanded: boolean;
  detailLoaded: boolean;
  detailLoading: boolean;
  playing: boolean;
  audioTempUrl: string;
  audioError: string;
}

interface QaData {
  loading: boolean;
  refreshing: boolean;
  error: string;
  totalCount: number;
  audioCount: number;
  records: QaRecord[];
}

interface QaMethods {
  onLoad(): Promise<void>;
  onUnload(): void;
  loadQaRecords(): Promise<void>;
  toggleRecord(event: DatasetEvent): Promise<void>;
  loadRecordDetail(answerId: string): Promise<void>;
  playAudio(event: DatasetEvent): Promise<void>;
  stopAudio(): void;
  patchRecord(answerId: string, patch: Partial<QaRecord>): void;
  setPlaying(answerId: string): void;
  setAudioError(answerId: string, message: string): void;
  openHome(): void;
  openWorkbench(): void;
  openQa(): void;
  openProfile(): void;
}

interface AudioContextLike {
  src: string;
  play(): void;
  stop(): void;
  destroy(): void;
  onEnded(callback: () => void): void;
  onError(callback: (error: { errMsg?: string }) => void): void;
}

interface CloudWithTempFileUrl {
  getTempFileURL(options: {
    fileList: string[];
    success(result: { fileList: Array<{ tempFileURL?: string }> }): void;
    fail(error: { errMsg?: string }): void;
  }): void;
}

interface WxWithAudioContext {
  createInnerAudioContext(): AudioContextLike;
}

let audioContext: AudioContextLike | null = null;
let playingAnswerId = "";
let activePage: Pick<QaMethods, "setPlaying" | "setAudioError"> | null = null;

function formatDate(value: string): string {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  const month = date.getMonth() + 1;
  const day = date.getDate();
  const hour = date.getHours();
  const minute = date.getMinutes();
  return `${month}月${day}日 ${hour}:${minute < 10 ? "0" : ""}${minute}`;
}

function formatDuration(seconds?: number | null): string {
  if (typeof seconds !== "number" || seconds <= 0) {
    return "";
  }
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes}:${rest < 10 ? "0" : ""}${rest}`;
}

function audioLabel(hasAudio: boolean, durationText: string): string {
  if (!hasAudio) {
    return "无录音";
  }
  return durationText ? `录音 ${durationText}` : "有录音";
}

function recordFromStory(story: MemoirStoryCard): QaRecord {
  const audioDurationText = formatDuration(story.duration_seconds);
  const hasAudio = Boolean(story.audio_file_id || story.duration_seconds);
  return {
    id: story.answer_id,
    question: story.question_prompt,
    sessionTitle: story.session_title,
    answerPreview: story.answer_preview,
    transcriptRaw: "",
    transcriptEdited: "",
    audioFileId: story.audio_file_id || "",
    audioCloudFileId: "",
    audioDurationText,
    audioLabel: audioLabel(hasAudio, audioDurationText),
    hasAudio,
    createdAtText: formatDate(story.created_at),
    expanded: false,
    detailLoaded: false,
    detailLoading: false,
    playing: false,
    audioTempUrl: "",
    audioError: "",
  };
}

function detailPatch(detail: MemoirStoryDetail, audioTempUrl: string, audioError: string): Partial<QaRecord> {
  const audioFile = detail.audio_file || null;
  const audioDurationText = formatDuration(audioFile?.duration_seconds ?? detail.answer.duration_seconds);
  const hasAudio = Boolean(audioFile?.file_id || detail.answer.audio_file_id);
  return {
    transcriptRaw: detail.answer.transcript_raw || "",
    transcriptEdited: detail.answer.transcript_edited || "",
    audioFileId: audioFile?.id || detail.answer.audio_file_id || "",
    audioCloudFileId: audioFile?.file_id || "",
    audioDurationText,
    audioLabel: audioLabel(hasAudio, audioDurationText),
    hasAudio,
    audioTempUrl,
    audioError,
    detailLoaded: true,
  };
}

function getTempFileUrl(fileId: string): Promise<string> {
  return new Promise((resolve, reject) => {
    (wx.cloud as unknown as CloudWithTempFileUrl).getTempFileURL({
      fileList: [fileId],
      success: (result) => {
        const file = result.fileList[0];
        if (file?.tempFileURL) {
          resolve(file.tempFileURL);
          return;
        }
        reject(new Error("录音播放地址生成失败"));
      },
      fail: (error) => {
        reject(new Error(error.errMsg || "录音播放地址生成失败"));
      },
    });
  });
}

function ensureAudioContext(): AudioContextLike {
  if (audioContext) {
    return audioContext;
  }
  audioContext = (wx as unknown as WxWithAudioContext).createInnerAudioContext();
  audioContext.onEnded(() => {
    playingAnswerId = "";
    activePage?.setPlaying("");
  });
  audioContext.onError((error) => {
    const failedAnswerId = playingAnswerId;
    playingAnswerId = "";
    activePage?.setPlaying("");
    if (failedAnswerId) {
      activePage?.setAudioError(failedAnswerId, error.errMsg || "录音播放失败");
    }
  });
  return audioContext;
}

Page<QaData, QaMethods>({
  data: {
    loading: false,
    refreshing: false,
    error: "",
    totalCount: 0,
    audioCount: 0,
    records: [],
  },

  async onLoad() {
    activePage = this;
    await this.loadQaRecords();
  },

  onUnload() {
    activePage = null;
    if (audioContext) {
      audioContext.stop();
      audioContext.destroy();
      audioContext = null;
    }
    playingAnswerId = "";
  },

  async loadQaRecords() {
    if (this.data.refreshing) {
      return;
    }
    const loading = !this.data.records.length;
    this.setData({ loading, refreshing: true, error: "" });
    try {
      const result = await getMemoirStories();
      const records = result.stories.map(recordFromStory);
      writeMemoirDashboardSnapshot({ stories: result.stories });
      this.setData({
        records,
        totalCount: result.total,
        audioCount: records.filter((record) => record.hasAudio).length,
      });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "问答记录加载失败" });
    } finally {
      this.setData({ loading: false, refreshing: false });
    }
  },

  async toggleRecord(event) {
    const answerId = event.currentTarget.dataset.id;
    if (!answerId) {
      return;
    }
    const record = this.data.records.find((item) => item.id === answerId);
    if (!record) {
      return;
    }
    this.patchRecord(answerId, { expanded: !record.expanded });
    if (!record.expanded && !record.detailLoaded) {
      await this.loadRecordDetail(answerId);
    }
  },

  async loadRecordDetail(answerId) {
    const record = this.data.records.find((item) => item.id === answerId);
    if (!record || record.detailLoading) {
      return;
    }
    this.patchRecord(answerId, { detailLoading: true, audioError: "" });
    try {
      const detail = await getMemoirStoryDetail(answerId);
      let audioTempUrl = "";
      let audioError = "";
      if (detail.audio_file?.file_id) {
        try {
          audioTempUrl = await getTempFileUrl(detail.audio_file.file_id);
        } catch (error) {
          audioError = error instanceof Error ? error.message : "录音播放地址生成失败";
        }
      }
      this.patchRecord(answerId, detailPatch(detail, audioTempUrl, audioError));
    } catch (error) {
      this.patchRecord(answerId, {
        audioError: error instanceof Error ? error.message : "问答详情加载失败",
      });
    } finally {
      this.patchRecord(answerId, { detailLoading: false });
    }
  },

  async playAudio(event) {
    const answerId = event.currentTarget.dataset.id;
    if (!answerId) {
      return;
    }
    if (playingAnswerId === answerId) {
      this.stopAudio();
      return;
    }

    let record = this.data.records.find((item) => item.id === answerId);
    if (!record) {
      return;
    }
    if (!record.detailLoaded) {
      await this.loadRecordDetail(answerId);
      record = this.data.records.find((item) => item.id === answerId);
    }
    if (!record?.audioTempUrl) {
      this.setAudioError(answerId, record?.audioError || "这条问答没有可播放的录音");
      return;
    }

    const context = ensureAudioContext();
    context.stop();
    context.src = record.audioTempUrl;
    playingAnswerId = answerId;
    this.setPlaying(answerId);
    context.play();
  },

  stopAudio() {
    if (audioContext) {
      audioContext.stop();
    }
    playingAnswerId = "";
    this.setPlaying("");
  },

  patchRecord(answerId, patch) {
    this.setData({
      records: this.data.records.map((record) => (
        record.id === answerId ? { ...record, ...patch } : record
      )),
    });
  },

  setPlaying(answerId) {
    this.setData({
      records: this.data.records.map((record) => ({
        ...record,
        playing: Boolean(answerId && record.id === answerId),
      })),
    });
  },

  setAudioError(answerId, message) {
    this.patchRecord(answerId, { audioError: message });
  },

  openHome() {
    wx.redirectTo({ url: "/pages/home/index" });
  },

  openWorkbench() {
    wx.redirectTo({ url: "/pages/workbench/index" });
  },

  openQa() {
  },

  openProfile() {
    wx.redirectTo({ url: "/pages/profile/index" });
  },
});
