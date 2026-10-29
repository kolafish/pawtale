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
        <h1 className="text-base font-semibold text-[#202824]">全书信息</h1>
        <span className="ml-auto text-xs text-[#69736e]">只读</span>
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
          <TextField label="前言" value={form.preface} />
          <TextField label="后记" value={form.afterword} />
          <TextField label="封底语" value={form.back_cover_quote} />
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

function TextField({ label, value }: { label: string; value: string }) {
  return (
    <dl className="text-xs text-[#69736e]">
      <dt>{label}</dt>
      <dd className="mt-1.5 whitespace-pre-wrap break-words text-sm leading-6 text-[#29322e]">{value || '未填写'}</dd>
    </dl>
  );
}
