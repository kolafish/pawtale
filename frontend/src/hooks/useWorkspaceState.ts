import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { charactersApi, outlinesApi, scenesApi, storiesApi } from '../api';
import { useAppStore } from '../stores/appStore';
import type {
  AIDebugInfo,
  Character,
  ExtractedCharacter,
  ExtractedOutlinePoint,
  ExtractedScene,
  Scene,
  Story,
  StoryOutline,
  WorkspaceChapter,
} from '../types';

function emptyCharacter(): ExtractedCharacter {
  return { name: '', role_hint: '', traits: [], summary: '' };
}

function emptyScene(): ExtractedScene {
  return { name: '', description: '', keywords: [] };
}

function emptyOutlinePoint(): ExtractedOutlinePoint {
  return { stage: '', summary: '' };
}

export function splitTokens(value: string): string[] {
  return value
    .split(/[，,、\n]/)
    .map((x) => x.trim())
    .filter(Boolean);
}

function countWords(text: string): number {
  if (!text) return 0;
  const chineseChars = (text.match(/[\u4e00-\u9fff\u3400-\u4dbf]/g) || []).length;
  const stripped = text.replace(/[\u4e00-\u9fff\u3400-\u4dbf]/g, ' ');
  const englishWords = stripped.split(/\s+/).filter((w) => w.length > 0).length;
  return chineseChars + englishWords;
}

function generateChapterId(): string {
  return `ch_${Math.random().toString(36).slice(2, 8)}`;
}

function chineseNumber(n: number): string {
  const nums = ['零', '一', '二', '三', '四', '五', '六', '七', '八', '九', '十'];
  if (n <= 10) return nums[n];
  if (n < 20) return `十${nums[n - 10]}`;
  const tens = Math.floor(n / 10);
  const ones = n % 10;
  return `${nums[tens]}十${ones > 0 ? nums[ones] : ''}`;
}

export type SaveStatus = 'saved' | 'saving' | 'unsaved';

