type WxWithNavigationBarTitle = typeof wx & {
  setNavigationBarTitle?: (options: { title: string }) => void;
};

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
