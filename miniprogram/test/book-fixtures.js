const CREATED_AT = "2026-06-17T00:00:00.000Z";

function sampleRef(id = "utterance-1", quote = "外婆家的院子") {
  return {
    voice_session_id: "voice-1",
    message_id: `message-${id}`,
    utterance_id: id,
    transcript_version_id: `version-${id}`,
    audio_file_id: `audio-${id}`,
    start_ms: 0,
    end_ms: 8000,
    created_at: CREATED_AT,
    quote,
  };
}

function samplePlan(status = "draft", { chapterCount = 2 } = {}) {
  const chapters = [
    {
      id: "chapter-1",
      plan_id: "plan-1",
      project_id: "project/1",
      title: "外婆家的童年",
      sort_order: 0,
      include_in_book: true,
      source_refs: [sampleRef("utterance-1", "外婆家的院子")],
      sections: [
        {
          id: "section-1",
          title: "院子里的日常",
          section_type: "life_fragment",
          sort_order: 0,
          status: "active",
          include_in_book: true,
          version: 1,
          material_count: 1,
          included_material_count: 1,
          materials: [
            {
              utterance_id: "utterance-1",
              sort_order: 0,
              text: "我小时候常在外婆家的院子里晒太阳。",
              include_in_book: true,
            },
          ],
        },
      ],
      metadata_json: {},
      pending_source_count: 0,
      pending_completion_source_count: 0,
      pending_new_source_count: 0,
      removed_source_count: 0,
      created_at: CREATED_AT,
      updated_at: CREATED_AT,
    },
    {
      id: "chapter-2",
      plan_id: "plan-1",
      project_id: "project/1",
      title: "第一次离家",
      sort_order: 1,
      include_in_book: true,
      source_refs: [sampleRef("utterance-2", "第一次离家")],
      sections: [
        {
          id: "section-2",
          title: "第一次离开家",
          section_type: "event",
          sort_order: 0,
          status: "active",
          include_in_book: true,
          version: 1,
          material_count: 1,
          included_material_count: 1,
          materials: [
            {
              utterance_id: "utterance-2",
              sort_order: 0,
              text: "后来我第一次离家去县城读书。",
              include_in_book: true,
            },
          ],
        },
      ],
      metadata_json: {},
      pending_source_count: 0,
      pending_completion_source_count: 0,
      pending_new_source_count: 0,
      removed_source_count: 0,
      created_at: CREATED_AT,
      updated_at: CREATED_AT,
    },
  ].slice(0, chapterCount);

  return {
    id: "plan-1",
    project_id: "project/1",
    title: "外婆回忆录章节规划",
    status,
    created_by: "ai",
    model: "fake",
    metadata_json: {},
    created_at: CREATED_AT,
    updated_at: CREATED_AT,
    chapters,
  };
}

function sampleDraft(
  version = 1,
  status = "draft",
  content = "我小时候常在外婆家的院子里晒太阳。",
  metadata = {},
) {
  return {
    id: "draft-1",
    project_id: "project/1",
    chapter_id: "chapter-1",
    status,
    metadata_json: metadata,
    pending_source_count: 0,
    removed_source_count: 0,
    latest_version: {
      id: `draft-version-${version}`,
      draft_id: "draft-1",
      project_id: "project/1",
      chapter_id: "chapter-1",
      version,
      content,
      created_by: "ai",
      model: "fake",
      prompt_snapshot: "prompt",
      feedback: null,
      created_at: CREATED_AT,
    },
    versions: [],
    created_at: CREATED_AT,
    updated_at: CREATED_AT,
  };
}

function sampleDraftWithContent(content, status = "draft", version = 2) {
  return sampleDraft(version, status, content);
}

function sampleAutomationEvent(overrides = {}) {
  const eventType = overrides.event_type || "chapter_draft_generated";
  return {
    id: overrides.id || `event-${eventType}`,
    project_id: overrides.project_id || "project/1",
    event_type: eventType,
    status: overrides.status || "pending",
    chapter_id: Object.prototype.hasOwnProperty.call(overrides, "chapter_id") ? overrides.chapter_id : "chapter-1",
    utterance_ids: overrides.utterance_ids || [],
    summary_text: overrides.summary_text || "「外婆家的童年」已提交后台生成。",
    confidence: Object.prototype.hasOwnProperty.call(overrides, "confidence") ? overrides.confidence : null,
    metadata_json: overrides.metadata_json || {},
    created_at: overrides.created_at || CREATED_AT,
    updated_at: overrides.updated_at || CREATED_AT,
  };
}

