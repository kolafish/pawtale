// @vitest-environment happy-dom
import { act, cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import type { MemoirBookChapterDraft, MemoirBookCoverStyle, MemoirBookOutput, MemoirBookPlan, MemoirMaterialItem, Project } from '../../types';
import { MemoirWorkspace } from '../workspaces/MemoirWorkspace';
import { MemoirBookWorkspace } from './MemoirBookWorkspace';
import { MemoirChapterDraftWorkspace } from './MemoirChapterDraftWorkspace';
import { MemoirChapterWorkspace } from './MemoirChapterWorkspace';
import { DraftSaveProvider } from '../DraftSaveProvider';
import { createDraftSaves, DraftSavesContext } from '../../hooks/project/draftSaves';

const { transport } = vi.hoisted(() => ({ transport: vi.fn() }));
vi.mock('axios', async importOriginal => {
  const module = await importOriginal<typeof import('axios')>();
  return {
    ...module,
    default: {
      ...module.default,
      create: (options: Parameters<typeof module.default.create>[0]) => module.default.create({ ...options, adapter: transport }),
    },
  };
});

const project: Project = {
  id: 'wechat-project', title: '小程序创建的回忆录', type: 'memoir', groups: [], references: [],
  total_word_count: 0, can_delete: true, created_at: '', updated_at: '',
};

function material(index: number, assigned = false): MemoirMaterialItem {
  return {
    id: `material-${index}`, project_id: project.id, project_title: project.title, voice_session_id: 'session',
    question_text: '', transcript_raw: `素材 ${index}`, transcript_edited: '', display_text: `素材 ${index}`,
    has_manual_correction: false, summary_title: '', summary_text: '', summary_source: 'fallback',
    transcription_status: 'ready', raw_char_count: 5, edited_char_count: 0, display_char_count: 5,
    is_included_in_book: false, included_chapters: [],
    section_assignment: assigned ? {
      chapter_id: 'chapter-1', chapter_title: '童年', chapter_sort_order: 0,
      section_id: 'section-1', section_title: '未收入本书的讲述', section_sort_order: 0, requires_user_assignment: false,
    } : null,
    created_at: '2026-10-05T01:00:00Z', session_created_at: '2026-10-05T01:00:00Z',
  };
}

function plan(): MemoirBookPlan {
  return {
    id: 'plan', project_id: project.id, title: project.title, status: 'draft', created_by: 'ai', metadata_json: {}, created_at: '', updated_at: '',
    chapters: [{
      id: 'chapter-1', plan_id: 'plan', project_id: project.id, title: '童年', sort_order: 0,
      include_in_book: true, source_refs: [], metadata_json: {}, pending_source_count: 0,
      pending_completion_source_count: 0, pending_new_source_count: 0, removed_source_count: 0, created_at: '', updated_at: '',
      sections: [{
        id: 'section-1', title: '未收入本书的讲述', section_type: 'event', sort_order: 0,
        status: 'ready', include_in_book: false, requires_user_assignment: false, version: 1,
        material_count: 1, included_material_count: 0,
        materials: [{ utterance_id: 'material-200', sort_order: 0, text: '素材 200', include_in_book: false }],
      }],
    }],
  };
}

function draft(): MemoirBookChapterDraft {
  const first = {
    id: 'draft-version-1', draft_id: 'draft', project_id: project.id, chapter_id: 'chapter-1', version: 1,
    content: '这是小程序保存的第一版章节正文。', sections: [], created_by: 'ai', prompt_snapshot: '', created_at: '',
  };
  const latest = { ...first, id: 'draft-version-2', version: 2, content: '这是小程序保存的最新章节正文。' };
  return {
    id: 'draft', project_id: project.id, chapter_id: 'chapter-1', status: 'draft', metadata_json: {},
    pending_source_count: 0, removed_source_count: 0, latest_version: latest, versions: [first, latest], created_at: '', updated_at: '',
  };
}

function output(coverStyle: MemoirBookCoverStyle): MemoirBookOutput {
  return {
    id: 'output', project_id: project.id, plan_id: 'plan', title: project.title, status: 'draft', is_stale: false,
    stale_chapter_titles: [], created_at: '', updated_at: '',
    versions: [{ id: 'output-version', version: 3, created_by: 'ai', created_at: '' }],
    latest_version: {
      id: 'output-version', version: 3, content: '全书原始正文', created_by: 'ai', created_at: '',
      manuscript: {
        schema_version: 3,
        profile: { template: coverStyle, title: project.title, subtitle: '家人的故事', narrator_name: '口述者', prepared_by: '家人整理' },
        front_matter: [], table_of_contents: [{ chapter_id: 'chapter-1', index: 1, title: '童年', char_count: 15, source_count: 1 }],
        chapters: [{ chapter_id: 'chapter-1', index: 1, title: '童年', subtitle: '', intro: '', content: '这是小程序已经组装的全书正文。', pull_quotes: [] }],
        back_matter: { afterword: { kind: 'afterword', title: '后记', content: '', enabled: true } },
        render_settings: { cover_style: coverStyle, back_cover_quote: '' },
      },
    },
  };
}

let currentPlan: MemoirBookPlan | null;
let planError: string | null;
let materials: MemoirMaterialItem[];
let currentDraft: MemoirBookChapterDraft | null;
let currentOutput: MemoirBookOutput | null;
let writeError: string | null;

function response(config: InternalAxiosRequestConfig, data: unknown): AxiosResponse {
  return { config, data, status: 200, statusText: 'OK', headers: {} };
}
function missing(config: InternalAxiosRequestConfig, detail: string): never {
  throw new AxiosError(detail, 'ERR_BAD_REQUEST', config, undefined, { ...response(config, { detail }), status: 404 });
}

beforeEach(() => {
  window.history.replaceState({}, '', '/?view=drafts');
  currentPlan = plan(); planError = null; materials = [material(200, true)]; currentDraft = draft(); currentOutput = output('modern_minimal');
  writeError = null;
  window.confirm = vi.fn(() => true);
  transport.mockReset();
  transport.mockImplementation(async (config: InternalAxiosRequestConfig) => {
    const path = `/mobile/memoir/projects/${project.id}`;
    if (config.url === `${path}/book-plan`) {
      if (planError) missing(config, planError);
      if (!currentPlan) missing(config, '章节规划不存在');
      return response(config, structuredClone(currentPlan));
    }
    if (config.url === `/memoir/projects/${project.id}/materials/material-200/transcript` && config.method === 'patch') {
      const text = JSON.parse(config.data).transcript_edited;
      materials[0] = { ...materials[0], display_text: text, transcript_edited: text, has_manual_correction: true };
      currentDraft!.latest_version!.content = '校对后的章节正文';
      currentOutput!.latest_version!.manuscript!.chapters[0].content = '校对后的全书正文';
      return response(config, materials[0]);
    }
    if (config.url === `/memoir/projects/${project.id}/materials`) {
      const { offset, limit } = config.params;
      return response(config, { total: materials.length, offset, limit, items: materials.slice(offset, offset + limit) });
    }
    if (config.url === `${path}/book-plan/chapters/chapter-1/draft/revise` && config.method === 'post') {
      const update = JSON.parse(config.data);
      if (writeError || update.expected_version_id !== currentDraft!.latest_version!.id) {
        throw new AxiosError('Conflict', 'ERR_BAD_REQUEST', config, undefined, { ...response(config, { detail: writeError || '正文已更新，请刷新后再修改' }), status: 409 });
      }
      const latest = { ...currentDraft!.latest_version!, id: 'draft-version-3', version: 3, created_by: 'user',
        sections: currentDraft!.latest_version!.sections.map((section, index) => ({ ...section, content: update.sections[index].content })),
        content: update.sections.map((section: { content: string }) => section.content).join('\n\n') };
      currentDraft = { ...currentDraft!, latest_version: latest, versions: [...currentDraft!.versions, latest] };
      currentOutput!.latest_version!.manuscript!.chapters[0].content = latest.content;
      return response(config, structuredClone(currentDraft));
    }
    if (config.url === `${path}/book-plan/chapters/chapter-1/draft`) {
      if (!currentDraft) missing(config, '章节稿不存在');
      return response(config, structuredClone(currentDraft));
    }
    if (config.url === `${path}/book-output`) {
      if (!currentOutput) missing(config, '全书稿不存在');
      if (config.method === 'patch') {
        const update = JSON.parse(config.data);
        if (writeError || update.expected_version_id !== currentOutput!.latest_version!.id) {
          throw new AxiosError('Conflict', 'ERR_BAD_REQUEST', config, undefined, { ...response(config, { detail: writeError || '全书已更新，请刷新核对后再保存' }), status: 409 });
        }
        const latest = structuredClone(currentOutput!.latest_version!);
        latest.id = 'output-version-4'; latest.version = 4;
        const manuscript = latest.manuscript!;
        for (const key of ['title', 'subtitle', 'narrator_name', 'prepared_by'] as const) {
          if (key in update) manuscript.profile[key] = update[key];
        }
        if ('preface' in update) manuscript.front_matter = [{ kind: 'preface', title: '前言', content: update.preface, enabled: true }];
        if ('afterword' in update) manuscript.back_matter.afterword.content = update.afterword;
        if ('back_cover_quote' in update) manuscript.render_settings.back_cover_quote = update.back_cover_quote;
        if ('cover_style' in update) manuscript.render_settings.cover_style = update.cover_style;
        currentOutput = { ...currentOutput!, title: update.title ?? currentOutput!.title, latest_version: latest };
        return response(config, { ...structuredClone(currentOutput), is_stale: false, stale_chapter_titles: [] });
      }
      return response(config, structuredClone(currentOutput));
    }
    throw new Error(`Unexpected memoir request: ${config.method} ${config.url}`);
  });
});

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function workspace(node: ReactNode, client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })) {
  return render(<QueryClientProvider client={client}><DraftSaveProvider>{node}</DraftSaveProvider></QueryClientProvider>);
}

