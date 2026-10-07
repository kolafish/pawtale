import type { MemoirShareVideoJob } from "../domain/memoir-transcript";

// One wording for deleting from the video page and from the library.
export function shareVideoDeleteConfirmation(job: Pick<MemoirShareVideoJob, "status" | "title">): {
  title: string;
  content: string;
  confirmText: string;
} {
  if (job.status === "failed") {
    return {
      title: "删除这次失败的记录？",
      content: "只删除这条制作记录，原始讲述和图片素材会保留，之后还可以重新制作。",
      confirmText: "删除",
    };
  }
  return {
    title: "删除这版视频？",
    content: `《${job.title}》删除后，转发出去的卡片也会打不开。其他版本、原始讲述和图片素材会保留。`,
    confirmText: "删除视频",
  };
}
