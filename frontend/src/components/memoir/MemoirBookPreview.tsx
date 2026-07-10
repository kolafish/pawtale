import { FilePenLine, Loader2, Pencil, X } from 'lucide-react';
import { useState } from 'react';

import type {
  MemoirBookManuscriptChapter,
  MemoirBookOutput,
} from '../../types';


interface MemoirBookPreviewProps {
  output: MemoirBookOutput;
  isUpdatingChapterCopy: boolean;
  chapterCopyError: string | null;
  onResetChapterCopyError: () => void;
  onOpenDraft: (chapterId: string) => void;
  onUpdateChapterCopy: (
    chapterId: string,
    copy: { intro: string; pull_quote: string },
  ) => Promise<boolean>;
}

const COVER_COLORS = {
  classic_plain: { background: '#eee3d2', text: '#3c2b23', line: '#bca78d' },
  warm_gray: { background: '#e7e3dc', text: '#37332f', line: '#aaa39a' },
  dark_album: { background: '#443e39', text: '#f2eadf', line: '#9a8974' },
  moonlit_courtyard: { background: '#e6d2aa', text: '#442b18', line: '#8f704c' },
  navy_gilt: { background: '#263848', text: '#f1e4bd', line: '#b79a5b' },
} as const;

export function MemoirBookPreview({
  output,
  isUpdatingChapterCopy,
  chapterCopyError,
  onResetChapterCopyError,
  onOpenDraft,
  onUpdateChapterCopy,
}: MemoirBookPreviewProps) {
  const [editingChapter, setEditingChapter] = useState<MemoirBookManuscriptChapter | null>(null);
  const version = output.latest_version;
  const manuscript = version?.manuscript;
  const structured = version?.structured_content;
  if (!version) return null;

  const coverStyle = manuscript?.render_settings.cover_style ?? 'classic_plain';
  const coverColors = COVER_COLORS[coverStyle];
  const preface = manuscript?.front_matter.find(section => section.kind === 'preface');
  const chapters = manuscript?.chapters ?? structured?.chapters.map(chapter => ({
    chapter_id: chapter.chapter_id,
    index: chapter.index,
    title: chapter.title,
    subtitle: '',
    intro: '',
    content: chapter.content,
    pull_quotes: [],
  })) ?? [];
  const tableOfContents = manuscript?.table_of_contents ?? structured?.table_of_contents ?? [];

  return (
    <section className="relative h-full min-h-0 overflow-y-auto bg-[#e9ebe9] px-3 py-4 sm:px-6 sm:py-6">
      <div className="mx-auto max-w-[820px] overflow-hidden rounded-md bg-white shadow-[0_8px_30px_rgba(23,43,35,0.12),0_0_0_1px_rgba(0,0,0,0.06)]">
        <section
          className="flex min-h-[420px] flex-col items-center justify-center px-8 py-16 text-center"
          style={{ backgroundColor: coverColors.background, color: coverColors.text }}
        >
          <div className="h-px w-20" style={{ backgroundColor: coverColors.line }} />
          <h1 className="mt-9 max-w-2xl text-4xl font-semibold leading-tight [font-family:'Noto_Serif_SC','Songti_SC',serif] [text-wrap:balance]">
            {manuscript?.profile.title || output.title}
          </h1>
          {manuscript?.profile.subtitle && (
            <p className="mt-5 max-w-xl text-base leading-7 opacity-80 [font-family:'Noto_Serif_SC','Songti_SC',serif]">
              {manuscript.profile.subtitle}
            </p>
          )}
          <div className="mt-12 space-y-2 text-sm opacity-75">
            {manuscript?.profile.narrator_name && <p>口述：{manuscript.profile.narrator_name}</p>}
            {manuscript?.profile.prepared_by && <p>整理：{manuscript.profile.prepared_by}</p>}
          </div>
          <div className="mt-9 h-px w-20" style={{ backgroundColor: coverColors.line }} />
        </section>

        {preface?.enabled !== false && preface?.content && (
          <BookSection title={preface.title || '写在前面'}>
            <BookParagraph content={preface.content} />
          </BookSection>
        )}

        <BookSection title="目录">
          <ol className="space-y-3">
            {tableOfContents.map(item => (
              <li key={item.chapter_id} className="flex items-end gap-3 text-sm text-[#4a4641]">
                <span className="tabular-nums">{item.index}.</span>
                <span>{item.title}</span>
                <span className="mb-1 min-w-4 flex-1 border-b border-dotted border-[#c8c1b8]" />
                <span className="text-xs text-[#8b847c] tabular-nums">{item.char_count} 字</span>
              </li>
            ))}
          </ol>
        </BookSection>

        {chapters.map(chapter => (
          <article key={chapter.chapter_id} className="border-t border-[#e7e1d9] px-6 py-12 sm:px-14 sm:py-16">
            <div className="flex items-start gap-3">
              <div className="min-w-0 flex-1">
                <p className="text-xs text-[#9b8d7d] tabular-nums">第 {chapter.index} 章</p>
                <h2 className="mt-2 text-2xl font-semibold leading-tight text-[#2f2a25] [font-family:'Noto_Serif_SC','Songti_SC',serif] [text-wrap:balance]">
                  {chapter.title}
                </h2>
              </div>
              <div className="flex flex-shrink-0 items-center gap-1">
                {manuscript && (
                  <button
                    type="button"
                    onClick={() => {
                      onResetChapterCopyError();
                      setEditingChapter(chapter);
                    }}
                    aria-label={`编辑“${chapter.title}”章前文案`}
                    title="编辑章前文案"
                    className="inline-flex h-10 w-10 items-center justify-center rounded-md text-[#7a7168] transition-[background-color,color,transform] hover:bg-[#f2eee8] hover:text-[#403a34] active:scale-[0.96]"
                  >
                    <Pencil size={15} />
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => onOpenDraft(chapter.chapter_id)}
                  className="inline-flex h-10 items-center justify-center gap-1.5 rounded-md px-2 text-xs text-[#6f675f] transition-[background-color,color,transform] hover:bg-[#f2eee8] hover:text-[#403a34] active:scale-[0.96]"
                >
                  <FilePenLine size={14} />
                  修改正文
                </button>
              </div>
            </div>
            {chapter.intro && (
              <p className="mt-6 border-l-2 border-[#b79a65] pl-4 text-sm leading-7 text-[#71685f] [font-family:'Noto_Serif_SC','Songti_SC',serif]">
                {chapter.intro}
              </p>
            )}
            {chapter.pull_quotes[0]?.text && (
              <blockquote className="my-8 text-center text-lg leading-8 text-[#8b6c36] [font-family:'Noto_Serif_SC','Songti_SC',serif]">
                “{chapter.pull_quotes[0].text}”
              </blockquote>
            )}
            <BookParagraph content={chapter.content} className="mt-8" />
          </article>
        ))}

        {manuscript?.back_matter.afterword.enabled !== false && manuscript?.back_matter.afterword.content && (
          <BookSection title={manuscript.back_matter.afterword.title || '后记'}>
            <BookParagraph content={manuscript.back_matter.afterword.content} />
          </BookSection>
        )}

        {manuscript?.render_settings.back_cover_quote && (
          <section
            className="flex min-h-[260px] items-center justify-center px-10 py-16 text-center"
            style={{ backgroundColor: coverColors.background, color: coverColors.text }}
          >
            <p className="max-w-xl text-lg leading-9 [font-family:'Noto_Serif_SC','Songti_SC',serif]">
              {manuscript.render_settings.back_cover_quote}
            </p>
          </section>
        )}
      </div>

      {editingChapter && (
        <ChapterCopyDialog
          chapter={editingChapter}
          isSaving={isUpdatingChapterCopy}
          error={chapterCopyError}
          onClose={() => {
            onResetChapterCopyError();
            setEditingChapter(null);
          }}
          onSave={async copy => {
            if (await onUpdateChapterCopy(editingChapter.chapter_id, copy)) {
              onResetChapterCopyError();
              setEditingChapter(null);
            }
          }}
        />
      )}
    </section>
  );
}

function BookSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-t border-[#e7e1d9] px-6 py-12 sm:px-14 sm:py-16">
      <h2 className="text-xl font-semibold text-[#2f2a25] [font-family:'Noto_Serif_SC','Songti_SC',serif]">
        {title}
      </h2>
      <div className="mt-8">{children}</div>
    </section>
  );
}