test('reads every material page and preserves assignment for excluded sections', async () => {
  materials = Array.from({ length: 201 }, (_, index) => material(index, index === 200));
  const page = workspace(<MemoirChapterWorkspace project={project} />);
  expect(await page.findByText('素材 200')).toBeTruthy();
  const unassignedSection = page.getByText('未归入目录').closest('section')!;
  expect(within(unassignedSection).queryByText('素材 200')).toBeNull();
  expect(page.getByText('未收入本书的讲述')).toBeTruthy();
  const materialRequests = transport.mock.calls.map(([config]) => config).filter(config => config.url.endsWith('/materials'));
  expect(materialRequests.map(config => config.params)).toEqual([{ limit: 200, offset: 0 }, { limit: 200, offset: 200 }]);
  expect(materialRequests.every(config => config.method === 'get')).toBe(true);
});

test('a project without a plan shows its material count instead of a route failure', async () => {
  currentPlan = null;
  const page = workspace(<MemoirChapterWorkspace project={project} />);
  expect(await page.findByText('还没有章节目录')).toBeTruthy();
  expect(page.getByText('当前共有 1 段访谈素材，可在素材校对中查看。')).toBeTruthy();
});

test('authorization failures remain failures instead of becoming an empty plan', async () => {
  planError = '项目不存在';
  const page = workspace(<MemoirChapterWorkspace project={project} />);
  expect(await page.findByText('项目不存在')).toBeTruthy();
  expect(page.queryByText('还没有章节目录')).toBeNull();
});

