import { useState, useCallback } from 'react';
import DiffMatchPatch from 'diff-match-patch';
import { projectsApi } from '../api';
import type { DiffChunk, SelectionRange, AIDebugInfo } from '../types';

const dmp = new DiffMatchPatch();

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

  const isReviewing = diffChunks !== null;

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

      // Compute diff
      const diffs = dmp.diff_main(orig, revised);
      dmp.diff_cleanupSemantic(diffs);

      const chunks: DiffChunk[] = diffs.map(([op, text], i) => ({
        id: `chunk-${i}`,
        type: op === 0 ? 'equal' : op === 1 ? 'insert' : 'delete',
        text,
        accepted: null,
      }));

      setDiffChunks(chunks);
    } catch (e) {
      console.error('AI revision failed:', e);
      throw e;
    } finally {
      setIsRevising(false);
    }
  }, [revisionInstruction, revisionScope, selection]);

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
      // Cycle: null → true → false → null
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
        // Include if accepted (or still pending — treat pending as accepted)
        if (chunk.accepted !== false) {
          parts.push(chunk.text);
        }
      } else if (chunk.type === 'delete') {
        // Keep original text if rejected (or still pending — treat pending as deleted)
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
    // Actions
    requestRevision,
    acceptChunk,
    rejectChunk,
    toggleChunk,
    acceptAll,
    rejectAll,
    applyRevision,
    clearRevision,
  };
}
