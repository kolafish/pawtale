"""Material-group proposals compile to the existing transactional directory writer."""
from collections import Counter
from dataclasses import replace

from app.schemas.memoir_editorial import BookStructureFinding
from pydantic import BaseModel, ConfigDict, Field
from app.services.memoir import chapter_structure as st


class TargetSection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=200)
    material_refs: list[str]
    empty_section_ref: str | None = None


class TargetChapter(BaseModel):
    model_config = ConfigDict(extra='forbid')
    chapter_ref: str = Field(pattern=r'^(C|N)[1-9][0-9]*$')
    title: str = Field(min_length=1, max_length=200)
    sections: list[TargetSection]


class TargetDirectory(BaseModel):
    model_config = ConfigDict(extra='forbid')
    chapters: list[TargetChapter] = Field(min_length=1)


def compile_groups(target: TargetDirectory, context: st.StructureContext) -> st.StructureOutput:
    """No semantic decisions: partition source memberships, reuse IDs, validate."""
    groups = [(chapter, section) for chapter in target.chapters for section in chapter.sections]
    actual = [ref for _, group in groups for ref in group.material_refs]
    expected = [ref for values in context.section_materials.values() for ref in values]
    if Counter(actual) != Counter(expected) or len(actual) != len(set(actual)):
        raise ValueError('最终分组必须完整且唯一地收录已有素材，不得增加或遗漏')
    destination = {ref: index for index, (_, group) in enumerate(groups) for ref in group.material_refs}
    source_groups = {s.section_refs[0]: (chapter, s) for chapter in context.outline.chapters for s in chapter.sections}
    empty_expected = {ref for ref, values in context.section_materials.items() if not values}
    empty_actual = [g.empty_section_ref for _, g in groups if not g.material_refs]
    if Counter(empty_actual) != Counter(empty_expected):
        raise ValueError('无素材的现有小节必须通过 empty_section_ref 完整保留')
    if any(g.empty_section_ref for _, g in groups if g.material_refs):
        raise ValueError('有素材的小节不能使用 empty_section_ref')
    parts = {i: [] for i in range(len(groups))}
    splits = []
    for source_ref, members in context.section_materials.items():
        old_chapter, old_group = source_groups[source_ref]
        if not members:
            index = next(i for i, (_, g) in enumerate(groups) if g.empty_section_ref == source_ref)
            parts[index].append(source_ref)
            continue
        buckets = {}
        for material in members:
            buckets.setdefault(destination[material], []).append(material)
        forced = {destination[m] for m in members if m in context.fixed_materials}
        if source_ref in context.fixed_sections or context.chapter_refs.decode(old_chapter.chapter_ref) in context.locked_chapters:
            if len(buckets) != 1:
                raise ValueError('受保护小节不能拆分')
            forced.update(buckets)
        if len(forced) > 1:
            raise ValueError('同一小节中人工归属素材必须保留在同一原身份下')
        candidates = list(forced or buckets)
        if source_ref in context.section_titles:
            candidates = [i for i in candidates if groups[i][1].title == context.section_titles[source_ref]]
            if not candidates:
                raise ValueError('最终分组必须保留人工小节标题')
        keep = max(candidates, key=lambda i: (len(buckets[i]), groups[i][1].title == old_group.title, -i))
        for index, selected in buckets.items():
            ref = source_ref
            if index != keep:
                ref = f'T{len(splits)+1}'
                splits.append(st.SectionSplit(source_section_ref=source_ref, new_section_ref=ref, material_refs=selected))
            parts[index].append(ref)
    material_sets = {ref: set(values) for ref, values in context.section_materials.items()}
    for split in splits:
        material_sets[split.new_section_ref] = set(split.material_refs)
        material_sets[split.source_section_ref] -= set(split.material_refs)
    protected_refs = set(context.fixed_sections) | set(context.section_titles) | {s for s, _ in context.fixed_materials.values()}
    for index, refs in parts.items():
        forced = [ref for ref in refs if ref in protected_refs]
        if len(forced) > 1:
            raise ValueError('不能通过合节丢失多个受保护小节身份')
        keep = forced[0] if forced else max(refs, key=lambda ref: (ref.startswith('S'), len(material_sets[ref]), -refs.index(ref)))
        parts[index] = [keep, *[ref for ref in refs if ref != keep]]
    chapters = []
    index = 0
    for chapter in target.chapters:
        converted = []
        for section in chapter.sections:
            converted.append(st.SectionGroup(section_refs=parts[index], title=section.title))
            index += 1
        chapters.append(st.ChapterGroup(chapter_ref=chapter.chapter_ref, title=chapter.title, sections=converted))
    result = st.StructureOutput(chapters=chapters, section_splits=splits)
    st.validate_structure(result, context)
    if material_layout(result, context) != target_layout(target):
        raise ValueError('转换结果与模型最终分组不一致')
    return result


