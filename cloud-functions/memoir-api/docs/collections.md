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
| `audio_file_id` | string/null | Optional `memoir_audio_files` id |
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
| `created_at` | string | ISO timestamp |

Recommended index: `openid,created_at`.

Cloud audio registrations must use `memoir/audio/{storage_owner_id}/...` paths, where
`storage_owner_id` is returned by `GET /api/mobile/memoir/me`. The cloud API rejects
paths outside the current user's directory, unsupported extensions, MIME/extension
mismatches, empty files, and files over 20MB.

Keep indexes on ownership and workflow fields instead:

- `memoir_projects`: `openid,type,updated_at`
- `memoir_sessions`: `openid,project_id,status,updated_at`
- `memoir_questions`: `openid,session_id,sort_order`
- `memoir_answers`: `openid,question_id,updated_at`
- `memoir_drafts`: `openid,answer_id,updated_at`
- `memoir_audio_files`: `openid,created_at`

## `memoir_tts_assets`

| Field | Type | Notes |
| --- | --- | --- |
| `openid` | string | Owner |
| `project_id` | string | `memoir_projects` id |
| `source_type` | string | `question` / `open_question` / `answer` / `draft` / `output` / `output_version` / `session_summary` |
| `source_id` | string | Source row id |
| `source_version` | string | Source version or timestamp used for cache invalidation |
| `text_hash` | string | SHA-256 of normalized readable text |
| `text_preview` | string | Short readable preview |
| `text_length` | number | Normalized text length |
| `file_id` | string | Generated cloud storage file id |
| `cloud_path` | string | Generated cloud storage path under `memoir/tts/{storage_owner_id}/...` |
| `mime_type` | string | Generated audio MIME type |
| `size_bytes` | number | Generated audio size |
| `model` | string | TTS model used |
| `voice` | string | TTS voice used |
| `format` | string | Audio format, currently `mp3` |
| `synthesis_speed` | number | Provider synthesis speed; frontend playback speed is separate |
| `chunk_count` | number | Number of TTS chunks used |
| `created_at` | string | ISO timestamp |
| `updated_at` | string | ISO timestamp |
| `generated_at` | string | ISO timestamp |

Recommended index: `openid,project_id,source_type,source_id,text_hash,updated_at`.

## `memoir_tts_jobs`

| Field | Type | Notes |
| --- | --- | --- |
| `openid` | string | Owner |
| `project_id` | string | `memoir_projects` id |
| `source_type` | string | Same source types as `memoir_tts_assets` |
| `source_id` | string | Source row id |
| `source_version` | string | Source version or timestamp captured when queued |
| `text_hash` | string | SHA-256 of normalized readable text captured when queued |
| `text_preview` | string | Short readable preview |
| `text_length` | number | Normalized text length |
| `tts_asset_id` | string/null | Completed `memoir_tts_assets` id |
| `status` | string | `queued` / `running` / `completed` / `failed` |
| `model` | string | TTS model |
| `voice` | string | TTS voice |
| `format` | string | Audio format, currently `mp3` |
| `synthesis_speed` | number | Provider synthesis speed |
| `chunk_count` | number | Number of chunks generated |
| `error_message` | string | Failure reason |
| `created_at` | string | ISO timestamp |
| `updated_at` | string | ISO timestamp |
| `started_at` | string/null | ISO timestamp |
| `completed_at` | string/null | ISO timestamp |

Recommended index: `openid,project_id,status,updated_at`; for cache lookup also use `openid,project_id,source_type,source_id,text_hash`.
