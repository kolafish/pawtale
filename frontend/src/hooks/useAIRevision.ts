import { useState, useCallback } from 'react';
import { projectsApi } from '../api';
import { computeMarkdownDiff } from '../utils/markdownDiff';
import type { DiffChunk, SelectionRange, AIDebugInfo, MultiReviseResultItem } from '../types';

export function useAIRevision() {
  const [isOpen, setIsOpen] = useState(false);
  const [revisionInstruction, setRevisionInstruction] = useState('');
  const [selection, setSelection] = useState<SelectionRange | null>(null);
  const [isRevising, setIsRevising] = useState(false);
  const [diffChunks, setDiffChunks] = useState<DiffChunk[] | null>(null);
  const [originalText, setOriginalText] = useState<string | null>(null);
  const [revisedText, setRevisedText] = useState<string | null>(null);
  const [revisionScope, setRevisionScope] = useState<'full' | 'selection'>('full');
  const [debugInfo, setDebugInfo] = useState<AIDebugInfo | null>(null);

  // Multi-model state
  const [multiResults, setMultiResults] = useState<MultiReviseResultItem[] | null>(null);
  const [activeTabIndex, setActiveTabIndex] = useState(0);
  const [isMultiRevising, setIsMultiRevising] = useState(false);

  const isReviewing = diffChunks !== null || multiResults !== null;

  const requestRevision = useCallback(async (
    projectId: string,
    docId: string,
    content: string,
    aiModel: string,
    debug: boolean,
  ) => {
    if (!revisionInstruction.trim()) return;
    setIsRevising(true);
    try {
      const result = await projectsApi.aiRevise(projectId, docId, {
        instruction: revisionInstruction.trim(),
        selected_text: revisionScope === 'selection' && selection ? selection.text : undefined,
        full_content: revisionScope === 'selection' ? content : undefined,
        ai_model: aiModel,
        debug,
      });

      const orig = result.original_text;
      const revised = result.revised_text;
      setOriginalText(orig);
      setRevisedText(revised);
      setDebugInfo(result.debug_info || null);
      setDiffChunks(computeMarkdownDiff(orig, revised));
    } catch (e) {
      console.error('AI revision failed:', e);
      throw e;
    } finally {
      setIsRevising(false);
    }
  }, [revisionInstruction, revisionScope, selection]);

  const requestMultiRevision = useCallback(async (
    projectId: string,
    docId: string,
    content: string,
    models: string[],
    debug: boolean,
  ) => {
    if (!revisionInstruction.trim()) return;
    setIsMultiRevising(true);
    try {
      const result = await projectsApi.aiReviseMulti(projectId, docId, {
        instruction: revisionInstruction.trim(),
        selected_text: revisionScope === 'selection' && selection ? selection.text : undefined,
        full_content: revisionScope === 'selection' ? content : undefined,
        models,
        debug,
      });

      setOriginalText(result.original_text);
      setMultiResults(result.results);

      // Auto-select first successful result
      const firstSuccess = result.results.findIndex(r => r.revised_text);
      const idx = firstSuccess >= 0 ? firstSuccess : 0;
      setActiveTabIndex(idx);

      const item = result.results[idx];
      if (item?.revised_text) {
        setRevisedText(item.revised_text);
        setDiffChunks(computeMarkdownDiff(result.original_text, item.revised_text));
        setDebugInfo(item.debug_info || null);
      } else {
        setRevisedText(null);
        setDiffChunks(null);
        setDebugInfo(null);
      }
    } catch (e) {
      console.error('AI multi-revision failed:', e);
      throw e;
    } finally {
      setIsMultiRevising(false);
    }
  }, [revisionInstruction, revisionScope, selection]);

  const switchTab = useCallback((index: number) => {
    if (!multiResults || !originalText) return;
    setActiveTabIndex(index);
    const item = multiResults[index];
    if (item?.revised_text) {
      setRevisedText(item.revised_text);
      setDiffChunks(computeMarkdownDiff(originalText, item.revised_text));
      setDebugInfo(item.debug_info || null);
    } else {
      setRevisedText(null);
      setDiffChunks(null);
      setDebugInfo(null);
    }
  }, [multiResults, originalText]);

  const acceptChunk = useCallback((chunkId: string) => {
    setDiffChunks(prev => prev?.map(c =>
      c.id === chunkId && c.type !== 'equal' ? { ...c, accepted: true } : c
    ) ?? null);
  }, []);

  const rejectChunk = useCallback((chunkId: string) => {
    setDiffChunks(prev => prev?.map(c =>
      c.id === chunkId && c.type !== 'equal' ? { ...c, accepted: false } : c
    ) ?? null);
  }, []);

  const toggleChunk = useCallback((chunkId: string) => {
    setDiffChunks(prev => prev?.map(c => {
      if (c.id !== chunkId || c.type === 'equal') return c;
      const next = c.accepted === null ? true : c.accepted === true ? false : null;
      return { ...c, accepted: next };
    }) ?? null);
  }, []);

  const acceptAll = useCallback(() => {
    setDiffChunks(prev => prev?.map(c =>
      c.type !== 'equal' ? { ...c, accepted: true } : c
    ) ?? null);
  }, []);

  const rejectAll = useCallback(() => {
    setDiffChunks(prev => prev?.map(c =>
      c.type !== 'equal' ? { ...c, accepted: false } : c
    ) ?? null);
  }, []);

  const applyRevision = useCallback((): string | null => {
    if (!diffChunks) return null;
    const parts: string[] = [];
    for (const chunk of diffChunks) {
      if (chunk.type === 'equal') {
        parts.push(chunk.text);
      } else if (chunk.type === 'insert') {
        if (chunk.accepted !== false) {
          parts.push(chunk.text);
        }
      } else if (chunk.type === 'delete') {
        if (chunk.accepted === false) {
          parts.push(chunk.text);
        }
      }
    }
    return parts.join('');
  }, [diffChunks]);

  const clearRevision = useCallback(() => {
    setDiffChunks(null);
    setOriginalText(null);
    setRevisedText(null);
    setDebugInfo(null);
    setMultiResults(null);
    setActiveTabIndex(0);
  }, []);

  return {
    // Panel
    isOpen,
    setIsOpen,
    // Input
    revisionInstruction,
    setRevisionInstruction,
    revisionScope,
    setRevisionScope,
    // Selection
    selection,
    setSelection,
    // State
    isRevising,
    isReviewing,
    diffChunks,
    originalText,
    revisedText,
    debugInfo,
    // Multi-model
    multiResults,
    activeTabIndex,
    isMultiRevising,
    // Actions
    requestRevision,
    requestMultiRevision,
    switchTab,
    acceptChunk,
    rejectChunk,
    toggleChunk,
    acceptAll,
    rejectAll,
    applyRevision,
    clearRevision,
  };
}
