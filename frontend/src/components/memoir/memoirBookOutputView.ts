import { DEFAULT_BOOK_DESIGN } from '../../types';
import type {
  MemoirBookOutput,
  MemoirBookOutputForm,
} from '../../types';


const DEFAULT_FORM: MemoirBookOutputForm = {
  title: '',
  subtitle: '',
  narrator_name: '',
  prepared_by: '家人整理',
  preface: '',
  afterword: '',
  back_cover_quote: '',
  cover_style: DEFAULT_BOOK_DESIGN,
};

export function bookOutputForm(output: MemoirBookOutput | null | undefined): MemoirBookOutputForm {
  if (!output?.latest_version?.manuscript) {
    return { ...DEFAULT_FORM, title: output?.title ?? '' };
  }
  const manuscript = output.latest_version.manuscript;
  const preface = manuscript.front_matter.find(section => section.kind === 'preface')?.content ?? '';
  return {
    title: output.title,
    subtitle: manuscript.profile.subtitle,
    narrator_name: manuscript.profile.narrator_name,
    prepared_by: manuscript.profile.prepared_by || DEFAULT_FORM.prepared_by,
    preface,
    afterword: manuscript.back_matter.afterword.content,
    back_cover_quote: manuscript.render_settings.back_cover_quote,
    cover_style: manuscript.render_settings.cover_style,
  };
}

export function saveBrowserDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}
