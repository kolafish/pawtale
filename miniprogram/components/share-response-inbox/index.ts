import { adoptShareResponse, listShareResponses, readShareResponse, downloadShareResponseAudio, type ShareResponseItem } from "../../services/share-responses";

import { AudioPlaybackController } from "../../services/audio-playback";
const runtimes = new WeakMap<object, { generation: number; playback: AudioPlaybackController }>();
function runtime(owner: object) {
  let value = runtimes.get(owner);
  if (!value) { value = { generation: 0, playback: new AudioPlaybackController() }; runtimes.set(owner, value); }
  return value;
}

Component({
  options: { addGlobalClass: true },
  properties: { projectId: { type: String, value: "" }, userId: { type: String, value: "" } },
  data: { items: [] as ShareResponseItem[], unread: 0, next: "", open: false, selected: null as ShareResponseItem | null, busy: false, error: "", audioPlaying: false },
  observers: { "projectId,userId"() {
    runtime(this).generation += 1; runtime(this).playback.stop();
    this.setData({ audioPlaying: false, items: [], selected: null, open: false, unread: 0, next: "", busy: false, error: "" });
    void this.load();
  } },
  pageLifetimes: { show() { void this.load(); }, hide() { runtime(this).generation += 1; runtime(this).playback.stop(); this.setData({ busy: false, audioPlaying: false }); } },
  lifetimes: { detached() { runtime(this).generation += 1; runtime(this).playback.destroy(); runtimes.delete(this); } },
  methods: {
    async load(more = false) {
      if (!this.properties.projectId || !this.properties.userId || this.data.busy) return;
      const generation = runtime(this).generation;
      this.setData({ busy: true, error: "" });
      try {
        const result = await listShareResponses(this.properties.projectId, more ? this.data.next : "");
        if (generation !== runtime(this).generation) return;
        this.setData({ items: more ? [...this.data.items, ...result.items] : result.items, next: result.next_before_id || "", unread: result.unread_count });
      } catch (error) {
        if (generation === runtime(this).generation) this.setData({ error: error instanceof Error ? error.message : "回应暂时未能读取" });
      } finally { if (generation === runtime(this).generation) this.setData({ busy: false }); }
    },
    retry() { void this.load(); },
    loadMore() { if (this.data.next) void this.load(true); },
    toggle() { runtime(this).playback.stop(); this.setData({ open: !this.data.open, selected: null, audioPlaying: false }); },
    async select(event: { currentTarget: { dataset: { id?: string } } }) {
      const selected = this.data.items.find((item: ShareResponseItem) => item.id === event.currentTarget.dataset.id);
      if (!selected || this.data.busy) return;
      runtime(this).playback.stop(); this.setData({ selected, error: "", audioPlaying: false });
      const generation = runtime(this).generation;
      try {
        const row = await readShareResponse(selected.id);
        if (generation !== runtime(this).generation) return;
        this.setData({ items: this.data.items.map((item: ShareResponseItem) => item.id === row.id ? row : item), unread: Math.max(0, this.data.unread - (selected.read_at ? 0 : 1)) });
      } catch (error) { if (generation === runtime(this).generation) this.setData({ error: error instanceof Error ? error.message : "暂时未能标记已读" }); }
    },
    back() { runtime(this).playback.stop(); this.setData({ selected: null, error: "", audioPlaying: false }); },
    async playOriginal() {
      const selected = this.data.selected;
      if (!selected?.voice_capture_id) return;
      const playback = runtime(this).playback;
      if (this.data.audioPlaying) { playback.stop(); this.setData({ audioPlaying: false }); return; }
      const generation = runtime(this).generation;
      playback.setDownloadAudioFile(() => downloadShareResponseAudio(selected.id));
      const token = playback.nextToken();
      const isCurrent = () => generation === runtime(this).generation
        && playback.isCurrentToken(token) && this.data.selected?.id === selected.id;
      try {
        await playback.playFile(selected.id, token, { onPlay: () => {
          if (isCurrent()) this.setData({ audioPlaying: true });
        } });
      } catch (error) {
        if (isCurrent()) this.setData({ error: error instanceof Error ? error.message : "原音暂时不能播放" });
      } finally { if (isCurrent()) this.setData({ audioPlaying: false }); }
    },
    async adopt() {
      const selected = this.data.selected;
      if (!selected || selected.adopted_at || this.data.busy) return;
      const generation = runtime(this).generation;
      this.setData({ busy: true, error: "" });
      try {
        const row = await adoptShareResponse(selected.id);
        if (generation !== runtime(this).generation) return;
        this.setData({ selected: row, items: this.data.items.map((item: ShareResponseItem) => item.id === row.id ? row : item) });
      } catch (error) { if (generation === runtime(this).generation) this.setData({ error: error instanceof Error ? error.message : "暂时未能收录，请重试" }); }
      finally { if (generation === runtime(this).generation) this.setData({ busy: false }); }
    },
  },
});
