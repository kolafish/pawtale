import type { MemoirAudioFile, MemoirAudioTranscription } from "../domain/memoir";
import { transcribeMemoirAudioFile, uploadMemoirAudioFile } from "./memoir";

export const MAX_MEMOIR_RECORDING_MS = 60000;

export interface MemoirAudioRecorderUploaded {
  audioFile: MemoirAudioFile;
  durationSeconds: number;
  durationText: string;
}

export interface MemoirAudioRecorderTranscribed extends MemoirAudioRecorderUploaded {
  transcription: MemoirAudioTranscription;
}

export interface MemoirAudioRecorderSession {
  transcribeAfterUpload?: boolean;
  onStart(): void;
  onStop(): void;
  onMissingFile(): void;
  onUploadStart(): void;
  onUploaded(result: MemoirAudioRecorderUploaded): void | Promise<void>;
  onTranscribed(result: MemoirAudioRecorderTranscribed): void;
  onProcessingError(message: string, uploaded?: MemoirAudioRecorderUploaded): void;
  onProcessingDone(): void;
  onRecorderError(message: string): void;
}

const recorderManager = wx.getRecorderManager();

let recorderBound = false;
let activeSession: MemoirAudioRecorderSession | null = null;
let recordingStartedAt = 0;

export function durationSeconds(durationMs: number): number {
  return Math.max(0, Math.round(durationMs / 1000));
}

export function durationText(seconds?: number | null): string {
  if (typeof seconds !== "number" || seconds <= 0) {
    return "";
  }
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes}:${rest < 10 ? "0" : ""}${rest}`;
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function callActive(session: MemoirAudioRecorderSession, callback: () => void): void {
  if (activeSession === session) {
    callback();
  }
}

function bindRecorderEvents(): void {
  if (recorderBound) {
    return;
  }

  recorderManager.onStart(() => {
    recordingStartedAt = Date.now();
    activeSession?.onStart();
  });

  recorderManager.onStop(async (result) => {
    const session = activeSession;
    if (!session) {
      return;
    }

    callActive(session, () => session.onStop());
    if (!result.tempFilePath) {
      if (activeSession === session) {
        session.onMissingFile();
        activeSession = null;
      }
      return;
    }

    const seconds = durationSeconds(result.duration || Date.now() - recordingStartedAt);
    callActive(session, () => session.onUploadStart());

    let uploaded: MemoirAudioRecorderUploaded | undefined;
    try {
      const audioFile = await uploadMemoirAudioFile(result.tempFilePath, seconds, result.fileSize);
      const savedSeconds = audioFile.duration_seconds ?? seconds;
      const uploadedResult = {
        audioFile,
        durationSeconds: savedSeconds,
        durationText: durationText(savedSeconds),
      };
      uploaded = uploadedResult;
      if (activeSession === session) {
        await session.onUploaded(uploadedResult);
      }

      if (session.transcribeAfterUpload === false) {
        return;
      }

      const transcription = await transcribeMemoirAudioFile(audioFile.id);
      callActive(session, () => session.onTranscribed({
        ...uploadedResult,
        transcription,
      }));
    } catch (error) {
      callActive(session, () => session.onProcessingError(errorMessage(error, "录音处理失败"), uploaded));
    } finally {
      if (activeSession === session) {
        session.onProcessingDone();
        activeSession = null;
      }
    }
  });

  recorderManager.onError((error) => {
    const session = activeSession;
    if (!session) {
      return;
    }
    session.onRecorderError(error.errMsg || "录音失败");
    activeSession = null;
  });

  recorderBound = true;
}

export function startMemoirAudioRecording(session: MemoirAudioRecorderSession): void {
  bindRecorderEvents();
  activeSession = session;
  recorderManager.start({
    duration: MAX_MEMOIR_RECORDING_MS,
    sampleRate: 16000,
    numberOfChannels: 1,
    encodeBitRate: 48000,
    format: "mp3",
  });
}

export function stopMemoirAudioRecording(): void {
  recorderManager.stop();
}

export function clearMemoirAudioRecordingSession(session: MemoirAudioRecorderSession): void {
  if (activeSession === session) {
    activeSession = null;
  }
}
