import { updateMemoirImageAsset } from "../../services/memoir-book-api";

Component({
  properties: {
    projectId: { type: String, value: "" },
    assetId: { type: String, value: "" },
    caption: { type: String, value: "" },
    label: { type: String, value: "照片说明" },
    disabled: { type: Boolean, value: false },
  },
  data: { editing: false, value: "", busy: false, error: "", saved: false },
  methods: {
    edit() {
      if (this.properties.disabled || this.data.busy) return;
      this.setData({ editing: true, value: this.properties.caption, error: "", saved: false });
    },
    change(event: { detail: { value: string } }) {
      this.setData({ value: event.detail.value });
    },
    cancel() {
      if (!this.data.busy) this.setData({ editing: false, error: "" });
    },
    async save(event: { detail: { value: string } }) {
      if (this.properties.disabled || this.data.busy) return;
      this.setData({ busy: true, error: "", saved: false });
      this.triggerEvent("busy", { busy: true });
      try {
        const asset = await updateMemoirImageAsset(this.properties.projectId, this.properties.assetId,
          { caption: event.detail.value.trim().replace(/\s+/g, " ") });
        this.setData({ editing: false, saved: true });
        this.triggerEvent("saved", { asset });
      } catch (error) {
        this.setData({ error: error instanceof Error ? error.message : "照片说明保存失败，请重试" });
      } finally {
        this.setData({ busy: false });
        this.triggerEvent("busy", { busy: false });
      }
    },
  },
});
