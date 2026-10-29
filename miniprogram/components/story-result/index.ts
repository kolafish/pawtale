import type { HomeGrowthResult } from "../../pages/home/home-story-state";
import { AudioPlaybackController } from "../../services/audio-playback";
import { drawStoryBook, ease } from "./book-art";

interface ResultRuntime {
  attached: boolean;
  pageVisible: boolean;
  visible: boolean;
  elapsed: number;
  lastTick: number;
  lastText: number;
  rendering: boolean;
  completed: boolean;
  soundPlayed: boolean;
  audio: AudioPlaybackController;
  timer: ReturnType<typeof setTimeout> | null;
  context: CanvasRenderingContext2D | null;
  observer: WechatMiniprogram.IntersectionObserver | null;
  width: number;
  height: number;
  dpr: number;
  generation: number;
  resultId: string;
}
const runtimes = new WeakMap<object, ResultRuntime>();
function runtime(owner: object): ResultRuntime {
  let state = runtimes.get(owner);
  if (!state) {
    state = { attached: false, pageVisible: true, visible: false, elapsed: 0, lastTick: 0, lastText: -100,
      rendering: false, completed: false, soundPlayed: false,
      audio: new AudioPlaybackController({ configureAudio(audio) {
        wx.setInnerAudioOption?.({ obeyMuteSwitch: true, mixWithOther: true });
        audio.obeyMuteSwitch = true; audio.volume = .22;
      } }),
      timer: null, context: null, observer: null, width: 0, height: 0, dpr: 1, generation: 0, resultId: "" };
    runtimes.set(owner, state);
  }
  return state;
}

Component({
  options: { addGlobalClass: true },
  properties: { result: { type: Object, value: null }, largeText: { type: Boolean, value: false } },
  data: { title: "", count: 0, opacity: 0, textOpacity: 0, textOffset: 8, summary: "", structureLabel: "" },
  observers: { result() {
    const state = runtime(this), result = this.properties.result as HomeGrowthResult | null;
    if (state.attached && state.resultId !== (result?.id || "")) this.prepare();
  } },
  lifetimes: {
    ready() { runtime(this).attached = true; this.prepare(); },
    detached() {
      this.pause();
      const state = runtime(this);
      state.attached = false; state.generation += 1; state.observer?.disconnect(); state.audio.destroy();
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
      const soundPlayed = state.resultId === result?.id && state.soundPlayed;
      state.resultId = result?.id || "";
      state.observer?.disconnect(); state.generation += 1;
      state.elapsed = 0; state.visible = false; state.lastText = -100;
      state.context = null; state.completed = false; state.rendering = false; state.soundPlayed = soundPlayed;
      if (!result) return;
      const generation = state.generation;
      const mainTitle = result.titles.find(row => row.label === "新增小节") || result.titles[0];
      const characters = Array.from(mainTitle?.title || "回忆录");
      // The overlay is a short receipt; complete titles and content remain in the book.
      this.setData({ title: characters.length > 14 ? `${characters.slice(0, 13).join("")}…` : characters.join(""),
        structureLabel: mainTitle?.label || "已收录", count: result.wordDelta,
        opacity: 0, textOpacity: 0, textOffset: 8,
        summary: [...result.titles.map(row => `${row.label}：${row.title}`),
          result.wordDelta ? `本次新增 ${result.wordDelta} 字` : "", result.moreCount ? `另有 ${result.moreCount} 处更新` : ""].filter(Boolean).join("，") }, () => {
        if (!state.attached || generation !== state.generation) return;
        const observe = () => {
          state.observer = this.createIntersectionObserver({ thresholds: [0, .99] });
          // Observe the whole overlay, including the book, rather than only its text.
          state.observer.relativeToViewport().observe(".result-scene", entry => {
            if (!state.attached || generation !== state.generation) return;
            state.visible = entry.intersectionRatio >= .99;
            if (state.visible) this.resume(); else this.pause();
          });
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
          observe();
        }).exec();
      });
    },
    pause() {
      const state = runtime(this);
      if (state.timer !== null) clearTimeout(state.timer);
      state.timer = null; state.lastTick = 0; state.audio.stop();
    },
    resume() {
      const state = runtime(this);
      if (!state.attached || !state.pageVisible || !state.visible || state.timer !== null || state.rendering || state.completed) return;
      state.lastTick = Date.now(); this.tick();
    },
    finishFrame() {
      const state = runtime(this), result = this.properties.result as HomeGrowthResult | null;
      if (!result || !state.attached || !state.pageVisible || !state.visible || state.completed) return;
      if (state.elapsed >= (result.celebrate ? 6000 : 3200)) {
        state.completed = true; state.observer?.disconnect(); state.audio.stop();
        this.triggerEvent("complete", { id: result.id });
      } else state.timer = setTimeout(() => this.tick(), 33);
    },
    tick() {
      const state = runtime(this), result = this.properties.result as HomeGrowthResult | null;
      state.timer = null;
      if (!result || !state.attached || !state.pageVisible || !state.visible || state.completed) return;
      const duration = result.celebrate ? 6000 : 3200, now = Date.now();
      state.elapsed = Math.min(duration, state.elapsed + Math.min(80, now - state.lastTick)); state.lastTick = now;
      const seconds = state.elapsed / 1000;
      const artSeconds = seconds < 1 ? seconds : seconds < 4.4 ? 1 + (seconds - 1) * 1.25 / 3.4 : 2.25 + (seconds - 4.4) * 1.55 / 1.6;
      if (state.context) drawStoryBook(state.context, state.width, state.height, state.dpr, artSeconds);
      if (result.celebrate && !state.soundPlayed && state.elapsed >= 400) {
        state.soundPlayed = true;
        void state.audio.playTempFile("/assets/audio/story-collected.wav").catch(error => console.warn("story result sound unavailable", error));
      }
      if (state.elapsed - state.lastText >= 50 || state.elapsed === duration) {
        state.lastText = state.elapsed; state.rendering = true;
        const generation = state.generation, steps = Math.min(8, result.wordDelta);
        this.setData({ opacity: ease(seconds / .6) * (1 - ease((state.elapsed - duration + 900) / 900)),
          textOpacity: ease((seconds - .3) / .6), textOffset: 8 * (1 - ease((seconds - .3) / .6)),
          count: result.wordDelta - steps + Math.floor(steps * ease((seconds - .8) / .8)) }, () => {
          if (!state.attached || generation !== state.generation) return;
          state.rendering = false;
          // Bridge/render delays are not reading time; never acknowledge before the final render.
          state.lastTick = Date.now(); this.finishFrame();
        });
      } else this.finishFrame();
    },
  },
});
