type WxWithNavigationBarTitle = typeof wx & {
  setNavigationBarTitle?: (options: { title: string }) => void;
};

export function setPageNavigationTitle(title: string): void {
  if (typeof wx === "undefined") {
    return;
  }
  const wxRuntime = wx as WxWithNavigationBarTitle;
  if (typeof wxRuntime.setNavigationBarTitle !== "function") {
    return;
  }
  wxRuntime.setNavigationBarTitle({ title });
}