test('reads chapter prose and full history while its plan remains a draft', async () => {
  const page = workspace(<MemoirChapterDraftWorkspace project={project} onOpenChapters={vi.fn()} />);
  const prose = await page.findByRole('textbox', { name: '章节正文' });
  expect((prose as HTMLTextAreaElement).value).toBe('这是小程序保存的最新章节正文。');
  expect((prose as HTMLTextAreaElement).readOnly).toBe(true);
  fireEvent.click(page.getByRole('button', { name: '版本 2' }));
  fireEvent.click(page.getByRole('button', { name: '查看此版本' }));
  expect((page.getByRole('textbox', { name: '章节正文' }) as HTMLTextAreaElement).value).toBe('这是小程序保存的第一版章节正文。');
  expect(transport.mock.calls.some(([config]) => config.url.endsWith('/book-plan/chapters/chapter-1/draft'))).toBe(true);
});

test('a chapter without prose shows a normal empty draft', async () => {
  currentDraft = null;
  const page = workspace(<MemoirChapterDraftWorkspace project={project} onOpenChapters={vi.fn()} />);
  expect(await page.findByPlaceholderText('本章尚未生成正文')).toBeTruthy();
});

test.each(['heritage_classic', 'warm_journal', 'modern_minimal'] as const)('reads current manuscript with %s cover without a confirmation gate', async style => {
  currentOutput = output(style);
  const page = workspace(<MemoirBookWorkspace project={project} onOpenChapters={vi.fn()} onOpenDrafts={vi.fn()} />);
  expect(await page.findByText('这是小程序已经组装的全书正文。')).toBeTruthy();
  const bookInfo = page.getByRole('complementary');
  expect((within(bookInfo).getByRole('textbox', { name: '副标题' }) as HTMLInputElement).value).toBe('家人的故事');
  expect(transport.mock.calls.map(([config]) => config.url)).toEqual([`/mobile/memoir/projects/${project.id}/book-output`]);
  expect(page.getByRole('button', { name: 'PDF' })).toBeTruthy();
});

