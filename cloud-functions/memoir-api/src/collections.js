const COLLECTIONS = Object.freeze({
  projects: "memoir_projects",
  sessions: "memoir_sessions",
  questions: "memoir_questions",
  answers: "memoir_answers",
  drafts: "memoir_drafts",
  audioFiles: "memoir_audio_files",
});

const COLLECTION_SCHEMA = Object.freeze({
  memoir_projects: [
    "openid",
    "type",
    "title",
    "description",
    "total_word_count",
    "created_at",
    "updated_at",
  ],
  memoir_sessions: [
    "openid",
    "project_id",
    "title",
    "status",
    "current_question_id",
    "created_at",
    "updated_at",
  ],
  memoir_questions: [
    "openid",
    "session_id",
    "prompt",
    "source",
    "sort_order",
    "status",
    "created_at",
  ],
  memoir_answers: [
    "openid",
    "question_id",
    "audio_file_id",
    "transcript_raw",
    "transcript_edited",
    "status",
    "duration_seconds",
    "created_at",
    "updated_at",
  ],
  memoir_drafts: [
    "openid",
    "answer_id",
    "content_raw",
    "content_edited",
    "status",
    "created_at",
    "updated_at",
  ],
  memoir_audio_files: [
    "openid",
    "file_id",
    "cloud_path",
    "mime_type",
    "size_bytes",
    "duration_seconds",
    "created_at",
  ],
});

module.exports = {
  COLLECTIONS,
  COLLECTION_SCHEMA,
};
