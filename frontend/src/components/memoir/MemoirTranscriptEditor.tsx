import {
  ArrowLeft,
  ArrowRight,
  BookOpenText,
  ChevronDown,
  Headphones,
  Loader2,
  Pause,
  Play,
  RotateCcw,
  RotateCw,
  Save,
} from 'lucide-react';
import { useRef, useState, type KeyboardEvent } from 'react';

import type { MemoirMaterialItem } from '../../types';
import { formatDuration, formatMaterialDateTime } from './memoirMaterialView';


interface MemoirTranscriptEditorProps {
  material: MemoirMaterialItem;
  audioUrl: string | null;
  draftText: string;
  canEdit: boolean;
  hasUnsavedChanges: boolean;
  isSaving: boolean;
  canSave: boolean;
  hasNext: boolean;
  saveError: string | null;
  onDraftChange: (value: string) => void;
  onSave: () => void;
  onSaveAndNext: () => void;
  onBack: () => void;
}

const PLAYBACK_RATES = [0.75, 1, 1.25, 1.5];

function MemoirAudioPlayer({
  src,
  durationHint,
}: {
  src: string | null;
  durationHint?: number | null;
}) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(durationHint ?? 0);
  const [playbackRate, setPlaybackRate] = useState(1);
  const [isPlaying, setIsPlaying] = useState(false);
  const [hasError, setHasError] = useState(false);

  const seekBy = (seconds: number) => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.currentTime = Math.min(Math.max(0, audio.currentTime + seconds), audio.duration || duration);
  };

  const togglePlayback = async () => {
    const audio = audioRef.current;
    if (!audio) return;
    if (!audio.paused) {
      audio.pause();
      return;
    }
    try {
      await audio.play();
    } catch {
      setHasError(true);
      setIsPlaying(false);
    }
  };

  if (!src) {
    return (
      <div className="flex min-h-20 items-center gap-3 bg-[#f5f6f5] px-4 py-4 text-sm text-[#69736e]">
        <Headphones size={18} aria-hidden="true" />
        这段素材没有可播放的音频
      </div>
    );
  }

  return (
    <div className="bg-[#f5f6f5] px-4 py-4 sm:px-6">
      <audio
        ref={audioRef}
        src={src}
        preload="metadata"
        onLoadedMetadata={(event) => {
          const loadedDuration = event.currentTarget.duration;
          if (Number.isFinite(loadedDuration)) setDuration(loadedDuration);
          event.currentTarget.playbackRate = playbackRate;
        }}
        onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)}
        onPlay={() => {
          setHasError(false);
          setIsPlaying(true);
        }}
        onPause={() => setIsPlaying(false)}
        onEnded={() => setIsPlaying(false)}
        onError={() => {
          setHasError(true);
          setIsPlaying(false);
        }}
      />
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={() => seekBy(-5)}
          className="inline-flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-md text-[#59635e] transition-[background-color,color,transform] hover:bg-white hover:text-[#202824] active:scale-[0.96]"
          title="后退 5 秒"
          aria-label="后退 5 秒"
        >
          <RotateCcw size={17} />
        </button>
        <button
          type="button"
          onClick={togglePlayback}
          className="inline-flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-full bg-[#2f6d59] text-white shadow-[0_3px_9px_rgba(47,109,89,0.24)] transition-[background-color,box-shadow,transform] hover:bg-[#275d4c] hover:shadow-[0_4px_12px_rgba(47,109,89,0.28)] active:scale-[0.96]"
          title={isPlaying ? '暂停' : '播放'}
          aria-label={isPlaying ? '暂停' : '播放'}
        >
          {isPlaying ? <Pause size={18} /> : <Play size={18} className="translate-x-px" />}
        </button>
        <button
          type="button"
          onClick={() => seekBy(5)}
          className="inline-flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-md text-[#59635e] transition-[background-color,color,transform] hover:bg-white hover:text-[#202824] active:scale-[0.96]"
          title="前进 5 秒"
          aria-label="前进 5 秒"
        >
          <RotateCw size={17} />
        </button>
        <div className="min-w-0 flex-1">
          <input
            type="range"
            min={0}
            max={Math.max(duration, 0.1)}
            step={0.1}
            value={Math.min(currentTime, duration || currentTime)}
            onChange={(event) => {
              const nextTime = Number(event.target.value);
              if (audioRef.current) audioRef.current.currentTime = nextTime;
              setCurrentTime(nextTime);
            }}
            aria-label="音频播放进度"
            className="h-5 w-full cursor-pointer accent-[#2f6d59]"
          />
          <div className="flex items-center justify-between text-[11px] text-[#7a847f] tabular-nums">
            <span>{formatDuration(currentTime)}</span>
            <span>{formatDuration(duration)}</span>
          </div>
        </div>
        <label htmlFor="memoir-playback-rate" className="sr-only">播放速度</label>
        <select
          id="memoir-playback-rate"
          value={playbackRate}
          onChange={(event) => {
            const nextRate = Number(event.target.value);
            setPlaybackRate(nextRate);
            if (audioRef.current) audioRef.current.playbackRate = nextRate;
          }}
          className="h-9 flex-shrink-0 rounded-md border border-black/8 bg-white px-2 text-xs text-[#46504b] outline-none focus:border-[#3c7a67]"
        >
          {PLAYBACK_RATES.map((rate) => (
            <option key={rate} value={rate}>{rate}x</option>
          ))}
        </select>
      </div>
      {hasError && <p className="mt-2 text-xs text-[#9b4646]">音频加载失败，请刷新后重试。</p>}
    </div>
  );
}

