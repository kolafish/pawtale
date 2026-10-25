import { submitShareResponse } from "../../services/share-responses";
import { ApiError } from "../../services/request";

const runtimes = new WeakMap<object, { generation: number; requestId: string; requestContent: string }>();
function runtime(owner: object) {
  let value = runtimes.get(owner);
  if (!value) { value = { generation: 0, requestId: "", requestContent: "" }; runtimes.set(owner, value); }
  return value;
}

Component({
  options: { addGlobalClass: true },
  properties: { readingToken: { type: String, value: "" }, videoCode: { type: String, value: "" } },
  data: { open: false, content: "", busy: false, error: "", sent: false },
  observers: {
    "readingToken,videoCode"() {
      runtime(this).generation += 1;
      runtime(this).requestId = "";
      runtime(this).requestContent = "";
      this.setData({ open: false, content: "", busy: false, error: "", sent: false });
    },
  },
  lifetimes: { detached() { runtime(this).generation += 1; } },
  methods: {
    openForm() { this.setData({ open: true, sent: false }); },
    closeForm() { if (!this.data.busy) this.setData({ open: false }); },
    changeText(event: { detail: { value?: string } }) { this.setData({ content: event.detail.value || "", error: "" }); },
    async submit() {
      const content = this.data.content.trim();
      if (this.data.busy || !content) return;
      const generation = runtime(this).generation;
      if (!runtime(this).requestId || runtime(this).requestContent !== content) {
        runtime(this).requestId = `response-${Date.now()}-${Math.random().toString(36).slice(2)}`;
        runtime(this).requestContent = content;
      }
      this.setData({ busy: true, error: "" });
      try {
        const target = this.properties.readingToken ? { reading_token: this.properties.readingToken } : { video_code: this.properties.videoCode };
        await submitShareResponse(target, content, runtime(this).requestId);
        if (generation !== runtime(this).generation) return;
        runtime(this).requestId = ""; runtime(this).requestContent = "";
        this.setData({ sent: true, open: false, content: "" });
      } catch (error) {
        if (generation !== runtime(this).generation) return;
        if (error instanceof ApiError && error.statusCode === 401) {
          this.setData({ error: "请先登录，再提交这段回应。文字已保留。" });
          wx.navigateTo({ url: "/pages/login/index?from=response" });
        } else {
          this.setData({ error: error instanceof Error ? error.message : "暂时没能提交，请重试" });
          if (error instanceof ApiError && [404, 409, 410].includes(error.statusCode)) this.triggerEvent("unavailable", { error });
        }
      } finally { if (generation === runtime(this).generation) this.setData({ busy: false }); }
    },
  },
});
