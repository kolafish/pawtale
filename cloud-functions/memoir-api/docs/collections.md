# Memoir Cloud Collections

All collections store `openid` explicitly. Every query in `memoir-api` filters by `openid`; there is no cross-user global project or reference state in the mini program cloud path.

## `memoir_projects`

| Field | Type | Notes |
| --- | --- | --- |
| `openid` | string | WeChat user identity |
| `type` | string | Always `memoir` |
| `title` | string | Defaults to `我的回忆录` |
| `description` | string | Optional display copy |
| `total_word_count` | number | Reserved for confirmed drafts |
| `created_at` | string | ISO timestamp |
| `updated_at` | string | ISO timestamp |

Recommended index: `openid,type,updated_at`.

## `memoir_sessions`

| Field | Type | Notes |
| --- | --- | --- |
| `openid` | string | Owner |
| `project_id` | string | `memoir_projects` id |
| `title` | string | Defaults to `回忆录访谈` |
| `status` | string | `active` / `completed` |
| `current_question_id` | string/null | Current pending question |
| `created_at` | string | ISO timestamp |
| `updated_at` | string | ISO timestamp |

Recommended index: `openid,project_id,status,updated_at`.

## `memoir_questions`

| Field | Type | Notes |
| --- | --- | --- |
| `openid` | string | Owner |
| `session_id` | string | `memoir_sessions` id |
| `prompt` | string | Interview question |
| `source` | string | `template` / `ai_generated` |
| `sort_order` | number | Stable interview order |
| `status` | string | `pending` / `answered` / `skipped` |
| `created_at` | string | ISO timestamp |

Recommended index: `openid,session_id,sort_order`.

## `memoir_answers`

| Field | Type | Notes |
| --- | --- | --- |
| `openid` | string | Owner |
| `question_id` | string | `memoir_questions` id |
| `audio_file_id` | string/null | Optional cloud audio record |
| `transcript_raw` | string | ASR/raw text |
| `transcript_edited` | string | Corrected user answer |
| `status` | string | `uploaded` / `transcribing` / `answered` / `drafted` / `confirmed` |
| `duration_seconds` | number/null | Optional audio duration |
| `created_at` | string | ISO timestamp |
| `updated_at` | string | ISO timestamp |

Recommended index: `openid,question_id,updated_at`.

## `memoir_drafts`

| Field | Type | Notes |
| --- | --- | --- |
| `openid` | string | Owner |
| `answer_id` | string | `memoir_answers` id |
| `content_raw` | string | AI result |
| `content_edited` | string | User-editable result |
| `status` | string | `draft` / `accepted` |
| `created_at` | string | ISO timestamp |
| `updated_at` | string | ISO timestamp |

Recommended index: `openid,answer_id,updated_at`.

## `memoir_audio_files`

| Field | Type | Notes |
| --- | --- | --- |
| `openid` | string | Owner |
| `file_id` | string | Cloud storage file id |
| `cloud_path` | string | Cloud storage path |
| `mime_type` | string | Uploaded audio mime type |
| `size_bytes` | number | Uploaded audio size |
| `duration_seconds` | number/null | Optional duration |
| `transcript_text` | string | Last ASR result |
| `transcript_usage_json` | string | JSON-encoded ASR usage payload |
| `transcribed_at` | string/null | Last ASR timestamp |
| `created_at` | string | ISO timestamp |

Recommended index: `openid,created_at`.

`transcript_usage_json` is only for diagnostics and should not be queried or indexed. Keep indexes on ownership and workflow fields instead:

- `memoir_projects`: `openid,type,updated_at`
- `memoir_sessions`: `openid,project_id,status,updated_at`
- `memoir_questions`: `openid,session_id,sort_order`
- `memoir_answers`: `openid,question_id,updated_at`
- `memoir_drafts`: `openid,answer_id,updated_at`
- `memoir_audio_files`: `openid,created_at`
