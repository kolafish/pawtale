import { Trash2 } from 'lucide-react';
import type { Character, Scene, StoryOutline } from '../types';

interface ResourceLinkerProps {
  linkedCharacters: Character[];
  globalCharacterCandidates: Character[];
  selectedCharacterToLink: string;
  setSelectedCharacterToLink: (v: string) => void;
  allScenes: Scene[];
  linkedSceneId: string;
  linkedScene: Scene | null;
  allOutlines: StoryOutline[];
  linkedOutlineId: string;
  linkedOutline: StoryOutline | null;
  isUpdatingCharacters: boolean;
  onAddStoryCharacter: () => void;
  onRemoveStoryCharacter: (id: string) => void;
  onLinkScene: (sceneId: string) => void;
  onLinkOutline: (outlineId: string) => void;
}

export function ResourceLinker({
  linkedCharacters,
  globalCharacterCandidates,
  selectedCharacterToLink,
  setSelectedCharacterToLink,
  allScenes,
  linkedSceneId,
  linkedScene,
  allOutlines,
  linkedOutlineId,
  linkedOutline,
  isUpdatingCharacters,
  onAddStoryCharacter,
  onRemoveStoryCharacter,
  onLinkScene,
  onLinkOutline,
}: ResourceLinkerProps) {
  return (
    <section className="space-y-3 rounded-lg border border-pink-100 p-3 bg-pink-50/30">
      <h3 className="font-semibold text-gray-800">故事关联资源</h3>

      {/* Characters */}
      <div className="space-y-2">
        <h4 className="text-sm font-medium text-gray-700">关联角色（故事内）</h4>
        <div className="flex flex-wrap gap-2">
          {linkedCharacters.length === 0 && (
            <span className="text-xs text-gray-500">当前暂无关联角色</span>
          )}
          {linkedCharacters.map((char) => (
            <span
              key={char.id}
              className="inline-flex items-center gap-1 px-2 py-1 text-xs rounded-full bg-white border border-gray-200"
            >
              {char.name}
              <button
                onClick={() => onRemoveStoryCharacter(char.id)}
                className="text-gray-400 hover:text-red-500"
                title="移除关联"
              >
                <Trash2 size={12} />
              </button>
            </span>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <select
            value={selectedCharacterToLink}
            onChange={(e) => setSelectedCharacterToLink(e.target.value)}
            className="flex-1 px-2 py-1.5 text-sm rounded border border-gray-200"
          >
            <option value="">从全局角色库添加角色</option>
            {globalCharacterCandidates.map((char) => (
              <option key={char.id} value={char.id}>
                {char.name}
              </option>
            ))}
          </select>
          <button
            onClick={onAddStoryCharacter}
            disabled={!selectedCharacterToLink || isUpdatingCharacters}
            className="text-xs px-2 py-1.5 rounded border border-gray-200 hover:bg-gray-50 disabled:opacity-60"
          >
            添加
          </button>
        </div>
      </div>

      {/* Scene */}
      <div className="space-y-2">
        <h4 className="text-sm font-medium text-gray-700">关联场景（故事主场景）</h4>
        <select
          value={linkedSceneId}
          onChange={(e) => onLinkScene(e.target.value)}
          className="w-full px-2 py-1.5 text-sm rounded border border-gray-200"
        >
          <option value="">未关联场景</option>
          {allScenes.map((scene) => (
            <option key={scene.id} value={scene.id}>
              {scene.name}
            </option>
          ))}
        </select>
        {linkedScene && (
          <p className="text-xs text-gray-500">当前：{linkedScene.name}</p>
        )}
      </div>

      {/* Outline */}
      <div className="space-y-2">
        <h4 className="text-sm font-medium text-gray-700">关联大纲（故事主大纲）</h4>
        <select
          value={linkedOutlineId}
          onChange={(e) => onLinkOutline(e.target.value)}
          className="w-full px-2 py-1.5 text-sm rounded border border-gray-200"
        >
          <option value="">未关联大纲</option>
          {allOutlines.map((outline) => (
            <option key={outline.id} value={outline.id}>
              {outline.title}
            </option>
          ))}
        </select>
        {linkedOutline ? (
          <p className="text-xs text-gray-500">当前：{linkedOutline.title}</p>
        ) : (
          <p className="text-xs text-gray-500">全局大纲库为空或尚未绑定</p>
        )}
      </div>
    </section>
  );
}
