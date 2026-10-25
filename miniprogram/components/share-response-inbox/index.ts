import { adoptShareResponse, listShareResponses, readShareResponse, type ShareResponseItem } from "../../services/share-responses";

const runtimes = new WeakMap<object, { generation: number }>();
function runtime(owner: object) {
  let value = runtimes.get(owner);
  if (!value) { value = { generation: 0 }; runtimes.set(owner, value); }
  return value;
}

Component({
  options: { addGlobalClass: true },
  properties: { projectId: { type: String, value: "" }, userId: { type: String, value: "" } },
  data: { items: [] as ShareResponseItem[], unread: 0, next: "", open: false, selected: null as ShareResponseItem | null, busy: false, error: "" },
  observers: { "projectId,userId"() {
    runtime(this).generation += 1;
    this.setData({ items: [], selected: null, open: false, unread: 0, next: "", busy: false, error: "" });
    void this.load();
  } },
  pageLifetimes: { show() { void this.load(); }, hide() { runtime(this).generation += 1; this.setData({ busy: false }); } },
  lifetimes: { detached() { runtime(this).generation += 1; } },
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
    toggle() { this.setData({ open: !this.data.open, selected: null }); },
    async select(event: { currentTarget: { dataset: { id?: string } } }) {
      const selected = this.data.items.find((item: ShareResponseItem) => item.id === event.currentTarget.dataset.id);
      if (!selected || this.data.busy) return;
      this.setData({ selected, error: "" });
      const generation = runtime(this).generation;
      try {
        const row = await readShareResponse(selected.id);
        if (generation !== runtime(this).generation) return;
        this.setData({ items: this.data.items.map((item: ShareResponseItem) => item.id === row.id ? row : item), unread: Math.max(0, this.data.unread - (selected.read_at ? 0 : 1)) });
      } catch (error) { if (generation === runtime(this).generation) this.setData({ error: error instanceof Error ? error.message : "暂时未能标记已读" }); }
    },
    back() { this.setData({ selected: null, error: "" }); },
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
