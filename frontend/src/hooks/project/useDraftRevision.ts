import { useEffect } from 'react';
import { getAIModelOptions, type AiModelValue } from '../../constants/aiModels';
import { useAIRevision } from '../useAIRevision';
import { useAppStore } from '../../stores/appStore';

interface UseDraftRevisionArgs {
  projectId: string;
  currentDocumentId: string | null;
  documentContent: string;
  aiModel: AiModelValue;
  debugMode: boolean;
  setDocumentContent: (content: string) => void;
  setIsReviewing: (value: boolean) => void;
}

export function useDraftRevision({
  projectId,
  currentDocumentId,
  documentContent,
  aiModel,
  debugMode,
  setDocumentContent,
  setIsReviewing,
}: UseDraftRevisionArgs) {
  const revision = useAIRevision();
  const isAdmin = useAppStore((state) => state.isAdmin);

  useEffect(() => {
    setIsReviewing(revision.isReviewing);
    return () => setIsReviewing(false);
  }, [revision.isReviewing, setIsReviewing]);

  const requestRevision = async () => {
    if (!currentDocumentId) return;
    await revision.requestRevision(
      projectId,
      currentDocumentId,
      documentContent,
      aiModel,
      debugMode,
    );
  };

  const requestMultiRevision = async () => {
    if (!currentDocumentId) return;
    const models = getAIModelOptions(isAdmin).map((model) => model.value);
    await revision.requestMultiRevision(
      projectId,
      currentDocumentId,
      documentContent,
      models,
      debugMode,
    );
  };

  const applyRevision = () => {
    const result = revision.applyRevision();
    if (result === null) return;

    if (revision.revisionScope === 'selection' && revision.selection) {
      const before = documentContent.substring(0, revision.selection.start);
      const after = documentContent.substring(revision.selection.end);
      setDocumentContent(before + result + after);
    } else {
      setDocumentContent(result);
    }
    revision.clearRevision();
  };

  return {
    ...revision,
    requestRevision,
    requestMultiRevision,
    applyRevision,
  };
}
