import { useEffect, useState } from 'react';
import { useAppStore } from '../stores/appStore';
import { useProjectState } from '../hooks/useProjectState';
import { useAIRevision } from '../hooks/useAIRevision';
import { useReferenceEditor } from '../hooks/useReferenceEditor';
import { AI_MODEL_OPTIONS } from '../constants/aiModels';
import { ProjectHome } from './ProjectHome';
import { Binder } from './Binder';
import { EditorMain } from './EditorMain';
import { Inspector } from './Inspector';
import { AIAssistPanel } from './AIAssistPanel';
import { CharacterEditor } from './CharacterEditor';
import { OutlineEditor } from './OutlineEditor';
import { NoteEditor } from './NoteEditor';
import { AnalyzeDialog } from './AnalyzeDialog';

export function WritingWorkspace() {
  const { currentProject, currentReferenceId } = useAppStore();
  const ps = useProjectState();
  const revision = useAIRevision();
  const refEditor = useReferenceEditor();
  const [showAnalyze, setShowAnalyze] = useState(false);

  // Sync reviewing state to pause auto-save during diff review
  useEffect(() => {
    ps.setIsReviewing(revision.isReviewing);
  }, [revision.isReviewing]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!currentProject) {
    return <ProjectHome />;
  }

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

  // Determine which editor to show
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
      // location / note
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

  return (
    <div className="h-full w-full flex">
      <Binder
        project={currentProject}
        currentDocumentId={ps.currentDocumentId}
        currentReferenceId={currentReferenceId}
        onSelectDocument={ps.switchDocument}
        onSelectReference={ps.selectReference}
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
        onAnalyzeProject={() => setShowAnalyze(true)}
      />
      <div className="flex-1 min-w-0 relative">
        {renderEditor()}
        {ps.currentDocumentId && (
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
      {showAnalyze && currentProject && (
        <AnalyzeDialog
          projectId={currentProject.id}
          onClose={() => setShowAnalyze(false)}
          onComplete={() => ps.refreshProject()}
        />
      )}
    </div>
  );
}
