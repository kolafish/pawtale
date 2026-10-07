export function confirmDeleteTranscript(previewText: string): Promise<boolean> {
  return new Promise((resolve) => {
    const preview = previewText || "这条讲述记录";
    wx.showModal({
      title: "删除讲述记录",
      content: `确定删除“${preview.slice(0, 24)}${preview.length > 24 ? "..." : ""}”？删除后，这条原始讲述会从素材库移除；如果已放入章节，相关章节会标记为需重新确认，已生成正文不会自动改写。`,
      confirmText: "删除",
      confirmColor: "#b64545",
      success: (result) => resolve(Boolean(result.confirm)),
      fail: () => resolve(false),
    });
  });
}