def target_layout(target):
    return [(c.chapter_ref, c.title, [(s.title, sorted(s.material_refs)) for s in c.sections]) for c in target.chapters]


def material_layout(output, context):
    members = {k: list(v) for k, v in context.section_materials.items()}
    for split in output.section_splits:
        members[split.new_section_ref] = split.material_refs
        members[split.source_section_ref] = [m for m in members[split.source_section_ref] if m not in split.material_refs]
    return [(c.chapter_ref, c.title, [(s.title, sorted(m for ref in s.section_refs for m in members[ref])) for s in c.sections]) for c in output.chapters]



class StructureDiagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    findings: list[BookStructureFinding] = Field(default_factory=list)


def select_context(context: st.StructureContext, chapter_refs: set[str], section_refs: set[str] | None = None) -> st.StructureContext:
    chapters = [c.model_copy(deep=True) for c in context.outline.chapters if c.chapter_ref in chapter_refs]
    chapter_ids = {context.chapter_refs.decode(c.chapter_ref) for c in chapters}
    if section_refs is not None:
        for chapter in chapters:
            chapter.sections = [s for s in chapter.sections if s.section_refs[0] in section_refs]
    refs = {s.section_refs[0] for c in chapters for s in c.sections}
    return replace(context, outline=st.StructureOutput(chapters=chapters),
        section_materials={r: members for r, members in context.section_materials.items() if r in refs},
        chapter_titles={r: title for r, title in context.chapter_titles.items() if r in chapter_refs},
        section_titles={r: title for r, title in context.section_titles.items() if r in refs},
        fixed_sections=context.fixed_sections & refs,
        fixed_materials={m: destination for m, destination in context.fixed_materials.items() if destination[0] in refs},
        manual_chapter_order=tuple(cid for cid in context.manual_chapter_order if cid in chapter_ids))


def finding_context(context: st.StructureContext, finding: BookStructureFinding) -> st.StructureContext:
    return select_context(context, set(finding.chapter_ids),
        set(finding.section_ids) if finding.section_ids and finding.kind != "chapter_structure" else None)


def validate_diagnosis(diagnosis: StructureDiagnosis, context: st.StructureContext) -> None:
    chapters = {c.chapter_ref: c for c in context.outline.chapters}
    claimed = set()
    for finding in diagnosis.findings:
        refs = finding.chapter_ids
        if len(refs) != len(set(refs)) or not set(refs) <= chapters.keys():
            raise ValueError("诊断只能引用审阅范围内的唯一章节")
        if any(context.chapter_refs.decode(ref) in context.locked_chapters for ref in refs):
            raise ValueError("不能调整受保护章节")
        sections = [s.section_refs[0] for ref in refs for s in chapters[ref].sections]
        selected = finding.section_ids
        if len(selected) != len(set(selected)) or not set(selected) <= set(sections):
            raise ValueError("诊断的小节必须属于所列章节且不能重复")
        if finding.kind == "chapter_structure":
            indices = [list(chapters).index(ref) for ref in refs]
            if indices != list(range(min(indices), max(indices) + 1)):
                raise ValueError("章级调整必须列出按原顺序排列的连续章节范围")
            occupied = {f"chapter:{ref}" for ref in refs} | set(sections)
        elif finding.kind == "section_structure":
            if len(refs) != 1 or not selected:
                raise ValueError("节级调整必须指出一个章节内需要调整的小节；跨章问题使用 chapter_structure")
            indices = [sections.index(ref) for ref in selected]
            if indices != list(range(min(indices), max(indices) + 1)):
                raise ValueError("节级调整必须列出按原顺序排列的最小连续小节范围")
            occupied = set(selected)
        else:
            occupied = set(selected) if selected else {f"chapter:{ref}" for ref in refs}
        if claimed & occupied:
            raise ValueError("问题调整范围不得重叠；结构调整中的命名一并完成")
        claimed.update(occupied)


