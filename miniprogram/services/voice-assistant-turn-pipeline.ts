import type {
  MemoirVoiceFollowUpResponse,
  MemoirVoiceTurnPlanResponse,
} from "../domain/voice-chat";
import {
  createMemoirVoiceFollowUp,
  createMemoirVoiceTurnPlan,
} from "./memoir-voice-api";
import {
  assistantTurnQueueItem,
  type AssistantTurnQueueItem,
} from "./voice-assistant-turn-queue";

export type AssistantTurnPipelineStage = "acknowledgement" | "question";

const ACKNOWLEDGEMENT_BRIDGE_WAIT_MS = 900;

export interface AssistantTurnPipelineStep {
  stage: AssistantTurnPipelineStage;
  response: MemoirVoiceFollowUpResponse;
  item: AssistantTurnQueueItem;
}

export interface AssistantTurnPipelineResult {
  cancelled: boolean;
  turnPlan?: MemoirVoiceTurnPlanResponse;
  acknowledgement?: MemoirVoiceFollowUpResponse;
  followUp?: MemoirVoiceFollowUpResponse;
}

export interface AssistantTurnPipelineRequest {
  voiceSessionId: string;
  isCancelled?: () => boolean;
  onStep: (step: AssistantTurnPipelineStep) => void;
  createTurnPlan?: typeof createMemoirVoiceTurnPlan;
  createFollowUp?: typeof createMemoirVoiceFollowUp;
}

function cancelled(isCancelled?: () => boolean): boolean {
  return isCancelled ? isCancelled() : false;
}

export async function runAcknowledgedQuestionPipeline(
  request: AssistantTurnPipelineRequest,
): Promise<AssistantTurnPipelineResult> {
  const createTurnPlan = request.createTurnPlan || createMemoirVoiceTurnPlan;
  const createFollowUp = request.createFollowUp || createMemoirVoiceFollowUp;

  const turnPlan = await createTurnPlan(request.voiceSessionId, {});
  if (cancelled(request.isCancelled)) {
    return { cancelled: true };
  }
  const acknowledgement = turnPlan.plan.should_play_ack !== false
    ? turnPlan.acknowledgement || undefined
    : undefined;
  if (acknowledgement) {
    request.onStep({
      stage: "acknowledgement",
      response: acknowledgement,
      item: assistantTurnQueueItem(acknowledgement, {
        autoListenAfterPlayback: false,
        waitForNextTurnBeforePlaybackMs: ACKNOWLEDGEMENT_BRIDGE_WAIT_MS,
      }),
    });
  }
  if (cancelled(request.isCancelled)) {
    return { cancelled: true, turnPlan, acknowledgement };
  }

  const followUpRequest = {
    turn_plan: turnPlan.plan,
    ...(acknowledgement?.message.id ? { acknowledgement_message_id: acknowledgement.message.id } : {}),
  };
  const followUp = await createFollowUp(request.voiceSessionId, followUpRequest);
  if (cancelled(request.isCancelled)) {
    return { cancelled: true, turnPlan, acknowledgement };
  }
  request.onStep({
    stage: "question",
    response: followUp,
    item: assistantTurnQueueItem(followUp, {
      autoListenAfterPlayback: followUp.expects_answer !== false,
    }),
  });

  return { cancelled: false, turnPlan, acknowledgement, followUp };
}
