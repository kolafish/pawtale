import { useState, type ReactNode } from 'react';
import { REFERENCE_TYPES } from '../../domain/references';
import { useDraftRevision } from '../../hooks/project/useDraftRevision';
import { useProjectState } from '../../hooks/useProjectState';
import { useReferenceEditor } from '../../hooks/useReferenceEditor';
import { useAppStore } from '../../stores/appStore';
import type { Project } from '../../types';
import { AIAssistPanel } from '../AIAssistPanel';
import { AnalyzeDialog } from '../AnalyzeDialog';
import { Binder } from '../Binder';
import { CharacterEditor } from '../CharacterEditor';
import { EditorMain } from '../EditorMain';
import { Inspector } from '../Inspector';
import { NoteEditor } from '../NoteEditor';
import { OutlineEditor } from '../OutlineEditor';
import { WorkspaceShell } from './WorkspaceShell';

interface ProjectDraftWorkspaceProps {
  project: Project;
  showInspector?: boolean;
  enableAnalyze?: boolean;
  workspacePanel?: ReactNode;
}

export function ProjectDraftWorkspace({
  project,
  showInspector = false,
  enableAnalyze = false,
  workspacePanel,
}: ProjectDraftWorkspaceProps) {
  const currentReferenceId = useAppStore((state) => state.currentReferenceId);
  const ps = useProjectState();
  const revision = useDraftRevision({
    projectId: project.id,
    currentDocumentId: ps.currentDocumentId,
    documentContent: ps.documentContent,
    aiModel: ps.aiModel,
    debugMode: ps.debugMode,
    setDocumentContent: ps.setDocumentContent,
    setIsReviewing: ps.setIsReviewing,
  });
  const refEditor = useReferenceEditor();
  const [showAnalyze, setShowAnalyze] = useState(false);

  const renderEditor = () => {
    if (ps.currentDocumentId) {
      return (
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
          onSelectionChange={revision.setSelection}
          diffChunks={revision.diffChunks}
          onAcceptChunk={revision.acceptChunk}
          onRejectChunk={revision.rejectChunk}
          onToggleChunk={revision.toggleChunk}
          onAcceptAll={revision.acceptAll}
          onRejectAll={revision.rejectAll}
          onApplyRevision={revision.applyRevision}
          onCancelRevision={revision.clearRevision}
          multiResults={revision.multiResults}
          activeTabIndex={revision.activeTabIndex}
          onSwitchTab={revision.switchTab}
          isAIPanelOpen={revision.isOpen}
          onToggleAIPanel={() => revision.setIsOpen(!revision.isOpen)}
        />
      );
    }

    if (currentReferenceId && refEditor.referenceData) {
      const refType = refEditor.referenceType;
      if (refType === REFERENCE_TYPES.CHARACTER) {
        return (
          <CharacterEditor
            data={refEditor.referenceData}
            saveStatus={refEditor.saveStatus}
            onUpdate={refEditor.updateField}
            onUpdateMeta={refEditor.updateMeta}
            onSave={refEditor.handleSave}
          />
        );
      }
      if (refType === REFERENCE_TYPES.OUTLINE) {
        return (
          <OutlineEditor
            data={refEditor.referenceData}
            saveStatus={refEditor.saveStatus}
            onUpdate={refEditor.updateField}
            onSave={refEditor.handleSave}
          />
        );
      }
      return (
        <NoteEditor
          data={refEditor.referenceData}
          saveStatus={refEditor.saveStatus}
          onUpdate={refEditor.updateField}
          onUpdateMeta={refEditor.updateMeta}
          onSave={refEditor.handleSave}
        />
      );
    }

    return (
      <div className="h-full flex items-center justify-center text-gray-400">
        <p>选择一个条目开始编辑</p>
      </div>
    );
  };

  const renderBinder = (closeBinder: () => void) => {
    const handleSelectDocument = (docId: string) => {
      ps.switchDocument(docId);
      closeBinder();
    };

    const handleSelectReference = (refId: string) => {
      ps.selectReference(refId);
      closeBinder();
    };

    return (
      <Binder
        project={project}
        currentDocumentId={ps.currentDocumentId}
        currentReferenceId={currentReferenceId}
        onSelectDocument={handleSelectDocument}
        onSelectReference={handleSelectReference}
        onCreateGroup={ps.createGroup}
        onRenameGroup={ps.renameGroup}
        onDeleteGroup={ps.deleteGroup}
        onCreateDocument={ps.createDocument}
        onRenameDocument={ps.renameDocument}
        onDeleteDocument={ps.deleteDocument}
        onReorderGroups={ps.reorderGroups}
        onReorderDocuments={ps.reorderDocuments}
        onCreateReference={ps.createReference}
        onRenameReference={ps.renameReference}
        onDeleteReference={ps.deleteReference}
        onAnalyzeProject={enableAnalyze ? () => setShowAnalyze(true) : undefined}
      />
    );
  };

  const aiPanel = revision.isOpen && ps.currentDocumentId ? (
    <AIAssistPanel
      onClose={() => revision.setIsOpen(false)}
      revisionInstruction={revision.revisionInstruction}
      setRevisionInstruction={revision.setRevisionInstruction}
      revisionScope={revision.revisionScope}
      setRevisionScope={revision.setRevisionScope}
      selection={revision.selection}
      isRevising={revision.isRevising}
      isMultiRevising={revision.isMultiRevising}
      isReviewing={revision.isReviewing}
      onRequestRevision={revision.requestRevision}
      onRequestMultiRevision={revision.requestMultiRevision}
      continueInstruction={ps.continueInstruction}
      setContinueInstruction={ps.setContinueInstruction}
      isContinuing={ps.isContinuing}
      onContinue={ps.handleContinue}
      hasContent={!!ps.documentContent.trim()}
      aiModel={ps.aiModel}
      setAiModel={(v) => ps.setAiModel(v as typeof ps.aiModel)}
      debugMode={ps.debugMode}
      setDebugMode={ps.setDebugMode}
      debugInfo={ps.debugInfo}
      revisionDebugInfo={revision.debugInfo}
    />
  ) : null;

  const dialogs = enableAnalyze && showAnalyze ? (
    <AnalyzeDialog
      projectId={project.id}
      onClose={() => setShowAnalyze(false)}
      onComplete={() => ps.refreshProject()}
    />
  ) : null;

  return (
    <WorkspaceShell
      renderBinder={renderBinder}
      workspacePanel={workspacePanel}
      sidePanel={aiPanel}
      dialogs={dialogs}
    >
      <div className="flex-1 min-h-0">
        {renderEditor()}
      </div>
      {showInspector && ps.currentDocumentId && (
        <Inspector
          project={project}
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
      )}
    </WorkspaceShell>
  );
}