test('a full-book version without manuscript still displays its stored prose', async () => {
  currentOutput!.latest_version!.manuscript = null;
  const page = workspace(<MemoirBookWorkspace project={project} onOpenChapters={vi.fn()} onOpenDrafts={vi.fn()} />);
  expect(await page.findByText('全书原始正文')).toBeTruthy();
});

test('a project without book output shows a normal empty book', async () => {
  currentOutput = null;
  const page = workspace(<MemoirBookWorkspace project={project} onOpenChapters={vi.fn()} onOpenDrafts={vi.fn()} />);
  expect(await page.findByText('还没有全书内容')).toBeTruthy();
});

test('correcting material refreshes the chapter sources and derived prose before reopening tabs', async () => {
  window.history.replaceState({}, '', '/?view=materials');
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 60_000 } } });
  client.setQueryData(['memoirMaterials', project.id, 'chapterSources'], materials);
  client.setQueryData(['memoirBookPlan', project.id], currentPlan);
  client.setQueryData(['memoirChapterDraft', project.id, 'chapter-1'], structuredClone(currentDraft));
  client.setQueryData(['memoirBookOutput', project.id], structuredClone(currentOutput));
  const page = workspace(<MemoirWorkspace project={project} />, client);
  const editor = await page.findByDisplayValue('素材 200');
  fireEvent.change(editor, { target: { value: '校对后的素材' } });
  fireEvent.click(page.getByRole('button', { name: '保存' }));
  await page.findByText('已人工校正');
  fireEvent.click(page.getByRole('button', { name: '章节目录' }));
  expect(await page.findByText('校对后的素材')).toBeTruthy();
  fireEvent.click(page.getByRole('button', { name: '章节稿' }));
  expect(await page.findByDisplayValue('校对后的章节正文')).toBeTruthy();
  fireEvent.click(page.getByRole('button', { name: '全书' }));
  expect(await page.findByText('校对后的全书正文')).toBeTruthy();
});

function enableDraftEditing() {
  currentPlan!.status = 'confirmed';
  const latest = currentDraft!.latest_version!;
  latest.sections = [
    { section_id: 'section-1', title: '院子', content: '小时候住的院子。', section_type: 'event', section_version: 1, sort_order: 0, used_material_ids: ['material-200'] },
    { section_id: 'section-2', title: '上学', content: '后来去上学。', section_type: 'event', section_version: 1, sort_order: 1, used_material_ids: [] },
  ];
  latest.content = latest.sections.map(section => section.content).join('\n\n');
}

