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

test('the header home button saves textarea input before leaving the workspace', async () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const page = render(
    <QueryClientProvider client={queryClient}>
      <DraftSaveProvider>
        <Layout user={{ id: 'user', email: 'user@example.test', display_name: 'User', role: 'user', created_at: '' }} onLogout={vi.fn()} />
      </DraftSaveProvider>
    </QueryClientProvider>,
  );
  const textarea = await page.findByDisplayValue('original-a');
  fireEvent.change(textarea, { target: { value: 'input before clicking home' } });
  fireEvent.click(page.getByRole('button', { name: '返回我的项目' }));
  await waitFor(() => expect(useAppStore.getState().currentProject).toBeNull());
  expect(documents.a.content).toBe('input before clicking home');
  expect(await page.findByRole('heading', { name: '我的项目' })).toBeTruthy();
});