def compile_region(target: TargetDirectory, context: st.StructureContext, finding: BookStructureFinding) -> st.StructureOutput:
    proposed = target.model_copy(deep=True)
    original = context.outline
    if finding.kind != "chapter_structure":
        if [c.chapter_ref for c in proposed.chapters] != [c.chapter_ref for c in original.chapters]:
            raise ValueError("小节及标题调整不能增删或重排章节")
        for chapter, old in zip(proposed.chapters, original.chapters, strict=True):
            if finding.kind == "section_structure" or finding.section_ids:
                chapter.title = old.title
    old_groups = {frozenset(context.section_materials[s.section_refs[0]]): s
                  for c in original.chapters for s in c.sections if context.section_materials[s.section_refs[0]]}
    if finding.kind != "title_scope":
        for chapter in proposed.chapters:
            for group in chapter.sections:
                old = old_groups.get(frozenset(group.material_refs))
                if old is not None:
                    group.title = old.title
    else:
        for chapter, old_chapter in zip(proposed.chapters, original.chapters, strict=True):
            expected = [set(context.section_materials[s.section_refs[0]]) for s in old_chapter.sections]
            if [set(s.material_refs) for s in chapter.sections] != expected:
                raise ValueError("标题问题只能改名，不得改变小节数量、素材归属或顺序")
            if not finding.section_ids:
                for group, old in zip(chapter.sections, old_chapter.sections, strict=True):
                    group.title = old.title
    return compile_groups(proposed, context)


def splice_regions(context: st.StructureContext, diagnosis: StructureDiagnosis,
                   proposals: list[st.StructureOutput]) -> st.StructureOutput:
    validate_diagnosis(diagnosis, context)
    if len(proposals) != len(diagnosis.findings):
        raise ValueError("每个诊断必须有对应的处理结果")
    result = context.outline.model_copy(deep=True)
    next_split = 1
    next_chapter = 1
    for finding, raw in zip(diagnosis.findings, proposals, strict=True):
        proposal = raw.model_copy(deep=True)
        st.validate_structure(proposal, finding_context(context, finding))
        # Independent regions may use the same local T/N references.
        renamed = {}
        for split in proposal.section_splits:
            renamed[split.new_section_ref] = f"T{next_split}"
            next_split += 1
            split.new_section_ref = renamed[split.new_section_ref]
        for chapter in proposal.chapters:
            if chapter.chapter_ref.startswith("N"):
                chapter.chapter_ref = f"N{next_chapter}"
                next_chapter += 1
            for section in chapter.sections:
                section.section_refs = [renamed.get(ref, ref) for ref in section.section_refs]
        if finding.kind == "chapter_structure":
            first = next(i for i, c in enumerate(result.chapters) if c.chapter_ref == finding.chapter_ids[0])
            result.chapters[first:first + len(finding.chapter_ids)] = proposal.chapters
        elif finding.section_ids:
            replacements = {c.chapter_ref: c for c in proposal.chapters}
            for chapter in result.chapters:
                if chapter.chapter_ref not in replacements:
                    continue
                selected = [i for i, s in enumerate(chapter.sections) if s.section_refs[0] in finding.section_ids]
                if finding.kind == "title_scope":
                    by_ref = {s.section_refs[0]: s for s in replacements[chapter.chapter_ref].sections}
                    for i in selected:
                        chapter.sections[i] = by_ref[chapter.sections[i].section_refs[0]]
                else:
                    chapter.sections[min(selected):max(selected) + 1] = replacements[chapter.chapter_ref].sections
        else:
            titles = {c.chapter_ref: c.title for c in proposal.chapters}
            for chapter in result.chapters:
                chapter.title = titles.get(chapter.chapter_ref, chapter.title)
        result.section_splits.extend(proposal.section_splits)
    st.validate_structure(result, context)
    return result