test('saves chapter sections as a new version, retains history, and refreshes the full book', async () => {
  enableDraftEditing();
  const page = workspace(<MemoirWorkspace project={project} />);
  const prose = await page.findByRole('textbox', { name: '小节正文：院子' });
  fireEvent.change(prose, { target: { value: '人工校订后的院子。' } });
  fireEvent.click(page.getByRole('button', { name: '保存正文' }));
  await page.findByText('已保存，正在更新全书');
  const write = transport.mock.calls.map(([config]) => config).find(config => config.method === 'post');
  expect(JSON.parse(write.data)).toEqual({ expected_version_id: 'draft-version-2', sections: [
    { section_id: 'section-1', content: '人工校订后的院子。' }, { section_id: 'section-2', content: '后来去上学。' },
  ] });
  fireEvent.click(page.getByRole('button', { name: '版本 3' }));
  fireEvent.click(page.getAllByRole('button', { name: '查看此版本' })[0]);
  expect((page.getByRole('textbox', { name: '小节正文：院子' }) as HTMLTextAreaElement).value).toBe('小时候住的院子。');
  expect((page.getByRole('textbox', { name: '小节正文：院子' }) as HTMLTextAreaElement).readOnly).toBe(true);
  fireEvent.click(page.getByRole('button', { name: '返回最新版' }));
  expect((page.getByRole('textbox', { name: '小节正文：院子' }) as HTMLTextAreaElement).value).toBe('人工校订后的院子。');
  fireEvent.click(page.getByRole('button', { name: '全书' }));
  expect(await page.findByText(/人工校订后的院子。/)).toBeTruthy();
});

test('keeps unsaved prose and its original version after a background refresh and a conflict', async () => {
  enableDraftEditing();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const page = workspace(<MemoirWorkspace project={project} />, client);
  const prose = await page.findByRole('textbox', { name: '小节正文：院子' });
  fireEvent.change(prose, { target: { value: '尚未保存的人工稿。' } });
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
  fireEvent.click(page.getByRole('button', { name: '全书' }));
  expect(page.getByRole('textbox', { name: '小节正文：院子' })).toBeTruthy();
  const unloading = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(unloading);
  expect(unloading.defaultPrevented).toBe(true);
  currentDraft!.latest_version = { ...currentDraft!.latest_version!, id: 'remote-version', version: 3 };
  await act(async () => { await client.invalidateQueries({ queryKey: ['memoirChapterDraft', project.id] }); });
  fireEvent.click(page.getByRole('button', { name: '保存正文' }));
  await page.findByRole('alert');
  expect((page.getByRole('textbox', { name: '小节正文：院子' }) as HTMLTextAreaElement).value).toBe('尚未保存的人工稿。');
  const write = transport.mock.calls.map(([config]) => config).find(config => config.method === 'post');
  expect(JSON.parse(write.data).expected_version_id).toBe('draft-version-2');
  confirm.mockReturnValue(true);
  fireEvent.click(page.getByRole('button', { name: '放弃修改并读取最新版' }));
  await waitFor(() => expect((page.getByRole('textbox', { name: '小节正文：院子' }) as HTMLTextAreaElement).value).toBe('小时候住的院子。'));
});

