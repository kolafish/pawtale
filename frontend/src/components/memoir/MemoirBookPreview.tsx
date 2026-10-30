import { FileText } from 'lucide-react';

import { DEFAULT_BOOK_DESIGN } from '../../types';
import type {
  MemoirBookOutput,
} from '../../types';


interface MemoirBookPreviewProps {
  output: MemoirBookOutput;
  onOpenDraft: (chapterId: string) => void;
}

const COVER_COLORS = {
  classic_plain: { background: '#eee3d2', text: '#3c2b23', line: '#bca78d' },
  warm_gray: { background: '#e7e3dc', text: '#37332f', line: '#aaa39a' },
  dark_album: { background: '#443e39', text: '#f2eadf', line: '#9a8974' },
  moonlit_courtyard: { background: '#e6d2aa', text: '#442b18', line: '#8f704c' },
  navy_gilt: { background: '#263848', text: '#f1e4bd', line: '#b79a5b' },
  heritage_classic: { background: '#eee3d2', text: '#3c2b23', line: '#bca78d' },
  warm_journal: { background: '#e7e3dc', text: '#37332f', line: '#aaa39a' },
  modern_minimal: { background: '#f3f4f1', text: '#29322e', line: '#9aa39f' },
} as const;

export function MemoirBookPreview({
  output,
  onOpenDraft,
}: MemoirBookPreviewProps) {
  const version = output.latest_version;
  const manuscript = version?.manuscript;
  if (!version) return null;

  const coverStyle = manuscript?.render_settings.cover_style ?? DEFAULT_BOOK_DESIGN;
  const coverColors = COVER_COLORS[coverStyle];
  const preface = manuscript?.front_matter.find(section => section.kind === 'preface');
  const chapters = manuscript?.chapters ?? [];
  const tableOfContents = manuscript?.table_of_contents ?? [];

  return (
    <section aria-label="全书预览" className="relative h-full min-h-0 overflow-y-auto bg-[#e9ebe9] px-3 py-4 sm:px-6 sm:py-6">
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

        {manuscript && <BookSection title="目录">
          <ol className="space-y-3">
            {tableOfContents.map(item => (
              <li key={item.chapter_id} className="flex items-end gap-3 text-sm text-[#4a4641]">
                <span className="tabular-nums">{item.index}.</span>
                <span>{item.title}</span>
                <span className="mb-1 min-w-4 flex-1 border-b border-dotted border-[#c8c1b8]" />
                <span className="text-xs text-[#69736e] tabular-nums">{item.char_count} 字</span>
              </li>
            ))}
          </ol>
        </BookSection>}

        {!manuscript && <BookSection title="正文"><BookParagraph content={version.content} /></BookSection>}

        {chapters.map(chapter => (
          <article key={chapter.chapter_id} className="border-t border-[#e7e1d9] px-6 py-12 sm:px-14 sm:py-16">
            <div className="flex items-start gap-3">
              <div className="min-w-0 flex-1">
                <p className="text-xs text-[#69736e] tabular-nums">第 {chapter.index} 章</p>
                <h2 className="mt-2 text-2xl font-semibold leading-tight text-[#2f2a25] [font-family:'Noto_Serif_SC','Songti_SC',serif] [text-wrap:balance]">
                  {chapter.title}
                </h2>
              </div>
              <div className="flex flex-shrink-0 items-center gap-1">
                <button
                  type="button"
                  onClick={() => onOpenDraft(chapter.chapter_id)}
                  className="inline-flex h-10 items-center justify-center gap-1.5 rounded-md px-2 text-xs text-[#6f675f] transition-[background-color,color,transform] hover:bg-[#f2eee8] hover:text-[#403a34] active:scale-[0.96]"
                >
                  <FileText size={14} />
                  查看正文
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
