import type { MemoirMaterialOfflineASRState } from "../domain/voice-chat";


export interface MaterialOfflineASRView {
  statusText: string;
  statusClass: string;
  actionText: string;
  methodText: string;
  detailText: string;
  errorText: string;
  active: boolean;
  canStart: boolean;
}

function dialectText(state: MemoirMaterialOfflineASRState): string {
  const origin = state.narrator_origin.trim();
  if (state.dialect_enhancement_enabled && state.dialect_label) {
    return [origin ? `籍贯 ${origin}` : "", `方言 ${state.dialect_label}`].filter(Boolean).join(" · ");
  }
  if (origin) {
    return `籍贯 ${origin} · 通用口音校对`;
  }
  return "未填写籍贯 · 通用口音校对";
}

export function materialOfflineASRView(state: MemoirMaterialOfflineASRState): MaterialOfflineASRView {
  const active = state.status === "pending" || state.status === "running";
  const statusByValue: Record<MemoirMaterialOfflineASRState["status"], string> = {
    idle: "未处理",
    pending: "等待处理",
    running: "识别中",
    succeeded: "已加强",
    failed: "处理失败",
  };
  const actionByValue: Record<MemoirMaterialOfflineASRState["status"], string> = {
    idle: "离线加强识别",
    pending: "等待处理",
    running: "识别中",
    succeeded: "再次识别",
    failed: "重新识别",
  };
  return {
    statusText: statusByValue[state.status],
    statusClass: state.status,
    actionText: actionByValue[state.status],
    methodText: `火山 + StepFun · ${dialectText(state)}`,
    detailText: active
      ? "任务在后台运行，完成后会自动更新这条素材。"
      : (state.blocked_reason || (state.status === "succeeded"
        ? "增强结果已用于当前素材。"
        : "只有点击开始后才会处理当前素材。")),
    errorText: state.error_message,
    active,
    canStart: state.can_start && !active,
  };
}
