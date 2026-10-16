// @vitest-environment happy-dom
import { act, cleanup, fireEvent, render, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import type { ReactNode } from 'react';
import { projectsApi } from '../../api';
import { DraftSaveProvider } from '../../components/DraftSaveProvider';
import { Layout } from '../../components/Layout';
import { useAppStore } from '../../stores/appStore';
import type { Project, ReferenceItem, WritingDocument } from '../../types';
import { useProjectState } from '../useProjectState';
import { useReferenceEditor } from '../useReferenceEditor';

let documents: Record<string, WritingDocument>;
let project: Project;
let references: ReferenceItem[];

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

beforeEach(() => {
  documents = Object.fromEntries(['a', 'b'].map((id) => [id, {
    id, group_id: 'group', name: id, content: `original-${id}`, status: 'draft',
    label: '', synopsis: '', word_count: 1, content_revision: 0, sort_order: 0,
    created_at: '', updated_at: '',
  }]));
  project = {
    id: 'project', title: 'Test', type: 'novel', can_delete: true, total_word_count: 2,
    groups: [{ id: 'group', name: 'Group', sort_order: 0, documents: Object.values(documents) }],
    references: [], created_at: '', updated_at: '',
  };
  references = [{
    id: 'ref', project_id: project.id, type: 'note', name: 'Note', content: 'original note',
    metadata_json: {}, sort_order: 0, created_at: '', updated_at: '',
  }];
  useAppStore.setState({ currentProject: project, currentDocumentId: 'a', currentReferenceId: null });
  vi.spyOn(projectsApi, 'get').mockImplementation(async () => ({ ...project }));
  vi.spyOn(projectsApi, 'list').mockResolvedValue([]);
  vi.spyOn(projectsApi, 'getDocument').mockImplementation(async (_project, id) => ({ ...documents[id] }));
  vi.spyOn(projectsApi, 'updateDocument').mockImplementation(async (_project, id, patch) => {
    const { expected_content_revision, ...values } = patch;
    if (expected_content_revision !== undefined && expected_content_revision !== documents[id].content_revision) {
      throw { response: { status: 409 } };
    }
    documents[id] = {
      ...documents[id], ...values,
      content_revision: documents[id].content_revision + (patch.content === undefined ? 0 : 1),
    };
    return { ...documents[id] };
  });
  vi.spyOn(projectsApi, 'createDocument').mockImplementation(async () => ({ ...documents.b }));
  vi.spyOn(projectsApi, 'listReferences').mockImplementation(async () => references.map((ref) => ({ ...ref })));
  vi.spyOn(projectsApi, 'updateReference').mockImplementation(async (_project, id, patch) => {
    references = references.map((ref) => ref.id === id ? { ...ref, ...patch } : ref);
    return references.find((ref) => ref.id === id)!;
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  useAppStore.getState().reset();
});

async function editor() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const hook = renderHook(() => ({ page: useProjectState(), reference: useReferenceEditor() }), {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}><DraftSaveProvider>{children}</DraftSaveProvider></QueryClientProvider>
    ),
  });
  await waitFor(() => expect(hook.result.current.page.documentContent).toBe('original-a'));
  return hook;
}

test('renaming preserves dirty body and manual save submits it', async () => {
  const { result } = await editor();
  act(() => result.current.page.setDocumentContent('unsaved new body'));
  await act(() => result.current.page.renameDocument('a', 'renamed'));
  expect(result.current.page.saveStatus).toBe('unsaved');
  expect(documents.a.content).toBe('original-a');
  await act(() => result.current.page.handleSave());
  expect(documents.a).toMatchObject({ name: 'renamed', content: 'unsaved new body' });
  expect(result.current.page.saveStatus).toBe('saved');
});

test('closing a project flushes pending input before clearing it', async () => {
  const { result } = await editor();
  act(() => result.current.page.setDocumentContent('save before leaving'));
  await act(() => result.current.page.closeProject());
  expect(documents.a.content).toBe('save before leaving');
  expect(useAppStore.getState().currentProject).toBeNull();
});

