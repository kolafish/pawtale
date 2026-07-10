import { Loader2, Save, Sparkles, WandSparkles } from 'lucide-react';

import type {
  MemoirBookCoverStyle,
  MemoirBookOutputForm,
} from '../../types';


interface MemoirBookSettingsProps {
  form: MemoirBookOutputForm;
  canEdit: boolean;
  hasUnsavedChanges: boolean;
  isSaving: boolean;
  isGeneratingCopy: boolean;
  isSuggestingTitles: boolean;
  titleSuggestions: string[];
  onChange: (patch: Partial<MemoirBookOutputForm>) => void;
  onSave: () => void;
  onGenerateCopy: () => void;
  onSuggestTitles: () => void;
}

const COVER_STYLES: Array<{
  value: MemoirBookCoverStyle;
  label: string;
  swatch: string;
}> = [
  { value: 'classic_plain', label: '素雅米白', swatch: '#e8dccb' },
  { value: 'warm_gray', label: '暖灰', swatch: '#d8d4cc' },
  { value: 'dark_album', label: '深色相册', swatch: '#48423d' },
  { value: 'moonlit_courtyard', label: '月下庭院', swatch: '#d9c39b' },
  { value: 'navy_gilt', label: '藏蓝烫金', swatch: '#263848' },
];

export function MemoirBookSettings({
  form,
  canEdit,
  hasUnsavedChanges,
  isSaving,
  isGeneratingCopy,
  isSuggestingTitles,
  titleSuggestions,
  onChange,
  onSave,
  onGenerateCopy,
  onSuggestTitles,
}: MemoirBookSettingsProps) {
  return (
    <aside className="flex h-full min-h-0 flex-col bg-white">
      <div className="flex min-h-14 items-center gap-2 border-b border-black/6 px-4 py-2">
        <div className="min-w-0 flex-1">
          <h1 className="text-base font-semibold text-[#202824]">全书设置</h1>
          <p className="mt-0.5 text-xs text-[#8a948f]">
            {hasUnsavedChanges ? '有未保存修改' : '设置已保存'}
          </p>
        </div>
        <button
          type="button"
          onClick={onSave}
          disabled={!canEdit || !hasUnsavedChanges || !form.title.trim() || isSaving}
          className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-[#3c7a67] px-3 text-sm font-medium text-white transition-[background-color,transform] hover:bg-[#326956] active:scale-[0.96] disabled:opacity-45"
        >
          {isSaving ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />}
          保存
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <SettingsSection title="书名与署名">
          <FieldLabel label="书名" htmlFor="memoir-book-title">
            <div className="flex gap-2">
              <input
                id="memoir-book-title"
                value={form.title}
                onChange={event => onChange({ title: event.target.value })}
                disabled={!canEdit}
                maxLength={120}
                className="h-10 min-w-0 flex-1 rounded-md border border-black/10 bg-white px-3 text-sm text-[#29322e] outline-none focus:border-[#3c7a67] focus:shadow-[0_0_0_3px_rgba(60,122,103,0.12)] disabled:bg-[#f3f5f4]"
              />
              <button
                type="button"
                onClick={onSuggestTitles}
                disabled={!canEdit || isSuggestingTitles}
                aria-label="推荐书名"
                title="推荐书名"
                className="inline-flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-md bg-[#f1f3f2] text-[#59635e] transition-[background-color,color,transform] hover:bg-[#e8ecea] hover:text-[#29322e] active:scale-[0.96] disabled:opacity-45"
              >
                {isSuggestingTitles ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={15} />}
              </button>
            </div>
          </FieldLabel>
          {titleSuggestions.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {titleSuggestions.map(title => (
                <button
                  key={title}
                  type="button"
                  onClick={() => onChange({ title })}
                  className="min-h-9 rounded-md bg-[#f1f3f2] px-2.5 py-1 text-left text-xs text-[#59635e] transition-[background-color,color,transform] hover:bg-[#e8ecea] hover:text-[#29322e] active:scale-[0.96]"
                >
                  {title}
                </button>
              ))}
            </div>
          )}
          <FieldLabel label="副标题" htmlFor="memoir-book-subtitle">
            <input
              id="memoir-book-subtitle"
              value={form.subtitle}
              onChange={event => onChange({ subtitle: event.target.value })}
              disabled={!canEdit}
              maxLength={180}
              className={INPUT_CLASS}
            />
          </FieldLabel>
          <div className="grid grid-cols-2 gap-2">
            <FieldLabel label="口述者" htmlFor="memoir-book-narrator">
              <input
                id="memoir-book-narrator"
                value={form.narrator_name}
                onChange={event => onChange({ narrator_name: event.target.value })}
                disabled={!canEdit}
                maxLength={80}
                className={INPUT_CLASS}
              />
            </FieldLabel>
            <FieldLabel label="整理者" htmlFor="memoir-book-prepared-by">
              <input
                id="memoir-book-prepared-by"
                value={form.prepared_by}
                onChange={event => onChange({ prepared_by: event.target.value })}
                disabled={!canEdit}
                maxLength={80}
                className={INPUT_CLASS}
              />
            </FieldLabel>
          </div>
        </SettingsSection>

        <SettingsSection
          title="前后文案"
          action={(
            <button
              type="button"
              onClick={onGenerateCopy}
              disabled={!canEdit || isGeneratingCopy || hasUnsavedChanges}
              className="inline-flex h-9 items-center gap-1.5 rounded-md bg-[#f1f3f2] px-2 text-xs text-[#59635e] transition-[background-color,color,transform] hover:bg-[#e8ecea] hover:text-[#29322e] active:scale-[0.96] disabled:opacity-40"
              title={hasUnsavedChanges ? '请先保存当前设置' : 'AI 补全文案'}
            >
              {isGeneratingCopy ? <Loader2 size={14} className="animate-spin" /> : <WandSparkles size={14} />}
              AI 补全
            </button>
          )}
        >
          <FieldLabel label="前言" htmlFor="memoir-book-preface">
            <textarea
              id="memoir-book-preface"
              value={form.preface}
              onChange={event => onChange({ preface: event.target.value })}
              disabled={!canEdit}
              maxLength={1200}
              rows={5}
              className={TEXTAREA_CLASS}
            />
          </FieldLabel>
          <FieldLabel label="后记" htmlFor="memoir-book-afterword">
            <textarea
              id="memoir-book-afterword"
              value={form.afterword}
              onChange={event => onChange({ afterword: event.target.value })}
              disabled={!canEdit}
              maxLength={1200}
              rows={5}
              className={TEXTAREA_CLASS}
            />
          </FieldLabel>
          <FieldLabel label="封底语" htmlFor="memoir-book-back-cover-quote">
            <textarea
              id="memoir-book-back-cover-quote"
              value={form.back_cover_quote}
              onChange={event => onChange({ back_cover_quote: event.target.value })}
              disabled={!canEdit}
              maxLength={160}
              rows={3}
              className={TEXTAREA_CLASS}
            />
          </FieldLabel>
        </SettingsSection>

        <SettingsSection title="封面风格">
          <div className="grid grid-cols-2 gap-2">
            {COVER_STYLES.map(style => {
              const selected = form.cover_style === style.value;
              return (
                <button
                  key={style.value}
                  type="button"
                  aria-pressed={selected}
                  onClick={() => onChange({ cover_style: style.value })}
                  disabled={!canEdit}
                  className={`flex min-h-11 items-center gap-2 rounded-md px-2 text-left text-xs transition-[background-color,box-shadow,transform] active:scale-[0.96] disabled:opacity-50 ${
                    selected
                      ? 'bg-[#edf5f1] text-[#245c4b] shadow-[inset_0_0_0_1px_rgba(60,122,103,0.24)]'
                      : 'bg-[#f7f8f7] text-[#69736e] hover:bg-[#f1f3f2]'
                  }`}
                >
                  <span
                    className="h-6 w-6 flex-shrink-0 rounded shadow-[0_0_0_1px_rgba(0,0,0,0.1)]"
                    style={{ backgroundColor: style.swatch }}
                  />
                  <span>{style.label}</span>
                </button>
              );
            })}
          </div>
        </SettingsSection>
      </div>
    </aside>
  );
}