test('saves only changed book fields, including deliberately cleared text', async () => {
  currentOutput!.is_stale = true;
  currentOutput!.stale_chapter_titles = ['童年'];
  const page = workspace(<MemoirBookWorkspace project={project} onOpenChapters={vi.fn()} onOpenDrafts={vi.fn()} />);
  fireEvent.change(await page.findByRole('textbox', { name: /^书名$/ }), { target: { value: '校订后的书名' } });
  fireEvent.change(page.getByRole('textbox', { name: '副标题' }), { target: { value: '' } });
  fireEvent.change(page.getByRole('textbox', { name: '整理者' }), { target: { value: '' } });
  fireEvent.change(page.getByRole('textbox', { name: '前言' }), { target: { value: '写给家人的前言。' } });
  fireEvent.click(page.getByRole('button', { name: '保存信息' }));
  await page.findByText('已保存');
  expect(page.getByText('章节稿已更新：童年')).toBeTruthy();
  const write = transport.mock.calls.map(([config]) => config).find(config => config.method === 'patch');
  expect(JSON.parse(write.data)).toEqual({ expected_version_id: 'output-version', title: '校订后的书名', subtitle: '', prepared_by: '', preface: '写给家人的前言。' });
  expect((page.getByRole('textbox', { name: '整理者' }) as HTMLInputElement).value).toBe('');
  expect(page.getByText('这是小程序已经组装的全书正文。')).toBeTruthy();
  expect(within(page.getByRole('region', { name: '全书预览' })).getByText('写给家人的前言。')).toBeTruthy();
});

test('keeps book edits after conflicts and blocks shared navigation until saved', async () => {
  const drafts = createDraftSaves();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const page = render(<QueryClientProvider client={client}><DraftSavesContext.Provider value={drafts}><MemoirBookWorkspace project={project} onOpenChapters={vi.fn()} onOpenDrafts={vi.fn()} /></DraftSavesContext.Provider></QueryClientProvider>);
  fireEvent.change(await page.findByRole('textbox', { name: '前言' }), { target: { value: '尚未保存的前言。' } });
  writeError = '全书已更新，请刷新核对后再保存';
  await act(async () => { await expect(drafts.flush()).rejects.toThrow(); });
  expect((page.getByRole('textbox', { name: '前言' }) as HTMLTextAreaElement).value).toBe('尚未保存的前言。');
  expect((await page.findByRole('alert')).textContent).toContain('全书已更新');
  expect(drafts.isDirty()).toBe(true);
  writeError = null;
  await act(async () => { await drafts.flush(); });
  await waitFor(() => expect(drafts.isDirty()).toBe(false));
  expect(within(page.getByRole('region', { name: '全书预览' })).getByText('尚未保存的前言。')).toBeTruthy();
});

test('reverting book edits releases the old version and follows subsequent server updates', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const page = workspace(<MemoirBookWorkspace project={project} onOpenChapters={vi.fn()} onOpenDrafts={vi.fn()} />, client);
  const subtitle = await page.findByRole('textbox', { name: '副标题' });
  fireEvent.change(subtitle, { target: { value: '临时修改' } });
  fireEvent.change(subtitle, { target: { value: '家人的故事' } });
  currentOutput!.latest_version!.id = 'remote-version';
  currentOutput!.latest_version!.manuscript!.profile.subtitle = '服务端更新的副标题';
  await act(async () => { await client.invalidateQueries({ queryKey: ['memoirBookOutput', project.id] }); });
  await waitFor(() => expect((page.getByRole('textbox', { name: '副标题' }) as HTMLInputElement).value).toBe('服务端更新的副标题'));
  expect((page.getByRole('button', { name: '保存信息' }) as HTMLButtonElement).disabled).toBe(true);
});

test('a refreshed chapter plan cannot remove an unsaved chapter from the editor', async () => {
  enableDraftEditing();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const page = workspace(<MemoirChapterDraftWorkspace project={project} onOpenChapters={vi.fn()} />, client);
  fireEvent.change(await page.findByRole('textbox', { name: '小节正文：院子' }), { target: { value: '尚未保存的章节。' } });
  currentPlan!.chapters = [];
  await act(async () => { await client.invalidateQueries({ queryKey: ['memoirBookPlan', project.id] }); });
  await waitFor(() => expect(page.getByText('共 0 章')).toBeTruthy());
  expect((page.getByRole('textbox', { name: '小节正文：院子' }) as HTMLTextAreaElement).value).toBe('尚未保存的章节。');
});