test('failed save prevents navigation and keeps the current draft', async () => {
  const { result } = await editor();
  act(() => result.current.page.setDocumentContent('keep this draft'));
  vi.mocked(projectsApi.updateDocument).mockRejectedValueOnce(new Error('offline'));
  await act(async () => { await expect(result.current.page.closeProject()).rejects.toThrow('offline'); });
  expect(useAppStore.getState().currentDocumentId).toBe('a');
  expect(result.current.page.documentContent).toBe('keep this draft');
});

test('creating a document flushes the previous draft', async () => {
  const { result } = await editor();
  act(() => result.current.page.setDocumentContent('save before creating'));
  await act(() => result.current.page.createDocument('group'));
  expect(documents.a.content).toBe('save before creating');
  expect(result.current.page.currentDocumentId).toBe('b');
});

test('overlapping saves serialize revisions and include input made during saving', async () => {
  const { result } = await editor();
  const gate = deferred<void>();
  const update = vi.mocked(projectsApi.updateDocument).getMockImplementation()!;
  vi.mocked(projectsApi.updateDocument).mockImplementationOnce(async (...args) => {
    await gate.promise;
    return update(...args);
  });
  act(() => result.current.page.setDocumentContent('first input'));
  let first!: Promise<void>;
  act(() => { first = result.current.page.handleSave(); });
  await waitFor(() => expect(projectsApi.updateDocument).toHaveBeenCalledTimes(1));
  act(() => result.current.page.setDocumentContent('input while saving'));
  await act(async () => {
    const second = result.current.page.handleSave();
    gate.resolve();
    await Promise.all([first, second]);
  });
  expect(documents.a.content).toBe('input while saving');
  expect(documents.a.content_revision).toBe(2);
  expect(result.current.page.saveStatus).toBe('saved');
});

test('AI continuation saves the visible draft before requesting generation', async () => {
  const { result } = await editor();
  const ai = vi.spyOn(projectsApi, 'aiContinue').mockImplementation(async (_project, id, request) => {
    expect(documents[id].content).toBe('visible draft');
    expect(request.expected_content_revision).toBe(1);
    documents[id].content = 'visible draft plus AI';
    documents[id].content_revision = 2;
    return { content: documents[id].content, document_id: 'a', generated_chunk: 'plus AI', ai_model: 'deepseek', content_revision: 2 };
  });
  act(() => result.current.page.setDocumentContent('visible draft'));
  await act(() => result.current.page.handleContinue());
  expect(ai).toHaveBeenCalledOnce();
  expect(result.current.page.documentContent).toBe('visible draft plus AI');
});

test('AI response preserves new local input and blocks conflicting autosave', async () => {
  const { result } = await editor();
  const response = deferred<Awaited<ReturnType<typeof projectsApi.aiContinue>>>();
  vi.spyOn(projectsApi, 'aiContinue').mockReturnValue(response.promise);
  let pending!: Promise<void>;
  act(() => { pending = result.current.page.handleContinue(); });
  await waitFor(() => expect(projectsApi.aiContinue).toHaveBeenCalledOnce());
  act(() => result.current.page.setDocumentContent('typed during AI'));
  await act(async () => {
    response.resolve({ content: 'original-a plus AI', document_id: 'a', generated_chunk: 'plus AI', ai_model: 'deepseek', content_revision: 1 });
    await pending;
  });
  expect(result.current.page.documentContent).toBe('typed during AI');
  expect(result.current.page.saveStatus).toBe('conflict');
  await act(async () => { await expect(result.current.page.handleSave()).rejects.toThrow('冲突'); });
  expect(projectsApi.updateDocument).not.toHaveBeenCalled();
  act(() => result.current.page.setDocumentContent('original-a'));
  await act(async () => { await expect(result.current.page.closeProject()).rejects.toThrow('冲突'); });
  expect(useAppStore.getState().currentDocumentId).toBe('a');
});

test('AI response for a previous document cannot replace the current document', async () => {
  const { result } = await editor();
  const response = deferred<Awaited<ReturnType<typeof projectsApi.aiContinue>>>();
  vi.spyOn(projectsApi, 'aiContinue').mockReturnValue(response.promise);
  let pending!: Promise<void>;
  act(() => { pending = result.current.page.handleContinue(); });
  await waitFor(() => expect(projectsApi.aiContinue).toHaveBeenCalledOnce());
  await act(() => result.current.page.switchDocument('b'));
  await act(async () => {
    response.resolve({ content: 'original-a plus AI', document_id: 'a', generated_chunk: 'plus AI', ai_model: 'deepseek', content_revision: 1 });
    await pending;
  });
  expect(result.current.page.currentDocumentId).toBe('b');
  expect(result.current.page.documentContent).toBe('original-b');
});