export function useWorkspaceState() {
  const queryClient = useQueryClient();
  const { currentWorkspace, setCurrentWorkspace, defaultAiModel } = useAppStore();

  // Home screen state
  const [newTitle, setNewTitle] = useState('');
  const [newContent, setNewContent] = useState('');
  const [selectedExistingStoryId, setSelectedExistingStoryId] = useState('');

  // Editor state
  const [title, setTitle] = useState('');
  const [editorContent, setEditorContent] = useState('');
  const [aiModel, setAiModel] = useState(defaultAiModel);
  const [continueInstruction, setContinueInstruction] = useState('');
  const [debugMode, setDebugMode] = useState(false);
  const [debugInfo, setDebugInfo] = useState<AIDebugInfo | null>(null);
  const [extractDebugInfo, setExtractDebugInfo] = useState<AIDebugInfo | null>(null);

  // Chapter state
  const [chapters, setChapters] = useState<WorkspaceChapter[]>([]);
  const [currentChapterId, setCurrentChapterId] = useState('');

  // Extraction state
  const [extractedCharacters, setExtractedCharacters] = useState<ExtractedCharacter[]>([]);
  const [extractedScenes, setExtractedScenes] = useState<ExtractedScene[]>([]);
  const [extractedOutlinePoints, setExtractedOutlinePoints] = useState<ExtractedOutlinePoint[]>([]);
  const [extractionNotesText, setExtractionNotesText] = useState('');

  // Resource linking state
  const [selectedCharacterToLink, setSelectedCharacterToLink] = useState('');
  const [linkedSceneId, setLinkedSceneId] = useState('');
  const [linkedOutlineId, setLinkedOutlineId] = useState('');

  // Save status
  const [saveStatus, setSaveStatus] = useState<SaveStatus>('saved');
  const autoSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastSavedChaptersRef = useRef<string>('');
  const lastSavedTitleRef = useRef<string>('');

  // ─── Queries ───
  const { data: stories = [] } = useQuery({
    queryKey: ['stories'],
    queryFn: storiesApi.getAll,
  });
  const { data: allCharacters = [] } = useQuery({
    queryKey: ['characters'],
    queryFn: charactersApi.getAll,
  });
  const { data: allScenes = [] } = useQuery({
    queryKey: ['scenes'],
    queryFn: scenesApi.getAll,
  });
  const { data: allOutlines = [] } = useQuery({
    queryKey: ['outlines'],
    queryFn: outlinesApi.getAll,
  });
  const { data: currentStory } = useQuery({
    queryKey: ['story', currentWorkspace?.story_id],
    queryFn: () => storiesApi.getById(currentWorkspace!.story_id),
    enabled: Boolean(currentWorkspace?.story_id),
  });

  // ─── Helper: serialize chapters for dirty check ───
  const serializeChapters = useCallback((chs: WorkspaceChapter[]) => {
    return JSON.stringify(chs.map((ch) => ({ id: ch.id, title: ch.title, content: ch.content })));
  }, []);

  // ─── editorContent ↔ chapter sync ───
  // When editorContent changes (user typing), sync to current chapter
  const handleSetEditorContent = useCallback(
    (value: string) => {
      setEditorContent(value);
      if (!currentChapterId) return;
      setChapters((prev) =>
        prev.map((ch) => (ch.id === currentChapterId ? { ...ch, content: value } : ch)),
      );
    },
    [currentChapterId],
  );

  // ─── Mutations ───
  const createWorkspaceMutation = useMutation({
    mutationFn: storiesApi.createWorkspace,
    onSuccess: (workspace) => {
      setCurrentWorkspace(workspace);
      setDebugInfo(null);
      setExtractDebugInfo(null);
      setSaveStatus('saved');
      queryClient.invalidateQueries({ queryKey: ['stories'] });
      queryClient.invalidateQueries({ queryKey: ['story', workspace.story_id] });
    },
  });

  const loadWorkspaceMutation = useMutation({
    mutationFn: (storyId: string) => storiesApi.getWorkspace(storyId),
    onSuccess: (workspace) => {
      setCurrentWorkspace(workspace);
      setDebugInfo(null);
      setExtractDebugInfo(null);
      setSaveStatus('saved');
      queryClient.invalidateQueries({ queryKey: ['story', workspace.story_id] });
    },
  });

  const saveWorkspaceMutation = useMutation({
    mutationFn: ({ storyId, payload }: { storyId: string; payload: { title?: string; content?: string; chapters?: WorkspaceChapter[]; ai_model?: string } }) =>
      storiesApi.updateWorkspace(storyId, payload),
    onSuccess: (workspace) => {
      setCurrentWorkspace(workspace);
      setSaveStatus('saved');
      lastSavedChaptersRef.current = serializeChapters(chapters);
      lastSavedTitleRef.current = title;
      queryClient.invalidateQueries({ queryKey: ['stories'] });
    },
  });

  const continueMutation = useMutation({
    mutationFn: ({ storyId, payload }: { storyId: string; payload: { instruction?: string; ai_model?: string; append?: boolean; debug?: boolean; chapter_id?: string } }) =>
      storiesApi.continueWorkspace(storyId, payload),
    onSuccess: async (result) => {
      const separator = '\n\n--- AI 续写 ---\n\n';
      const generatedChunk = result.generated_chunk;
      // Update current chapter content
      setChapters((prev) => {
        const updated = prev.map((ch) => {
          if (ch.id === currentChapterId) {
            const newContent = ch.content.trimEnd() + separator + generatedChunk;
            setEditorContent(newContent);
            return { ...ch, content: newContent };
          }
          return ch;
        });
        // Save with updated chapters
        if (currentWorkspace) {
          saveWorkspaceMutation.mutate({
            storyId: currentWorkspace.story_id,
            payload: { chapters: updated },
          });
        }
        return updated;
      });
      setDebugInfo(result.debug_info || null);
      queryClient.invalidateQueries({ queryKey: ['stories'] });
    },
  });

  const extractMutation = useMutation({
    mutationFn: ({ storyId, payload }: { storyId: string; payload: { content?: string; ai_model?: string; debug?: boolean } }) =>
      storiesApi.extractWorkspace(storyId, payload),
    onSuccess: (result) => {
      setExtractedCharacters(result.extracted_characters || []);
      setExtractedScenes(result.extracted_scenes || []);
      setExtractedOutlinePoints(result.extracted_outline_points || []);
      setExtractionNotesText((result.extraction_notes || []).join('\n'));
      setDebugInfo(result.debug_info || null);
      setExtractDebugInfo(result.debug_info || null);
    },
  });

  const saveExtractionMutation = useMutation({
    mutationFn: ({
      storyId,
      payload,
    }: {
      storyId: string;
      payload: {
        extracted_characters?: ExtractedCharacter[];
        extracted_scenes?: ExtractedScene[];
        extracted_outline_points?: ExtractedOutlinePoint[];
        extraction_notes?: string[];
      };
    }) => storiesApi.updateWorkspaceExtraction(storyId, payload),
    onSuccess: (workspace) => {
      setCurrentWorkspace(workspace);
    },
  });

  const updateStoryRelationMutation = useMutation({
    mutationFn: ({
      storyId,
      payload,
    }: {
      storyId: string;
      payload: { scene_id?: string | null; outline_id?: string | null };
    }) => storiesApi.update(storyId, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['story', currentWorkspace?.story_id] });
      queryClient.invalidateQueries({ queryKey: ['stories'] });
    },
  });

  const updateStoryCharactersMutation = useMutation({
    mutationFn: ({
      storyId,
      add_character_ids,
      remove_character_ids,
    }: {
      storyId: string;
      add_character_ids: string[];
      remove_character_ids: string[];
    }) => storiesApi.updateCharacters(storyId, { add_character_ids, remove_character_ids }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['story', currentWorkspace?.story_id] });
      queryClient.invalidateQueries({ queryKey: ['stories'] });
    },
  });

  // ─── Effects ───
  useEffect(() => {
    if (!currentWorkspace) return;
    setTitle(currentWorkspace.title || '未命名作品');
    setAiModel((currentWorkspace.ai_model as typeof defaultAiModel) || defaultAiModel);
    setExtractedCharacters(currentWorkspace.extracted_characters || []);
    setExtractedScenes(currentWorkspace.extracted_scenes || []);
    setExtractedOutlinePoints(currentWorkspace.extracted_outline_points || []);
    setExtractionNotesText((currentWorkspace.extraction_notes || []).join('\n'));
    lastSavedTitleRef.current = currentWorkspace.title || '未命名作品';
    setSaveStatus('saved');

    // Initialize chapters
    const wsChapters = currentWorkspace.chapters;
    if (wsChapters && wsChapters.length > 0) {
      setChapters(wsChapters);
      setCurrentChapterId(wsChapters[0].id);
      setEditorContent(wsChapters[0].content || '');
    } else {
      // Backfill from editor_content
      const fallback: WorkspaceChapter[] = [{
        id: 'ch_1',
        title: '第一章',
        content: currentWorkspace.editor_content || '',
      }];
      setChapters(fallback);
      setCurrentChapterId('ch_1');
      setEditorContent(currentWorkspace.editor_content || '');
    }
    lastSavedChaptersRef.current = serializeChapters(
      currentWorkspace.chapters?.length ? currentWorkspace.chapters : [{
        id: 'ch_1',
        title: '第一章',
        content: currentWorkspace.editor_content || '',
      }],
    );
  }, [currentWorkspace, defaultAiModel, serializeChapters]);

  useEffect(() => {
    setLinkedSceneId(currentStory?.scene_id || '');
    setLinkedOutlineId(currentStory?.outline_id || '');
  }, [currentStory]);

  // Auto-save (3 second debounce)
  const doSave = useCallback(async () => {
    if (!currentWorkspace) return;
    const trimmedTitle = title.trim() || '未命名作品';
    const currentSerialized = serializeChapters(chapters);
    if (currentSerialized === lastSavedChaptersRef.current && trimmedTitle === lastSavedTitleRef.current) return;
    setSaveStatus('saving');
    await saveWorkspaceMutation.mutateAsync({
      storyId: currentWorkspace.story_id,
      payload: { title: trimmedTitle, chapters, ai_model: aiModel },
    });
  }, [currentWorkspace, title, chapters, aiModel, saveWorkspaceMutation, serializeChapters]);

  useEffect(() => {
    if (!currentWorkspace) return;
    const trimmedTitle = title.trim() || '未命名作品';
    const currentSerialized = serializeChapters(chapters);
    if (currentSerialized === lastSavedChaptersRef.current && trimmedTitle === lastSavedTitleRef.current) return;
    setSaveStatus('unsaved');
    if (autoSaveTimerRef.current) clearTimeout(autoSaveTimerRef.current);
    autoSaveTimerRef.current = setTimeout(() => {
      doSave();
    }, 3000);
    return () => {
      if (autoSaveTimerRef.current) clearTimeout(autoSaveTimerRef.current);
    };
  }, [chapters, title, currentWorkspace, doSave, serializeChapters]);

  // Cmd+S keyboard shortcut
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 's') {
        e.preventDefault();
        doSave();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [doSave]);

  // ─── Computed ───
  const storyOptions = useMemo(() => stories as Story[], [stories]);
  const linkedCharacters = useMemo(() => currentStory?.characters || [], [currentStory]);
  const globalCharacterCandidates = useMemo(() => {
    const linkedSet = new Set(linkedCharacters.map((c) => c.id));
    return (allCharacters as Character[]).filter((c) => !linkedSet.has(c.id));
  }, [allCharacters, linkedCharacters]);
  const linkedScene = useMemo(
    () => (allScenes as Scene[]).find((scene) => scene.id === linkedSceneId) || null,
    [allScenes, linkedSceneId],
  );
  const linkedOutline = useMemo(
    () => (allOutlines as StoryOutline[]).find((outline) => outline.id === linkedOutlineId) || null,
    [allOutlines, linkedOutlineId],
  );
  const wordCount = useMemo(() => countWords(editorContent), [editorContent]);
  const totalWordCount = useMemo(
    () => chapters.reduce((sum, ch) => sum + countWords(ch.content), 0),
    [chapters],
  );

  // ─── Chapter handlers ───
  const addChapter = useCallback(() => {
    const newId = generateChapterId();
    const num = chapters.length + 1;
    const newTitle = `第${chineseNumber(num)}章`;
    const newChapter: WorkspaceChapter = { id: newId, title: newTitle, content: '' };
    setChapters((prev) => [...prev, newChapter]);
    setCurrentChapterId(newId);
    setEditorContent('');
  }, [chapters.length]);

  const deleteChapter = useCallback(
    (chapterId: string) => {
      if (chapters.length <= 1) return; // keep at least one
      const idx = chapters.findIndex((ch) => ch.id === chapterId);
      const next = chapters.filter((ch) => ch.id !== chapterId);
      setChapters(next);
      if (currentChapterId === chapterId) {
        const newIdx = Math.min(idx, next.length - 1);
        setCurrentChapterId(next[newIdx].id);
        setEditorContent(next[newIdx].content);
      }
    },
    [chapters, currentChapterId],
  );

  const renameChapter = useCallback(
    (chapterId: string, newTitle: string) => {
      setChapters((prev) =>
        prev.map((ch) => (ch.id === chapterId ? { ...ch, title: newTitle } : ch)),
      );
    },
    [],
  );

  const switchChapter = useCallback(
    (chapterId: string) => {
      if (chapterId === currentChapterId) return;
      const target = chapters.find((ch) => ch.id === chapterId);
      if (!target) return;
      setCurrentChapterId(chapterId);
      setEditorContent(target.content);
    },
    [chapters, currentChapterId],
  );

  // ─── Handlers ───
  const handleCreateWorkspace = async () => {
    await createWorkspaceMutation.mutateAsync({
      title: newTitle.trim() || '未命名作品',
      content: newContent,
      ai_model: defaultAiModel,
    });
  };

  const handleOpenExistingStory = async () => {
    if (!selectedExistingStoryId) return;
    await loadWorkspaceMutation.mutateAsync(selectedExistingStoryId);
  };

  const handleAddStoryCharacter = async () => {
    if (!currentWorkspace || !selectedCharacterToLink) return;
    await updateStoryCharactersMutation.mutateAsync({
      storyId: currentWorkspace.story_id,
      add_character_ids: [selectedCharacterToLink],
      remove_character_ids: [],
    });
    setSelectedCharacterToLink('');
  };

  const handleRemoveStoryCharacter = async (characterId: string) => {
    if (!currentWorkspace) return;
    await updateStoryCharactersMutation.mutateAsync({
      storyId: currentWorkspace.story_id,
      add_character_ids: [],
      remove_character_ids: [characterId],
    });
  };

  const handleLinkScene = async (sceneId: string) => {
    if (!currentWorkspace) return;
    setLinkedSceneId(sceneId);
    await updateStoryRelationMutation.mutateAsync({
      storyId: currentWorkspace.story_id,
      payload: { scene_id: sceneId || null },
    });
  };

  const handleLinkOutline = async (outlineId: string) => {
    if (!currentWorkspace) return;
    setLinkedOutlineId(outlineId);
    await updateStoryRelationMutation.mutateAsync({
      storyId: currentWorkspace.story_id,
      payload: { outline_id: outlineId || null },
    });
  };

  const handleSaveContent = async () => {
    if (!currentWorkspace) return;
    setSaveStatus('saving');
    await saveWorkspaceMutation.mutateAsync({
      storyId: currentWorkspace.story_id,
      payload: {
        title: title.trim() || '未命名作品',
        chapters,
        ai_model: aiModel,
      },
    });
  };

  const handleContinue = async () => {
    if (!currentWorkspace) return;
    await continueMutation.mutateAsync({
      storyId: currentWorkspace.story_id,
      payload: {
        instruction: continueInstruction.trim() || undefined,
        ai_model: aiModel,
        append: true,
        debug: debugMode,
        chapter_id: currentChapterId || undefined,
      },
    });
    setContinueInstruction('');
  };

  const handleExtract = async () => {
    if (!currentWorkspace) return;
    // Extract from all chapters combined
    const allContent = chapters.map((ch) => ch.content).join('\n\n');
    await extractMutation.mutateAsync({
      storyId: currentWorkspace.story_id,
      payload: { content: allContent, ai_model: aiModel, debug: debugMode },
    });
  };

  const handleSaveExtraction = async () => {
    if (!currentWorkspace) return;

    const cleanCharacters = extractedCharacters
      .map((item) => ({
        name: item.name.trim(),
        role_hint: item.role_hint?.trim() || undefined,
        traits: (item.traits || []).map((t) => t.trim()).filter(Boolean),
        summary: item.summary?.trim() || undefined,
      }))
      .filter((item) => item.name);

    const cleanScenes = extractedScenes
      .map((item) => ({
        name: item.name.trim(),
        description: item.description?.trim() || undefined,
        keywords: (item.keywords || []).map((k) => k.trim()).filter(Boolean),
      }))
      .filter((item) => item.name);

    const cleanOutline = extractedOutlinePoints
      .map((item) => ({
        stage: item.stage.trim(),
        summary: item.summary.trim(),
      }))
      .filter((item) => item.stage && item.summary);

    const cleanNotes = extractionNotesText
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean);

    await saveExtractionMutation.mutateAsync({
      storyId: currentWorkspace.story_id,
      payload: {
        extracted_characters: cleanCharacters,
        extracted_scenes: cleanScenes,
        extracted_outline_points: cleanOutline,
        extraction_notes: cleanNotes,
      },
    });
  };

  const handleExportTxt = () => {
    if (!currentWorkspace) return;
    const fullText = chapters
      .map((ch) => `${ch.title}\n\n${ch.content}`)
      .join('\n\n---\n\n');
    const blob = new Blob([fullText], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${title || '未命名作品'}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleSaveCharacterToLibrary = async (char: ExtractedCharacter) => {
    await charactersApi.create({
      name: char.name,
      personality: char.traits || [],
      identity: char.role_hint || undefined,
      background: char.summary || undefined,
      habits: [],
    });
    queryClient.invalidateQueries({ queryKey: ['characters'] });
  };

  const handleSaveSceneToLibrary = async (scene: ExtractedScene) => {
    await scenesApi.create({
      name: scene.name,
      description: scene.description || '',
      category: '自定义',
      mood_keywords: scene.keywords || [],
      sensory_details: {},
      props: [],
    });
    queryClient.invalidateQueries({ queryKey: ['scenes'] });
  };

  return {
    // State
    currentWorkspace,
    setCurrentWorkspace,
    newTitle,
    setNewTitle,
    newContent,
    setNewContent,
    selectedExistingStoryId,
    setSelectedExistingStoryId,
    title,
    setTitle,
    editorContent,
    setEditorContent: handleSetEditorContent,
    aiModel,
    setAiModel,
    continueInstruction,
    setContinueInstruction,
    debugMode,
    setDebugMode,
    debugInfo,
    extractDebugInfo,
    extractedCharacters,
    setExtractedCharacters,
    extractedScenes,
    setExtractedScenes,
    extractedOutlinePoints,
    setExtractedOutlinePoints,
    extractionNotesText,
    setExtractionNotesText,
    selectedCharacterToLink,
    setSelectedCharacterToLink,
    linkedSceneId,
    linkedOutlineId,
    saveStatus,
    wordCount,
    totalWordCount,

    // Chapter state
    chapters,
    currentChapterId,
    addChapter,
    deleteChapter,
    renameChapter,
    switchChapter,

    // Queries
    storyOptions,
    allCharacters: allCharacters as Character[],
    allScenes: allScenes as Scene[],
    allOutlines: allOutlines as StoryOutline[],
    linkedCharacters,
    globalCharacterCandidates,
    linkedScene,
    linkedOutline,

    // Mutations loading states
    isCreating: createWorkspaceMutation.isPending,
    isLoading: loadWorkspaceMutation.isPending,
    isSaving: saveWorkspaceMutation.isPending,
    isContinuing: continueMutation.isPending,
    isExtracting: extractMutation.isPending,
    isSavingExtraction: saveExtractionMutation.isPending,
    isUpdatingCharacters: updateStoryCharactersMutation.isPending,

    // Handlers
    handleCreateWorkspace,
    handleOpenExistingStory,
    handleAddStoryCharacter,
    handleRemoveStoryCharacter,
    handleLinkScene,
    handleLinkOutline,
    handleSaveContent,
    handleContinue,
    handleExtract,
    handleSaveExtraction,
    handleExportTxt,
    handleSaveCharacterToLibrary,
    handleSaveSceneToLibrary,

    // Helpers
    emptyCharacter,
    emptyScene,
    emptyOutlinePoint,
  };
}
