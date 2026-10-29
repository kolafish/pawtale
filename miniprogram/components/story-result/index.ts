import type { HomeGrowthResult } from "../../pages/home/home-story-state";
import { drawStoryBook, ease } from "./book-art";

interface ResultRuntime {
  attached: boolean;
  pageVisible: boolean;
  visible: boolean;
  elapsed: number;
  lastTick: number;
  lastText: number;
  timer: ReturnType<typeof setTimeout> | null;
  context: CanvasRenderingContext2D | null;
  observer: WechatMiniprogram.IntersectionObserver | null;
  width: number;
  height: number;
  dpr: number;
  generation: number;
  resultId: string;
  readingTimes: number[];
  visibleBands: Set<number>;
}
const runtimes = new WeakMap<object, ResultRuntime>();
function runtime(owner: object): ResultRuntime {
  let state = runtimes.get(owner);
  if (!state) {
    state = { attached: false, pageVisible: true, visible: false, elapsed: 0, lastTick: 0, lastText: -100,
      timer: null, context: null, observer: null, width: 0, height: 0, dpr: 1, generation: 0, resultId: "", readingTimes: [], visibleBands: new Set() };
    runtimes.set(owner, state);
  }
  return state;
}

Component({
  options: { addGlobalClass: true },
  properties: { result: { type: Object, value: null }, largeText: { type: Boolean, value: false } },
  data: { rows: [] as (HomeGrowthResult["titles"][number] & { opacity: number; offset: number })[],
    bands: [] as { index: number; top: number; height: number }[], count: 0, opacity: 1, metaOpacity: 0, summary: "" },
  observers: { result() {
    const state = runtime(this), result = this.properties.result as HomeGrowthResult | null;
    if (state.attached && state.resultId !== (result?.id || "")) this.prepare();
  }, largeText() { if (runtime(this).attached) this.prepare(); } },
  lifetimes: {
    ready() { runtime(this).attached = true; this.prepare(); },
    detached() {
      this.pause();
      const state = runtime(this);
      state.attached = false; state.generation += 1; state.observer?.disconnect();
      runtimes.delete(this);
    },
  },
  pageLifetimes: {
    show() { runtime(this).pageVisible = true; this.resume(); },
    hide() { runtime(this).pageVisible = false; this.pause(); },
    resize() { if (runtime(this).attached) this.prepare(); },
  },
  methods: {
    prepare() {
      this.pause();
      const state = runtime(this), result = this.properties.result as HomeGrowthResult | null;
      state.resultId = result?.id || "";
      state.observer?.disconnect(); state.generation += 1;
      state.elapsed = 0; state.visible = false; state.lastText = -100;
      state.context = null; state.readingTimes = []; state.visibleBands.clear();
      if (!result) return;
      const generation = state.generation;
      this.setData({ bands: [], rows: result.titles.map(row => ({ ...row, opacity: 0, offset: 12 })), count: result.wordDelta,
        opacity: 1, metaOpacity: 0, summary: [...result.titles.map(row => `${row.label}：${row.title}`),
          result.wordDelta ? `合计新增 ${result.wordDelta} 字` : "", result.moreCount ? `另有 ${result.moreCount} 处更新` : ""].filter(Boolean).join("，") }, () => {
        if (!state.attached || generation !== state.generation) return;
        const observe = () => {
          this.createSelectorQuery().select(".result-copy").boundingClientRect(rect => {
            if (!state.attached || generation !== state.generation) return;
            const viewportHeight = wx.getWindowInfo().windowHeight;
            // A tall result cannot be fully visible at once. Keep its complete text readable,
            // and require visible dwell for every bounded strip, including the middle.
            const tall = Boolean(rect && rect.height > viewportHeight * .8);
            const bands = tall && rect ? Array.from({ length: Math.ceil(rect.height / Math.min(120, viewportHeight * .4)) }, (_, index) => {
              const height = Math.min(120, viewportHeight * .4);
              return { index, top: index * height, height: Math.min(height, rect.height - index * height) };
            }) : [];
            state.readingTimes = bands.map(() => 0);
            this.setData({ bands, ...(tall ? { rows: result.titles.map(row => ({ ...row, opacity: 1, offset: 0 })),
              count: result.wordDelta, metaOpacity: 1 } : {}) }, () => {
              if (!state.attached || generation !== state.generation) return;
              this.triggerEvent("readingmode", { id: result.id, static: tall });
              state.observer = this.createIntersectionObserver({ thresholds: [0, .99], observeAll: tall });
              state.observer.relativeToViewport().observe(tall ? ".reading-band" : ".result-copy", entry => {
                if (!state.attached || generation !== state.generation) return;
                if (tall) {
                  const index = Number(entry.dataset.index);
                  if (!Number.isInteger(index) || index < 0 || index >= state.readingTimes.length) return;
                  if (entry.intersectionRatio >= .99) state.visibleBands.add(index);
                  else state.visibleBands.delete(index);
                  state.visible = state.visibleBands.size > 0;
                } else state.visible = entry.intersectionRatio >= .99;
                if (state.visible) this.resume(); else this.pause();
              });
            });
          }).exec();
        };
        if (!result.celebrate) { observe(); return; }
        this.createSelectorQuery().select("#result-book").fields({ node: true, size: true }, node => {
          if (!state.attached || generation !== state.generation) return;
          if (node?.node && node.width && node.height) {
            state.context = node.node.getContext("2d");
            state.width = node.width; state.height = node.height; state.dpr = Math.min(wx.getWindowInfo().pixelRatio || 1, 2);
            node.node.width = Math.round(node.width * state.dpr); node.node.height = Math.round(node.height * state.dpr);
            drawStoryBook(state.context, state.width, state.height, state.dpr, 0);
          }
          // Text stays available even if a device cannot initialize the decorative canvas.
          observe();
        }).exec();
      });
    },
    pause() {
      const state = runtime(this);
      if (state.timer !== null) clearTimeout(state.timer);
      state.timer = null; state.lastTick = 0;
    },
    resume() {
      const state = runtime(this);
      if (!state.attached || !state.pageVisible || !state.visible || state.timer !== null || state.elapsed >= 3800) return;
      state.lastTick = Date.now();
      this.tick();
    },
    tick() {
      const state = runtime(this), result = this.properties.result as HomeGrowthResult | null;
      if (!result || !state.attached || !state.pageVisible || !state.visible) return;
      const now = Date.now();
      // Suspended frames cannot count as reading time.
      const delta = Math.min(80, now - state.lastTick); state.lastTick = now;
      if (state.readingTimes.length && state.elapsed === 0) {
        for (const index of state.visibleBands) state.readingTimes[index] = Math.min(900, state.readingTimes[index] + delta);
        if (state.readingTimes.every(time => time >= 900)) state.elapsed = 2950;
      } else state.elapsed = Math.min(3800, state.elapsed + delta);
      const seconds = state.elapsed / 1000;
      if (state.context && !state.readingTimes.length) drawStoryBook(state.context, state.width, state.height, state.dpr, seconds);
      if (state.elapsed - state.lastText >= 50 || state.elapsed === 3800) {
        state.lastText = state.elapsed;
        if (state.readingTimes.length) this.setData({ opacity: state.elapsed ? 1 - ease((seconds - 2.95) / .85) : 1 });
        else {
          const countProgress = ease((seconds - .8) / .6), steps = Math.min(8, result.wordDelta);
          this.setData({ opacity: 1 - ease((seconds - 2.95) / .85), metaOpacity: ease((seconds - .8) / .5),
            rows: result.titles.map((row, index) => {
              const enter = ease((seconds - .48 - index * .2) / .52);
              return { ...row, opacity: enter, offset: result.celebrate ? 12 * (1 - enter) : 0 };
            }), count: result.wordDelta - steps + Math.floor(steps * countProgress) });
        }
      }
      if (state.elapsed >= 3800) {
        state.timer = null; state.observer?.disconnect();
        this.triggerEvent("complete", { id: result.id });
      } else if (state.readingTimes.length && !state.elapsed && [...state.visibleBands].every(index => state.readingTimes[index] >= 900)) {
        // Wait for scrolling to reveal unread text instead of ticking while the reader rests.
        state.timer = null;
      } else state.timer = setTimeout(() => this.tick(), 33);
    },
  },
});
