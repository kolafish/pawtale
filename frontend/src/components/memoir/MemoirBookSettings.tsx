import { Loader2, Save } from 'lucide-react';
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

export function MemoirBookSettings({ form, canEdit, dirty, saving, saved, error, onChange, onSave, onDiscard, onReload }: {
  form: MemoirBookOutputForm;
  canEdit: boolean;
  dirty: boolean;
  saving: boolean;
  saved: boolean;
  error: string;
  onChange: (patch: Partial<MemoirBookOutputForm>) => void;
  onSave: () => void;
  onDiscard: () => void;
  onReload: () => void;
}) {
  const disabled = !canEdit || saving;
  return (
    <aside className="flex h-full min-h-0 flex-col bg-white">
      <div className="flex min-h-14 items-center border-b border-black/6 px-4 py-2">
        <h1 className="text-base font-semibold text-[#202824]">全书信息</h1>
        <span role="status" className="ml-auto text-xs text-[#69736e]">{saving ? '正在保存' : dirty ? '待保存' : saved ? '已保存' : !canEdit ? '只读' : ''}</span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <SettingsSection title="书名与署名">
          <TextField label="书名" value={form.title} disabled={disabled} onChange={value => onChange({ title: value })} />
          <TextField label="副标题" value={form.subtitle} disabled={disabled} onChange={value => onChange({ subtitle: value })} />
          <div className="grid grid-cols-2 gap-2">
            <TextField label="口述者" value={form.narrator_name} disabled={disabled} onChange={value => onChange({ narrator_name: value })} />
            <TextField label="整理者" value={form.prepared_by} disabled={disabled} onChange={value => onChange({ prepared_by: value })} />
          </div>
        </SettingsSection>
        <SettingsSection title="前后文案">
          <TextField label="前言" value={form.preface} multiline disabled={disabled} onChange={value => onChange({ preface: value })} />
          <TextField label="后记" value={form.afterword} multiline disabled={disabled} onChange={value => onChange({ afterword: value })} />
          <TextField label="封底语" value={form.back_cover_quote} multiline disabled={disabled} onChange={value => onChange({ back_cover_quote: value })} />
        </SettingsSection>
        <SettingsSection title="封面风格">
          <label className="block text-xs text-[#69736e]">封面风格
            <select aria-label="封面风格" value={form.cover_style} disabled={disabled} onChange={event => onChange({ cover_style: event.target.value as MemoirBookCoverStyle })} className="mt-1.5 min-h-11 w-full rounded-md bg-[#f7f8f7] px-3 text-sm text-[#29322e] outline-none focus-visible:ring-2 focus-visible:ring-[#3c7a67]">
              {Object.entries(COVER_STYLES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
        </SettingsSection>
      </div>
      {canEdit && <footer className="border-t border-black/6 px-4 py-3">
        {error && <div className="mb-2 text-sm text-[#7c3f3f]"><p role="alert">{error}</p><button type="button" onClick={onReload} disabled={saving} className="min-h-11 underline">放弃修改并读取最新版</button></div>}
        <div className="flex items-center justify-end gap-2">
          <button type="button" onClick={onDiscard} disabled={!dirty || saving} className="h-11 rounded-md px-3 text-sm text-[#59635e] disabled:opacity-45">放弃修改</button>
          <button type="button" onClick={onSave} disabled={!dirty || saving || !form.title.trim()} className="inline-flex h-11 items-center gap-2 rounded-md bg-[#3c7a67] px-4 text-sm text-white transition-transform active:scale-[0.96] disabled:opacity-45">{saving ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />}保存信息</button>
        </div>
      </footer>}
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

function TextField({ label, value, multiline = false, disabled, onChange }: {
  label: string;
  value: string;
  multiline?: boolean;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  const className = 'mt-1.5 min-h-11 w-full rounded-md bg-[#f7f8f7] px-3 py-2 text-sm leading-6 text-[#29322e] outline-none focus-visible:ring-2 focus-visible:ring-[#3c7a67]';
  return (
    <label className="block text-xs text-[#69736e]">{label}
      {multiline ? <textarea value={value} disabled={disabled} onChange={event => onChange(event.target.value)} rows={5} className={className} /> : <input value={value} disabled={disabled} onChange={event => onChange(event.target.value)} className={className} />}
    </label>
  );
}