function BookParagraph({ content, className = '' }: { content: string; className?: string }) {
  return (
    <div className={`whitespace-pre-wrap text-[16px] leading-8 text-[#403a34] [font-family:'Noto_Serif_SC','Songti_SC',serif] [text-wrap:pretty] ${className}`}>
      {content}
    </div>
  );
}

function ChapterCopyDialog({
  chapter,
  isSaving,
  error,
  onClose,
  onSave,
}: {
  chapter: MemoirBookManuscriptChapter;
  isSaving: boolean;
  error: string | null;
  onClose: () => void;
  onSave: (copy: { intro: string; pull_quote: string }) => void;
}) {
  const [intro, setIntro] = useState(chapter.intro);
  const [pullQuote, setPullQuote] = useState(chapter.pull_quotes[0]?.text ?? '');
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/25 p-4 backdrop-blur-[1px]">
      <div role="dialog" aria-modal="true" aria-labelledby="chapter-copy-title" className="w-full max-w-lg rounded-md bg-white shadow-[0_16px_48px_rgba(0,0,0,0.22)]">
        <header className="flex items-center gap-3 border-b border-black/6 px-4 py-3">
          <h3 id="chapter-copy-title" className="min-w-0 flex-1 truncate text-base font-semibold text-[#29322e]">
            {chapter.title}
          </h3>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭章前文案编辑"
            title="关闭"
            className="inline-flex h-10 w-10 items-center justify-center rounded-md text-[#69736e] transition-[background-color,transform] hover:bg-black/5 active:scale-[0.96]"
          >
            <X size={17} />
          </button>
        </header>
        <div className="space-y-4 p-4">
          {error && (
            <div className="rounded-md bg-[#fbf2f2] px-3 py-2 text-sm text-[#7c3f3f]">
              {error}
            </div>
          )}
          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-[#69736e]">章前导语</span>
            <textarea
              value={intro}
              onChange={event => setIntro(event.target.value)}
              rows={5}
              className="w-full resize-y rounded-md border border-black/10 px-3 py-2 text-sm leading-6 text-[#29322e] outline-none focus:border-[#3c7a67] focus:shadow-[0_0_0_3px_rgba(60,122,103,0.12)]"
            />
          </label>
          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-[#69736e]">章节摘句</span>
            <textarea
              value={pullQuote}
              onChange={event => setPullQuote(event.target.value)}
              rows={3}
              className="w-full resize-y rounded-md border border-black/10 px-3 py-2 text-sm leading-6 text-[#29322e] outline-none focus:border-[#3c7a67] focus:shadow-[0_0_0_3px_rgba(60,122,103,0.12)]"
            />
          </label>
        </div>
        <footer className="flex justify-end gap-2 border-t border-black/6 px-4 py-3">
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-10 items-center rounded-md px-3 text-sm text-[#69736e] transition-[background-color,transform] hover:bg-[#f1f3f2] active:scale-[0.96]"
          >
            取消
          </button>
          <button
            type="button"
            onClick={() => onSave({ intro: intro.trim(), pull_quote: pullQuote.trim() })}
            disabled={isSaving}
            className="inline-flex h-10 items-center gap-2 rounded-md bg-[#3c7a67] px-3 text-sm font-medium text-white transition-[background-color,transform] hover:bg-[#326956] active:scale-[0.96] disabled:opacity-50"
          >
            {isSaving && <Loader2 size={15} className="animate-spin" />}
            保存文案
          </button>
        </footer>
      </div>
    </div>
  );
}
