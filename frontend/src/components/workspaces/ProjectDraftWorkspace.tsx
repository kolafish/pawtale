import { useEffect, useState } from 'react';
import { FileText } from 'lucide-react';
import { AI_MODEL_OPTIONS } from '../../constants/aiModels';
import { useAIRevision } from '../../hooks/useAIRevision';
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

interface ProjectDraftWorkspaceProps {
  project: Project;
  showInspector?: boolean;
  enableAnalyze?: boolean;
}

export function ProjectDraftWorkspace({
  project,
  showInspector = false,
  enableAnalyze = false,
}: ProjectDraftWorkspaceProps) {
  const currentReferenceId = useAppStore((state) => state.currentReferenceId);
  const ps = useProjectState();
  const revision = useAIRevision();
  const refEditor = useReferenceEditor();
  const [showAnalyze, setShowAnalyze] = useState(false);
  const [mobileBinderOpen, setMobileBinderOpen] = useState(false);

  useEffect(() => {
    ps.setIsReviewing(revision.isReviewing);
  }, [revision.isReviewing]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleRequestRevision = async () => {
    if (!ps.currentDocumentId) return;
    await revision.requestRevision(
      project.id,
      ps.currentDocumentId,
      ps.documentContent,
      ps.aiModel,
      ps.debugMode,
    );
  };

  const handleRequestMultiRevision = async () => {
    if (!ps.currentDocumentId) return;
    const models = AI_MODEL_OPTIONS.map(m => m.value);
    await revision.requestMultiRevision(
      project.id,
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
          onApplyRevision={handleApplyRevision}
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
      if (refType === 'character') {
        return (
          <CharacterEditor
            data={refEditor.referenceData}
            saveStatus={refEditor.saveStatus}
            onUpdate={refEditor.updateField}
            onUpdateMeta={refEditor.updateMeta}
            onSave={refEditor.handleSave}
            onSyncGlobal={refEditor.syncFromGlobal}
            onExportGlobal={refEditor.exportToGlobal}
            isExporting={refEditor.isExporting}
          />
        );
      }
      if (refType === 'outline') {
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
          onExportGlobal={refEditor.referenceData.type === 'location' ? refEditor.exportToGlobal : undefined}
          isExporting={refEditor.isExporting}
        />
      );
    }

    return (
      <div className="h-full flex items-center justify-center text-gray-400">
        <p>选择一个条目开始编辑</p>
      </div>
    );
  };

  const handleSelectDocument = (docId: string) => {
    ps.switchDocument(docId);
    setMobileBinderOpen(false);
  };

  const handleSelectReference = (refId: string) => {
    ps.selectReference(refId);
    setMobileBinderOpen(false);
  };

  return (
    <div className="h-full w-full flex relative">
      {/* Mobile Binder overlay */}
      {mobileBinderOpen && (
        <div
          className="md:hidden fixed inset-0 bg-black/30 z-40"
          onClick={() => setMobileBinderOpen(false)}
        />
      )}
      <div className={`
        ${mobileBinderOpen ? 'fixed inset-y-0 left-0 z-50' : 'hidden'}
        md:relative md:block
      `}>
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
      </div>
      <div className="flex-1 min-w-0 relative">
        <button
          onClick={() => setMobileBinderOpen(true)}
          className="md:hidden fixed bottom-4 left-4 z-30 w-12 h-12 rounded-full bg-pink-500 text-white shadow-lg flex items-center justify-center"
        >
          <FileText size={20} />
        </button>
        {renderEditor()}
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
      </div>
      {revision.isOpen && ps.currentDocumentId && (
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
          onRequestRevision={handleRequestRevision}
          onRequestMultiRevision={handleRequestMultiRevision}
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
      )}
      {enableAnalyze && showAnalyze && (
        <AnalyzeDialog
          projectId={project.id}
          onClose={() => setShowAnalyze(false)}
          onComplete={() => ps.refreshProject()}
        />
      )}
    </div>
  );
}
