import type { MemoirBookCoverStyle, MemoirBookOutputForm } from '../../types';

const COVER_STYLES: Record<MemoirBookCoverStyle, string> = {
  classic_plain: '素雅米白',
  warm_gray: '暖灰',
  dark_album: '深色相册',
  moonlit_courtyard: '月下庭院',
  navy_gilt: '藏蓝烫金',
  heritage_classic: '传世经典',
  warm_journal: '温暖手记',
  modern_minimal: '现代简约',
};

export function MemoirBookSettings({ form }: { form: MemoirBookOutputForm }) {
  return (
    <aside className="flex h-full min-h-0 flex-col bg-white">
      <div className="flex min-h-14 items-center border-b border-black/6 px-4 py-2">
        <h1 className="text-base font-semibold text-[#202824]">全书设置</h1>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <SettingsSection title="书名与署名">
          <TextField label="书名" value={form.title} />
          <TextField label="副标题" value={form.subtitle} />
          <div className="grid grid-cols-2 gap-2">
            <TextField label="口述者" value={form.narrator_name} />
            <TextField label="整理者" value={form.prepared_by} />
          </div>
        </SettingsSection>
        <SettingsSection title="前后文案">
          <TextField label="前言" value={form.preface} multiline />
          <TextField label="后记" value={form.afterword} multiline />
          <TextField label="封底语" value={form.back_cover_quote} multiline />
        </SettingsSection>
        <SettingsSection title="封面风格">
          <p className="text-sm text-[#69736e]">{COVER_STYLES[form.cover_style]}</p>
        </SettingsSection>
      </div>
    </aside>
  );
}

function SettingsSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-b border-black/6 px-4 py-4">
      <h2 className="mb-3 text-sm font-semibold text-[#37413c]">{title}</h2>
      <div className="space-y-3">{children}</div>
    </section>
  );
}

function TextField({ label, value, multiline = false }: { label: string; value: string; multiline?: boolean }) {
  return (
    <label className="block text-xs text-[#69736e]">
      {label}
      {multiline ? (
        <textarea aria-label={label} value={value} readOnly rows={5} className="mt-1.5 w-full rounded-md border border-black/10 bg-[#f7f8f7] px-3 py-2 text-sm leading-6 text-[#29322e]" />
      ) : (
        <input aria-label={label} value={value} readOnly className="mt-1.5 h-10 w-full rounded-md border border-black/10 bg-[#f7f8f7] px-3 text-sm text-[#29322e]" />
      )}
    </label>
  );
}