export function MemoirTranscriptEditor({
  material,
  audioUrl,
  draftText,
  canEdit,
  hasUnsavedChanges,
  isSaving,
  canSave,
  hasNext,
  saveError,
  onDraftChange,
  onSave,
  onSaveAndNext,
  onBack,
}: MemoirTranscriptEditorProps) {
  const handleEditorKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
      event.preventDefault();
      if (canSave) onSave();
    }
  };

  return (
    <main className="flex h-full min-h-0 flex-col bg-[#fdfdfc]">
      <header className="flex min-h-[72px] items-center gap-3 border-b border-black/6 bg-white px-4 py-3 sm:px-6">
        <button
          type="button"
          onClick={onBack}
          className="inline-flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-md text-[#59635e] transition-[background-color,color,transform] hover:bg-[#f3f5f4] hover:text-[#202824] active:scale-[0.96] lg:hidden"
          title="返回素材列表"
          aria-label="返回素材列表"
        >
          <ArrowLeft size={18} />
        </button>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h2 className="truncate text-base font-semibold text-[#202824]">
              {material.summary_title || '未命名讲述'}
            </h2>
            {hasUnsavedChanges ? (
              <span className="flex-shrink-0 rounded bg-[#fff3dc] px-2 py-1 text-[11px] text-[#8a5a20]">未保存</span>
            ) : material.has_manual_correction ? (
              <span className="flex-shrink-0 text-[11px] text-[#2d705b]">已人工校正</span>
            ) : null}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[#7a847f]">
            <span>{formatMaterialDateTime(material)}</span>
            <span className="tabular-nums">{material.display_char_count} 字</span>
            {material.included_chapters.length > 0 && (
              <span className="inline-flex min-w-0 items-center gap-1">
                <BookOpenText size={13} aria-hidden="true" />
                <span className="max-w-56 truncate">
                  {material.included_chapters
                    .map(chapter => (
                      chapter.include_in_book === false
                        ? `${chapter.title}（不收入本书）`
                        : chapter.title
                    ))
                    .join('、')}
                </span>
              </span>
            )}
          </div>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {material.question_text && (
          <section className="border-b border-black/5 bg-[#faf9f6] px-4 py-3 sm:px-6">
            <p className="text-[11px] font-medium text-[#8a7963]">提问上下文</p>
            <p className="mt-1 text-sm leading-6 text-[#59554f]">{material.question_text}</p>
          </section>
        )}

        <section aria-label="原始音频" className="border-b border-black/5">
          <MemoirAudioPlayer
            key={material.id}
            src={audioUrl}
            durationHint={material.audio_duration_seconds}
          />
        </section>

        <section className="px-4 py-5 sm:px-6">
          <div className="mb-2 flex items-center justify-between gap-3">
            <label htmlFor="memoir-material-transcript" className="text-sm font-semibold text-[#29322e]">
              人工校正文
            </label>
            {material.transcription_status === 'pending_transcription' && (
              <span className="text-xs text-[#9a6b2f]">等待转写，可直接录入</span>
            )}
          </div>
          <textarea
            id="memoir-material-transcript"
            value={draftText}
            readOnly={!canEdit}
            onChange={(event) => onDraftChange(event.target.value)}
            onKeyDown={handleEditorKeyDown}
            className="h-[min(52vh,620px)] min-h-[340px] w-full resize-y rounded-md border border-black/10 bg-white px-4 py-3 text-base leading-8 text-[#29322e] shadow-[0_1px_6px_rgba(23,43,35,0.06)] outline-none transition-[border-color,box-shadow,background-color] focus:border-[#3c7a67] focus:shadow-[0_0_0_3px_rgba(60,122,103,0.12)] read-only:bg-[#f5f6f5] read-only:text-[#69736e]"
          />
          {!canEdit && (
            <p className="mt-2 text-xs text-[#8a5a20]">当前账号只有查看权限。</p>
          )}
        </section>

        <details className="group border-t border-black/5 bg-white">
          <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm text-[#59635e] transition-[background-color] hover:bg-[#fafbfa] sm:px-6">
            <ChevronDown
              size={16}
              aria-hidden="true"
              className="transition-transform group-open:rotate-180"
            />
            查看原始识别文字
            <span className="ml-auto text-xs text-[#929b97] tabular-nums">{material.raw_char_count} 字</span>
          </summary>
          <div className="border-t border-black/5 bg-[#f7f8f7] px-4 py-4 sm:px-6">
            <p className="whitespace-pre-wrap text-sm leading-7 text-[#69736e]">
              {material.transcript_raw || '暂无原始识别文字'}
            </p>
          </div>
        </details>
      </div>

      <footer className="border-t border-black/6 bg-white px-4 py-3 sm:px-6">
        {saveError && <p className="mb-2 text-xs text-[#9b4646]">{saveError}</p>}
        <div className="flex items-center justify-end gap-2">
          <button
            type="button"
            disabled={!canSave || isSaving}
            onClick={onSave}
            className="inline-flex h-10 min-w-24 items-center justify-center gap-2 rounded-md bg-[#edf1ef] px-4 text-sm font-medium text-[#34443d] transition-[background-color,opacity,transform] hover:bg-[#e2e8e5] active:scale-[0.96] disabled:cursor-not-allowed disabled:opacity-45"
          >
            {isSaving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
            保存
          </button>
          <button
            type="button"
            disabled={!canSave || !hasNext || isSaving}
            onClick={onSaveAndNext}
            className="inline-flex h-10 min-w-32 items-center justify-center gap-2 rounded-md bg-[#2f6d59] px-4 text-sm font-medium text-white shadow-[0_2px_7px_rgba(47,109,89,0.2)] transition-[background-color,box-shadow,opacity,transform] hover:bg-[#275d4c] hover:shadow-[0_3px_10px_rgba(47,109,89,0.24)] active:scale-[0.96] disabled:cursor-not-allowed disabled:opacity-45"
          >
            保存并下一段
            <ArrowRight size={16} />
          </button>
        </div>
      </footer>
    </main>
  );
}
