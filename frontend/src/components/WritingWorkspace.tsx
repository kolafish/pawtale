import { useWorkspaceState } from '../hooks/useWorkspaceState';
import { WorkspaceHome } from './WorkspaceHome';
import { EditorMain } from './EditorMain';
import { WritingSidebar } from './WritingSidebar';

export function WritingWorkspace() {
  const ws = useWorkspaceState();

  if (!ws.currentWorkspace) {
    return (
      <WorkspaceHome
        newTitle={ws.newTitle}
        setNewTitle={ws.setNewTitle}
        newContent={ws.newContent}
        setNewContent={ws.setNewContent}
        selectedExistingStoryId={ws.selectedExistingStoryId}
        setSelectedExistingStoryId={ws.setSelectedExistingStoryId}
        storyOptions={ws.storyOptions}
        isCreating={ws.isCreating}
        isLoading={ws.isLoading}
        onCreateWorkspace={ws.handleCreateWorkspace}
        onOpenExistingStory={ws.handleOpenExistingStory}
      />
    );
  }

  return (
    <div className="h-full flex">
      <div className="flex-1 min-w-0">
        <EditorMain
          title={ws.title}
          setTitle={ws.setTitle}
          editorContent={ws.editorContent}
          setEditorContent={ws.setEditorContent}
          aiModel={ws.aiModel}
          setAiModel={(v) => ws.setAiModel(v as typeof ws.aiModel)}
          continueInstruction={ws.continueInstruction}
          setContinueInstruction={ws.setContinueInstruction}
          debugMode={ws.debugMode}
          setDebugMode={ws.setDebugMode}
          debugInfo={ws.debugInfo}
          saveStatus={ws.saveStatus}
          wordCount={ws.wordCount}
          isSaving={ws.isSaving}
          isContinuing={ws.isContinuing}
          isExtracting={ws.isExtracting}
          onSave={ws.handleSaveContent}
          onContinue={ws.handleContinue}
          onExtract={ws.handleExtract}
          onSwitchWorkspace={() => ws.setCurrentWorkspace(null)}
        />
      </div>
      <WritingSidebar ws={ws} />
    </div>
  );
}