test('switching from a reference to a document flushes reference edits', async () => {
  const { result } = await editor();
  await act(() => result.current.page.selectReference('ref'));
  await waitFor(() => expect(result.current.reference.referenceData?.id).toBe('ref'));
  act(() => result.current.reference.updateField({ content: 'edited note' }));
  await act(() => result.current.page.switchDocument('b'));
  expect(references[0].content).toBe('edited note');
  expect(result.current.page.currentDocumentId).toBe('b');
});

function workspace() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <DraftSaveProvider>
        <Layout user={{ id: 'user', email: 'user@example.test', display_name: 'User', role: 'user', created_at: '' }} onLogout={vi.fn()} />
      </DraftSaveProvider>
    </QueryClientProvider>,
  );
}

test('the header home button saves textarea input before leaving the workspace', async () => {
  const page = workspace();
  const textarea = await page.findByDisplayValue('original-a');
  fireEvent.change(textarea, { target: { value: 'input before clicking home' } });
  fireEvent.click(page.getByRole('button', { name: '返回我的项目' }));
  await waitFor(() => expect(useAppStore.getState().currentProject).toBeNull());
  expect(documents.a.content).toBe('input before clicking home');
  expect(await page.findByRole('heading', { name: '我的项目' })).toBeTruthy();
});

test.each(['novel', 'article'] as const)('%s editor undoes formatting with selection and redoes via keyboard', async (type) => {
  project.type = type;
  const page = workspace();
  const textarea = await page.findByDisplayValue('original-a') as HTMLTextAreaElement;
  const undo = page.getByRole('button', { name: '撤销' }) as HTMLButtonElement;
  const redo = page.getByRole('button', { name: '重做' }) as HTMLButtonElement;
  expect(undo.disabled).toBe(true);
  expect(redo.disabled).toBe(true);
  textarea.setSelectionRange(0, 8);
  fireEvent.select(textarea);
  fireEvent.click(page.getByTitle('加粗 Cmd/Ctrl+B'));
  await waitFor(() => expect(textarea.value).toBe('**original**-a'));
  fireEvent.click(undo);
  await waitFor(() => {
    expect(textarea.value).toBe('original-a');
    expect(textarea.selectionStart).toBe(0);
    expect(textarea.selectionEnd).toBe(8);
  });
  expect(undo.disabled).toBe(true);
  expect(redo.disabled).toBe(false);
  expect(page.getByText('已保存')).toBeTruthy();
  fireEvent.keyDown(textarea, { key: 'Z', metaKey: true, shiftKey: true });
  await waitFor(() => expect(textarea.value).toBe('**original**-a'));
  fireEvent.keyDown(textarea, { key: 'z', ctrlKey: true });
  expect(textarea.value).toBe('original-a');
  fireEvent.keyDown(textarea, { key: 'y', ctrlKey: true });
  expect(textarea.value).toBe('**original**-a');
  fireEvent.click(page.getByTitle('分屏'));
  fireEvent.click(undo);
  expect((page.getByDisplayValue('original-a') as HTMLTextAreaElement).value).toBe('original-a');
  fireEvent.click(page.getByTitle('预览'));
  expect(redo.disabled).toBe(true);
});

