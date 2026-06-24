import type { MemoirVoiceFollowUpResponse } from "../domain/voice-chat";
import {
  createMemoirVoiceAcknowledgement,
  createMemoirVoiceFollowUp,
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
  acknowledgement?: MemoirVoiceFollowUpResponse;
  followUp?: MemoirVoiceFollowUpResponse;
}

export interface AssistantTurnPipelineRequest {
  voiceSessionId: string;
  isCancelled?: () => boolean;
  onStep: (step: AssistantTurnPipelineStep) => void;
  createAcknowledgement?: typeof createMemoirVoiceAcknowledgement;
  createFollowUp?: typeof createMemoirVoiceFollowUp;
}

function cancelled(isCancelled?: () => boolean): boolean {
  return isCancelled ? isCancelled() : false;
}

export async function runAcknowledgedQuestionPipeline(
  request: AssistantTurnPipelineRequest,
): Promise<AssistantTurnPipelineResult> {
  const createAcknowledgement = request.createAcknowledgement || createMemoirVoiceAcknowledgement;
  const createFollowUp = request.createFollowUp || createMemoirVoiceFollowUp;

  const acknowledgement = await createAcknowledgement(request.voiceSessionId, {});
  if (cancelled(request.isCancelled)) {
    return { cancelled: true };
  }
  request.onStep({
    stage: "acknowledgement",
    response: acknowledgement,
    item: assistantTurnQueueItem(acknowledgement, {
      autoListenAfterPlayback: false,
      waitForNextTurnBeforePlaybackMs: ACKNOWLEDGEMENT_BRIDGE_WAIT_MS,
    }),
  });
  if (cancelled(request.isCancelled)) {
    return { cancelled: true, acknowledgement };
  }

  const followUp = await createFollowUp(request.voiceSessionId, {
    acknowledgement_message_id: acknowledgement.message.id,
  });
  if (cancelled(request.isCancelled)) {
    return { cancelled: true, acknowledgement };
  }
  request.onStep({
    stage: "question",
    response: followUp,
    item: assistantTurnQueueItem(followUp, {
      autoListenAfterPlayback: followUp.expects_answer !== false,
    }),
  });

  return { cancelled: false, acknowledgement, followUp };
}