const INPUT_CLASS = 'h-10 w-full rounded-md border border-black/10 bg-white px-3 text-sm text-[#29322e] outline-none focus:border-[#3c7a67] focus:shadow-[0_0_0_3px_rgba(60,122,103,0.12)] disabled:bg-[#f3f5f4]';
const TEXTAREA_CLASS = 'w-full resize-y rounded-md border border-black/10 bg-white px-3 py-2 text-sm leading-6 text-[#29322e] outline-none focus:border-[#3c7a67] focus:shadow-[0_0_0_3px_rgba(60,122,103,0.12)] disabled:bg-[#f3f5f4]';

function SettingsSection({
  title,
  action,
  children,
}: {
  title: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="border-b border-black/6 px-4 py-4">
      <div className="mb-3 flex min-h-9 items-center gap-2">
        <h2 className="text-sm font-semibold text-[#37413c]">{title}</h2>
        {action && <div className="ml-auto">{action}</div>}
      </div>
      <div className="space-y-3">{children}</div>
    </section>
  );
}

function FieldLabel({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor: string;
  children: React.ReactNode;
}) {
  return (
    <div className="block">
      <label htmlFor={htmlFor} className="mb-1.5 block text-xs font-medium text-[#69736e]">{label}</label>
      {children}
    </div>
  );
}
