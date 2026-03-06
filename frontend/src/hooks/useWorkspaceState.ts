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
  // Count Chinese characters
  const chineseChars = (text.match(/[\u4e00-\u9fff\u3400-\u4dbf]/g) || []).length;
  // Count English words (sequences of non-CJK, non-whitespace)
  const stripped = text.replace(/[\u4e00-\u9fff\u3400-\u4dbf]/g, ' ');
  const englishWords = stripped.split(/\s+/).filter((w) => w.length > 0).length;
  return chineseChars + englishWords;
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
  const lastSavedContentRef = useRef<string>('');
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
    mutationFn: ({ storyId, payload }: { storyId: string; payload: { title?: string; content?: string; ai_model?: string } }) =>
      storiesApi.updateWorkspace(storyId, payload),
    onSuccess: (workspace) => {
      setCurrentWorkspace(workspace);
      setSaveStatus('saved');
      lastSavedContentRef.current = editorContent;
      lastSavedTitleRef.current = title;
      queryClient.invalidateQueries({ queryKey: ['stories'] });
    },
  });

  const continueMutation = useMutation({
    mutationFn: ({ storyId, payload }: { storyId: string; payload: { instruction?: string; ai_model?: string; append?: boolean; debug?: boolean } }) =>
      storiesApi.continueWorkspace(storyId, payload),
    onSuccess: async (result) => {
      // Insert separator before AI-generated content
      const separator = '\n\n--- AI 续写 ---\n\n';
      const newContent = editorContent.trimEnd() + separator + result.generated_chunk;
      setEditorContent(newContent);
      setDebugInfo(result.debug_info || null);
      // Save with separator included
      if (currentWorkspace) {
        await saveWorkspaceMutation.mutateAsync({
          storyId: currentWorkspace.story_id,
          payload: { content: newContent },
        });
      }
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
    setEditorContent(currentWorkspace.editor_content || '');
    setAiModel((currentWorkspace.ai_model as typeof defaultAiModel) || defaultAiModel);
    setExtractedCharacters(currentWorkspace.extracted_characters || []);
    setExtractedScenes(currentWorkspace.extracted_scenes || []);
    setExtractedOutlinePoints(currentWorkspace.extracted_outline_points || []);
    setExtractionNotesText((currentWorkspace.extraction_notes || []).join('\n'));
    lastSavedContentRef.current = currentWorkspace.editor_content || '';
    lastSavedTitleRef.current = currentWorkspace.title || '未命名作品';
    setSaveStatus('saved');
  }, [currentWorkspace, defaultAiModel]);

  useEffect(() => {
    setLinkedSceneId(currentStory?.scene_id || '');
    setLinkedOutlineId(currentStory?.outline_id || '');
  }, [currentStory]);

  // Auto-save (3 second debounce)
  const doSave = useCallback(async () => {
    if (!currentWorkspace) return;
    const trimmedTitle = title.trim() || '未命名作品';
    if (editorContent === lastSavedContentRef.current && trimmedTitle === lastSavedTitleRef.current) return;
    setSaveStatus('saving');
    await saveWorkspaceMutation.mutateAsync({
      storyId: currentWorkspace.story_id,
      payload: { title: trimmedTitle, content: editorContent, ai_model: aiModel },
    });
  }, [currentWorkspace, title, editorContent, aiModel, saveWorkspaceMutation]);

  useEffect(() => {
    if (!currentWorkspace) return;
    const trimmedTitle = title.trim() || '未命名作品';
    if (editorContent === lastSavedContentRef.current && trimmedTitle === lastSavedTitleRef.current) return;
    setSaveStatus('unsaved');
    if (autoSaveTimerRef.current) clearTimeout(autoSaveTimerRef.current);
    autoSaveTimerRef.current = setTimeout(() => {
      doSave();
    }, 3000);
    return () => {
      if (autoSaveTimerRef.current) clearTimeout(autoSaveTimerRef.current);
    };
  }, [editorContent, title, currentWorkspace, doSave]);

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
        content: editorContent,
        ai_model: aiModel,
      },
    });

    if (editorContent.trim().length >= 60) {
      await extractMutation.mutateAsync({
        storyId: currentWorkspace.story_id,
        payload: { content: editorContent, ai_model: aiModel, debug: debugMode },
      });
    }
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
      },
    });
    setContinueInstruction('');
  };

  const handleExtract = async () => {
    if (!currentWorkspace) return;
    await extractMutation.mutateAsync({
      storyId: currentWorkspace.story_id,
      payload: { content: editorContent, ai_model: aiModel, debug: debugMode },
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
    const blob = new Blob([editorContent], { type: 'text/plain;charset=utf-8' });
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
    setEditorContent,
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
