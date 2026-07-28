export interface TTSAlignmentSegment {
  text: string;
  start_ms: number;
  end_ms: number;
  text_start: number;
  text_end: number;
  confidence?: number | null;
}
