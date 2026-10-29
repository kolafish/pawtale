// Ma Shan Zheng, SIL Open Font License; see OFL-MaShanZheng.txt.
// Preload the complete font, never send private chapter titles to a font-subsetting service.
let requested = false;
let loaded = false;

export function preloadStoryResultFont(): void {
  if (requested || !wx.loadFontFace) return;
  requested = true;
  wx.loadFontFace({
    global: true,
    family: "StoryCalligraphy",
    source: 'url("https://fonts.gstatic.cn/s/mashanzheng/v18/NaPecZTRCLxvwo41b4gvzkXaRMQ.ttf")',
    success: () => { loaded = true; },
    // A decorative font must never hold up a saved-story receipt.
    fail: () => {},
  });
}

export function storyResultFontReady(): boolean { return loaded; }
