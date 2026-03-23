import { useEffect } from 'react';
import { useAppStore } from '../stores/appStore';
import { useProjectState } from '../hooks/useProjectState';
import { useAIRevision } from '../hooks/useAIRevision';
import { AI_MODEL_OPTIONS } from '../constants/aiModels';
import { ProjectHome } from './ProjectHome';
import { Binder } from './Binder';
import { EditorMain } from './EditorMain';
import { Inspector } from './Inspector';
import { AIAssistPanel } from './AIAssistPanel';

export function WritingWorkspace() {
  const { currentProject } = useAppStore();
  const ps = useProjectState();
  const revision = useAIRevision();

  // Sync reviewing state to pause auto-save during diff review
  useEffect(() => {
    ps.setIsReviewing(revision.isReviewing);
  }, [revision.isReviewing]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!currentProject) {
    return <ProjectHome />;
  }

  console.log('[WritingWorkspace] Rendering with project:', currentProject.title);
  console.log('[WritingWorkspace] Project state:', {
    hasGroups: !!currentProject.groups,
    groupsLength: currentProject.groups?.length,
    currentDocId: ps.currentDocumentId,
  });

  const handleRequestRevision = async () => {
    if (!currentProject || !ps.currentDocumentId) return;
    await revision.requestRevision(
      currentProject.id,
      ps.currentDocumentId,
      ps.documentContent,
      ps.aiModel,
      ps.debugMode,
    );
  };

  const handleRequestMultiRevision = async () => {
    if (!currentProject || !ps.currentDocumentId) return;
    const models = AI_MODEL_OPTIONS.map(m => m.value);
    await revision.requestMultiRevision(
      currentProject.id,
      ps.currentDocumentId,
      ps.documentContent,
      models,
      ps.debugMode,
    );
  };

  const handleApplyRevision = () => {
    const result = revision.applyRevision();
    if (result !== null) {
      if (revision.revisionScope === 'selection' && revision.selection) {
        const before = ps.documentContent.substring(0, revision.selection.start);
        const after = ps.documentContent.substring(revision.selection.end);
        ps.setDocumentContent(before + result + after);
      } else {
        ps.setDocumentContent(result);
      }
      revision.clearRevision();
    }
  };

  return (
    <div className="h-full w-full flex">
      <Binder
        project={currentProject}
        currentDocumentId={ps.currentDocumentId}
        onSelectDocument={ps.switchDocument}
        onCreateGroup={ps.createGroup}
        onRenameGroup={ps.renameGroup}
        onDeleteGroup={ps.deleteGroup}
        onCreateDocument={ps.createDocument}
        onRenameDocument={ps.renameDocument}
        onDeleteDocument={ps.deleteDocument}
        onReorderGroups={ps.reorderGroups}
        onReorderDocuments={ps.reorderDocuments}
      />
      <div className="flex-1 min-w-0 relative">
        {ps.currentDocumentId ? (
          <EditorMain
            title={ps.documentName}
            setTitle={ps.setDocumentName}
            editorContent={ps.documentContent}
            setEditorContent={ps.setDocumentContent}
            saveStatus={ps.saveStatus}
            wordCount={ps.wordCount}
            totalWordCount={ps.totalWordCount}
            isSaving={ps.isSaving}
            onSave={ps.handleSave}
            onCloseProject={ps.closeProject}
            // Selection
            onSelectionChange={revision.setSelection}
            // Diff review
            diffChunks={revision.diffChunks}
            onAcceptChunk={revision.acceptChunk}
            onRejectChunk={revision.rejectChunk}
            onToggleChunk={revision.toggleChunk}
            onAcceptAll={revision.acceptAll}
            onRejectAll={revision.rejectAll}
            onApplyRevision={handleApplyRevision}
            onCancelRevision={revision.clearRevision}
            // Multi-model
            multiResults={revision.multiResults}
            activeTabIndex={revision.activeTabIndex}
            onSwitchTab={revision.switchTab}
            // AI panel
            isAIPanelOpen={revision.isOpen}
            onToggleAIPanel={() => revision.setIsOpen(!revision.isOpen)}
          />
        ) : (
          <div className="h-full flex items-center justify-center text-gray-400">
            <p>选择一个文档开始写作</p>
          </div>
        )}
        <Inspector
          project={currentProject}
          currentDocumentId={ps.currentDocumentId}
          synopsis={ps.synopsis}
          setSynopsis={ps.setSynopsis}
          status={ps.status}
          setStatus={ps.setStatus}
          label={ps.label}
          setLabel={ps.setLabel}
          wordCount={ps.wordCount}
          totalWordCount={ps.totalWordCount}
          onExport={ps.handleExportTxt}
        />
      </div>
      {revision.isOpen && ps.currentDocumentId && (
        <AIAssistPanel
          onClose={() => revision.setIsOpen(false)}
          // Revision
          revisionInstruction={revision.revisionInstruction}
          setRevisionInstruction={revision.setRevisionInstruction}
          revisionScope={revision.revisionScope}
          setRevisionScope={revision.setRevisionScope}
          selection={revision.selection}
          isRevising={revision.isRevising}
          isMultiRevising={revision.isMultiRevising}
          isReviewing={revision.isReviewing}
          onRequestRevision={handleRequestRevision}
          onRequestMultiRevision={handleRequestMultiRevision}
          // Continue
          continueInstruction={ps.continueInstruction}
          setContinueInstruction={ps.setContinueInstruction}
          isContinuing={ps.isContinuing}
          onContinue={ps.handleContinue}
          hasContent={!!ps.documentContent.trim()}
          // Model & debug
          aiModel={ps.aiModel}
          setAiModel={(v) => ps.setAiModel(v as typeof ps.aiModel)}
          debugMode={ps.debugMode}
          setDebugMode={ps.setDebugMode}
          debugInfo={ps.debugInfo}
          revisionDebugInfo={revision.debugInfo}
        />
      )}
    </div>
  );
}
