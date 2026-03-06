import { useState } from 'react';
import { ChevronRight, Download, Sparkles, Link2, BookOpen } from 'lucide-react';
import { useAppStore } from '../stores/appStore';
import { storiesApi } from '../api';
import { ExtractionPanel } from './ExtractionPanel';
import { ResourceLinker } from './ResourceLinker';
import type { useWorkspaceState } from '../hooks/useWorkspaceState';

type WorkspaceState = ReturnType<typeof useWorkspaceState>;

interface WritingSidebarProps {
  ws: WorkspaceState;
}

const TABS = [
  { id: 'extraction' as const, label: '提炼', icon: Sparkles },
  { id: 'resources' as const, label: '资源', icon: Link2 },
  { id: 'works' as const, label: '作品', icon: BookOpen },
] as const;

export function WritingSidebar({ ws }: WritingSidebarProps) {
  const { writingSidebarTab, setWritingSidebarTab } = useAppStore();
  const [collapsed, setCollapsed] = useState(false);

  return (
    <div
      className={`relative border-l border-pink-100 bg-white flex flex-col transition-all duration-300 ${
        collapsed ? 'w-0 overflow-hidden' : 'w-[380px]'
      }`}
    >
      {/* Collapse toggle */}
      <button
        onClick={() => setCollapsed(!collapsed)}
        className="absolute -left-3 top-4 z-10 w-6 h-6 rounded-full bg-white border border-pink-100 shadow-sm flex items-center justify-center hover:bg-pink-50"
      >
        <ChevronRight
          size={14}
          className={`text-gray-500 transition-transform ${collapsed ? 'rotate-180' : ''}`}
        />
      </button>

      {!collapsed && (
        <>
          {/* Tab switcher */}
          <div className="flex border-b border-pink-100">
            {TABS.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setWritingSidebarTab(tab.id)}
                className={`flex-1 flex items-center justify-center gap-1.5 py-2.5 text-sm font-medium transition-colors ${
                  writingSidebarTab === tab.id
                    ? 'text-pink-600 border-b-2 border-pink-500'
                    : 'text-gray-500 hover:text-gray-700'
                }`}
              >
                <tab.icon size={14} />
                {tab.label}
              </button>
            ))}
          </div>

          {/* Tab content */}
          <div className="flex-1 overflow-y-auto p-4">
            {writingSidebarTab === 'extraction' && (
              <ExtractionPanel
                extractedCharacters={ws.extractedCharacters}
                setExtractedCharacters={ws.setExtractedCharacters}
                extractedScenes={ws.extractedScenes}
                setExtractedScenes={ws.setExtractedScenes}
                extractedOutlinePoints={ws.extractedOutlinePoints}
                setExtractedOutlinePoints={ws.setExtractedOutlinePoints}
                extractionNotesText={ws.extractionNotesText}
                setExtractionNotesText={ws.setExtractionNotesText}
                isSavingExtraction={ws.isSavingExtraction}
                onSaveExtraction={ws.handleSaveExtraction}
                onSaveCharacterToLibrary={ws.handleSaveCharacterToLibrary}
                onSaveSceneToLibrary={ws.handleSaveSceneToLibrary}
                emptyCharacter={ws.emptyCharacter}
                emptyScene={ws.emptyScene}
                emptyOutlinePoint={ws.emptyOutlinePoint}
              />
            )}

            {writingSidebarTab === 'resources' && (
              <ResourceLinker
                linkedCharacters={ws.linkedCharacters}
                globalCharacterCandidates={ws.globalCharacterCandidates}
                selectedCharacterToLink={ws.selectedCharacterToLink}
                setSelectedCharacterToLink={ws.setSelectedCharacterToLink}
                allScenes={ws.allScenes}
                linkedSceneId={ws.linkedSceneId}
                linkedScene={ws.linkedScene}
                allOutlines={ws.allOutlines}
                linkedOutlineId={ws.linkedOutlineId}
                linkedOutline={ws.linkedOutline}
                isUpdatingCharacters={ws.isUpdatingCharacters}
                onAddStoryCharacter={ws.handleAddStoryCharacter}
                onRemoveStoryCharacter={ws.handleRemoveStoryCharacter}
                onLinkScene={ws.handleLinkScene}
                onLinkOutline={ws.handleLinkOutline}
              />
            )}

            {writingSidebarTab === 'works' && (
              <div className="space-y-3">
                <h3 className="font-semibold text-gray-800">作品列表</h3>
                {ws.storyOptions.length === 0 ? (
                  <p className="text-sm text-gray-500">暂无作品</p>
                ) : (
                  ws.storyOptions.map((story) => (
                    <div
                      key={story.id}
                      className="p-3 rounded-lg border border-gray-100 hover:border-pink-200 cursor-pointer transition-colors"
                      onClick={async () => {
                        const workspace = await storiesApi.getWorkspace(story.id);
                        ws.setCurrentWorkspace(workspace);
                      }}
                    >
                      <p className="text-sm font-medium text-gray-800 truncate">{story.title}</p>
                      <p className="text-xs text-gray-400 mt-1">
                        {(story.current_path || Object.keys(story.nodes)).length} 章
                      </p>
                    </div>
                  ))
                )}
              </div>
            )}
          </div>

          {/* Export button */}
          <div className="px-4 py-3 border-t border-pink-100">
            <button
              onClick={ws.handleExportTxt}
              className="w-full inline-flex items-center justify-center gap-2 px-3 py-2 text-sm rounded-lg border border-gray-200 hover:bg-gray-50"
            >
              <Download size={14} />
              导出 .txt
            </button>
          </div>
        </>
      )}
    </div>
  );
}