test('typing is grouped, pasting is separate, and new input discards redo', async () => {
  const page = workspace();
  const textarea = await page.findByDisplayValue('original-a') as HTMLTextAreaElement;
  textarea.setSelectionRange(10, 10);
  fireEvent.select(textarea);
  fireEvent.input(textarea, { target: { value: 'original-ab' }, inputType: 'insertText' });
  fireEvent.input(textarea, { target: { value: 'original-abc' }, inputType: 'insertText' });
  fireEvent.input(textarea, { target: { value: 'original-abc pasted' }, inputType: 'insertFromPaste' });
  fireEvent.keyDown(textarea, { key: 'z', metaKey: true });
  expect(textarea.value).toBe('original-abc');
  fireEvent.keyDown(textarea, { key: 'z', metaKey: true });
  expect(textarea.value).toBe('original-a');
  fireEvent.input(textarea, { target: { value: '' }, inputType: 'deleteContentBackward' });
  expect((page.getByRole('button', { name: '重做' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(page.getByRole('button', { name: '撤销' }));
  expect(textarea.value).toBe('original-a');
});

test('IME composition remains one edit and leaves composing shortcuts to the IME', async () => {
  const page = workspace();
  const textarea = await page.findByDisplayValue('original-a') as HTMLTextAreaElement;
  fireEvent.compositionStart(textarea);
  fireEvent.input(textarea, { target: { value: 'original-a你' }, inputType: 'insertCompositionText' });
  textarea.setSelectionRange(10, 11);
  fireEvent.select(textarea);
  fireEvent.input(textarea, { target: { value: 'original-a你好' }, inputType: 'insertCompositionText' });
  expect(fireEvent.keyDown(textarea, { key: 'z', ctrlKey: true, isComposing: true })).toBe(true);
  expect(textarea.value).toBe('original-a你好');
  fireEvent.compositionEnd(textarea);
  fireEvent.keyDown(textarea, { key: 'z', ctrlKey: true });
  expect(textarea.value).toBe('original-a');
  fireEvent.keyDown(textarea, { key: 'y', ctrlKey: true });
  expect(textarea.value).toBe('original-a你好');
});

test('accepted AI revisions can be undone and redone without reopening review', async () => {
  vi.spyOn(projectsApi, 'aiRevise').mockResolvedValue({
    document_id: 'a', original_text: 'original-a', revised_text: 'revised by AI', scope: 'full', ai_model: 'deepseek',
  });
  const page = workspace();
  await page.findByDisplayValue('original-a');
  fireEvent.click(page.getByRole('button', { name: 'AI' }));
  fireEvent.change(page.getByPlaceholderText('输入修改意见，如：让对话更自然、增加环境描写、加强冲突感...'), {
    target: { value: '润色' },
  });
  fireEvent.click(page.getByRole('button', { name: '单模型修改' }));
  fireEvent.click(await page.findByRole('button', { name: '应用修改' }));
  const textarea = await page.findByDisplayValue('revised by AI') as HTMLTextAreaElement;
  fireEvent.click(page.getByRole('button', { name: '撤销' }));
  expect(textarea.value).toBe('original-a');
  fireEvent.click(page.getByRole('button', { name: '重做' }));
  expect(textarea.value).toBe('revised by AI');
  expect(page.queryByText('审阅模式')).toBeNull();
});

test('history survives saves and AI continuation, undo saves the restored body, and switching resets it', async () => {
  const { result } = await editor();
  expect(result.current.page.canUndoContent).toBe(false);
  act(() => result.current.page.setDocumentContent('manual edit'));
  await act(() => result.current.page.handleSave());
  expect(result.current.page.canUndoContent).toBe(true);
  vi.spyOn(projectsApi, 'aiContinue').mockImplementation(async () => {
    documents.a = { ...documents.a, content: 'manual edit plus AI', content_revision: 2 };
    return { content: documents.a.content, document_id: 'a', generated_chunk: 'plus AI', ai_model: 'deepseek', content_revision: 2 };
  });
  await act(() => result.current.page.handleContinue());
  expect(result.current.page.documentContent).toBe('manual edit plus AI');
  act(() => result.current.page.undoDocumentContent());
  expect(result.current.page.documentContent).toBe('manual edit');
  expect(result.current.page.saveStatus).toBe('unsaved');
  await act(() => result.current.page.handleSave());
  expect(documents.a.content).toBe('manual edit');
  act(() => result.current.page.undoDocumentContent());
  expect(result.current.page.documentContent).toBe('original-a');
  await act(() => result.current.page.switchDocument('b'));
  expect(documents.a.content).toBe('original-a');
  expect(result.current.page.documentContent).toBe('original-b');
  expect(result.current.page.canUndoContent).toBe(false);
  expect(result.current.page.canRedoContent).toBe(false);
});
