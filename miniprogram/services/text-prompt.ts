export function promptRequiredText(
  title: string,
  placeholderText: string,
): Promise<string | null> {
  return new Promise((resolve) => {
    wx.showModal({
      title,
      editable: true,
      placeholderText,
      confirmText: "确定",
      success: (result) => {
        if (!result.confirm) {
          resolve(null);
          return;
        }
        const value = (result.content || "").trim();
        if (!value) {
          wx.showToast({ title: "名称不能为空", icon: "none" });
          resolve(null);
          return;
        }
        resolve(value);
      },
      fail: () => resolve(null),
    });
  });
}