function sampleStructuredBookContent({ chapterCount = 2, firstChapterSourceCount = chapterCount === 1 ? 1 : 2 } = {}) {
  const chapterItems = [
    {
      chapter_id: "chapter-1",
      index: 1,
      title: "外婆家的童年",
      content: "我小时候常在院子里晒太阳。",
      char_count: 14,
      source_count: firstChapterSourceCount,
      draft_version_id: "draft-version-1",
      draft_version: 1,
      sections: [
        {
          section_id: "section-1",
          index: 1,
          title: "院子里的日子",
          section_type: "life_fragment",
          content: "我小时候常在院子里晒太阳。",
          char_count: 14,
          source_count: firstChapterSourceCount,
        },
        {
          section_id: "section-2",
          index: 2,
          title: "外婆的身影",
          section_type: "person",
          content: "外婆总在屋里屋外忙碌。",
          char_count: 12,
          source_count: 1,
        },
      ],
    },
    {
      chapter_id: "chapter-2",
      index: 2,
      title: "第一次离家",
      content: "第一次离家去县城读书。",
      char_count: 10,
      source_count: 1,
      draft_version_id: "draft-version-2",
      draft_version: 1,
      sections: [
        {
          section_id: "section-3",
          index: 1,
          title: "去县城读书",
          section_type: "event",
          content: "第一次离家去县城读书。",
          char_count: 10,
          source_count: 1,
        },
      ],
    },
  ].slice(0, chapterCount);
  const tableOfContents = chapterItems.map(({
    content,
    draft_version_id,
    draft_version,
    sections,
    ...item
  }) => ({
    ...item,
    sections: sections.map((section) => ({
      section_id: section.section_id,
      index: section.index,
      title: section.title,
      char_count: section.char_count,
      source_count: section.source_count,
    })),
  }));

  return {
    title: "外婆回忆录",
    chapter_count: chapterItems.length,
    char_count: chapterItems.reduce((total, chapter) => total + chapter.char_count, 0),
    table_of_contents: tableOfContents,
    chapters: chapterItems,
  };
}

