type WxWithNavigationBarTitle = typeof wx & {
  setNavigationBarTitle?: (options: { title: string }) => void;
};

export function scrollPageTo(options: Parameters<typeof wx.pageScrollTo>[0]): void {
  if (typeof wx === "undefined" || typeof wx.pageScrollTo !== "function") return;
  const pages = typeof getCurrentPages === "function" ? getCurrentPages() : [];
  const navigationHeight = pages[pages.length - 1]?.data?.navigationHeight || 0;
  wx.pageScrollTo({
    ...options,
    // Explicit offsets (e.g. a measured sticky player) already include the
    // navigation. Absolute scrollTop values are saved document coordinates.
    ...(options.selector && options.offsetTop === undefined && navigationHeight > 0
      ? { offsetTop: -navigationHeight }
      : {}),
  });
}

export function setPageNavigationTitle(title: string): void {
  // Page data also covers titles set during onLoad, before the component attaches.
  if (typeof getCurrentPages === "function") {
    const pages = getCurrentPages();
    pages[pages.length - 1]?.setData?.({ navigationTitle: title });
  }
  if (typeof wx === "undefined") {
    return;
  }
  const wxRuntime = wx as WxWithNavigationBarTitle;
  if (typeof wxRuntime.setNavigationBarTitle !== "function") {
    return;
  }
  wxRuntime.setNavigationBarTitle({ title });
}