function sampleBookManuscript(title = "外婆回忆录", coverStyle = "classic_plain", { chapterCount = 2 } = {}) {
  const singleChapter = chapterCount === 1;
  return {
    schema_version: 2,
    profile: {
      template: "classic_family",
      title,
      subtitle: "根据口述访谈整理",
      narrator_name: singleChapter ? "外婆" : "",
      prepared_by: "家人整理",
      interview_date_range: "",
      privacy_note: "仅供家庭内部阅读。",
      ai_disclosure_note: "AI 辅助整理，不新增未经确认的事实。",
    },
    front_matter: singleChapter
      ? [
        { kind: "cover", title, content: `${title}\n根据口述访谈整理`, enabled: true },
        { kind: "preface", title: "前言", content: "这些故事来自家里的讲述。", enabled: true },
      ]
      : [
        { kind: "cover", title, content: `${title}\n根据口述访谈整理`, enabled: true },
        { kind: "title_page", title: "扉页", content: "口述：外婆\n整理：家人", enabled: true },
        { kind: "generation_note", title: "生成说明", content: "本书根据口述访谈整理。", enabled: true },
        { kind: "preface", title: "前言", content: "这些故事来自家里的讲述。", enabled: true },
      ],
    table_of_contents: sampleStructuredBookContent({ chapterCount }).table_of_contents,
    chapters: [
      {
        chapter_id: "chapter-1",
        index: 1,
        title: "外婆家的童年",
        subtitle: "",
        intro: "这一章主要记录外婆小时候在院子里的生活。",
        time_hint: "童年",
        place_hint: "老家",
        pull_quotes: [{ text: "我小时候常在院子里晒太阳。", source_ref_ids: ["utterance-1"] }],
        people_notes: [],
        uncertain_facts: [],
        content: "我小时候常在院子里晒太阳。",
        sections: sampleStructuredBookContent({ chapterCount: 1 }).chapters[0].sections,
        source_refs: [{ utterance_id: "utterance-1", transcript_version_id: "version-1", voice_session_id: "voice-1", quote: "院子" }],
      },
      {
        chapter_id: "chapter-2",
        index: 2,
        title: "第一次离家",
        subtitle: "",
        intro: "这一章主要记录第一次离家的经历。",
        time_hint: "",
        place_hint: "县城",
        pull_quotes: [],
        people_notes: [],
        uncertain_facts: [],
        content: "第一次离家去县城读书。",
        sections: sampleStructuredBookContent({ chapterCount: 2 }).chapters[1].sections,
        source_refs: [],
      },
    ].slice(0, chapterCount),
    back_matter: singleChapter
      ? {
        afterword: { kind: "afterword", title: "后记", content: "这些故事还可以继续补充。", enabled: true },
        version_note: { kind: "version_note", title: "整理说明", content: "默认展示故事书版。", enabled: true },
        source_appendix: { kind: "source_appendix", title: "原始讲述索引", content: "第1章：1 条讲述来源", enabled: true },
      }
      : {
        afterword: { kind: "afterword", title: "后记", content: "这些故事还可以继续补充。", enabled: true },
        interview_records: { kind: "interview_records", title: "访谈记录", content: "当前收录两章。", enabled: true },
        version_note: { kind: "version_note", title: "版本说明", content: "默认展示故事书版。", enabled: true },
        unconfirmed_facts: { kind: "unconfirmed_facts", title: "未确认信息", content: "暂无。", enabled: true },
        source_appendix: { kind: "source_appendix", title: "章节来源", content: "第1章：1 条讲述来源", enabled: true },
      },
    render_settings: {
      page_size: "a5",
      layout: "reading",
      font_scale: "standard",
      show_headers: true,
      show_source_appendix: true,
      photo_mode: "none",
      cover_style: coverStyle,
      back_cover_quote: "我小时候常在院子里晒太阳。",
    },
  };
}

function sampleBookOutputVersion(
  version,
  content,
  structuredContent = null,
  createdAt = CREATED_AT,
  manuscript = null,
) {
  return {
    id: `book-output-version-${version}`,
    output_id: "book-output-1",
    project_id: "project/1",
    plan_id: "plan-1",
    version,
    content,
    chapter_snapshot_json: [],
    structured_content: structuredContent,
    manuscript,
    created_by: "system",
    created_at: createdAt,
  };
}

function sampleBookOutput(
  version = 1,
  content = "外婆回忆录\n\n目录\n1. 外婆家的童年\n\n第1章 外婆家的童年\n\n正文",
  isStale = false,
  staleChapterTitles = [],
  structuredContent = null,
  versions = [],
  manuscript = null,
) {
  const latestVersion = sampleBookOutputVersion(version, content, structuredContent, CREATED_AT, manuscript);
  return {
    id: "book-output-1",
    project_id: "project/1",
    plan_id: "plan-1",
    title: "外婆回忆录",
    status: "draft",
    metadata_json: {},
    is_stale: isStale,
    stale_chapter_titles: staleChapterTitles,
    latest_version: latestVersion,
    versions,
    created_at: CREATED_AT,
    updated_at: CREATED_AT,
  };
}

function sampleAudioResponse() {
  return {
    audio_file_ids: ["audio-book-1", "audio-book-2"],
    audio_file_durations_seconds: [1.2, 1.4],
    provider: "fake",
    model: "fake-tts",
    mime_type: "audio/mpeg",
    chunk_count: 2,
    source_text_hash: "hash",
    duration_seconds: 2.6,
    alignment_provider: "fake",
    alignment: [
      {
        text: "外婆",
        start_ms: 0,
        end_ms: 600,
        text_start: 0,
        text_end: 2,
      },
      {
        text: "回忆录",
        start_ms: 600,
        end_ms: 1200,
        text_start: 2,
        text_end: 5,
      },
    ],
  };
}

module.exports = {
  sampleAudioResponse,
  sampleAutomationEvent,
  sampleBookManuscript,
  sampleBookOutput,
  sampleBookOutputVersion,
  sampleDraft,
  sampleDraftWithContent,
  samplePlan,
  sampleRef,
  sampleStructuredBookContent,
};
