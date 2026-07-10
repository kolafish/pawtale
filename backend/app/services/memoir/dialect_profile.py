from __future__ import annotations

from dataclasses import dataclass


DIALECT_STRATEGY_NONE = "none"
DIALECT_STRATEGY_SICHUANESE = "sichuanese"
DIALECT_STRATEGY_CHONGQING = "chongqing"


@dataclass(frozen=True)
class MemoirDialectProfile:
    narrator_origin: str
    dialect_code: str = ""
    dialect_label: str = ""
    strategy: str = DIALECT_STRATEGY_NONE
    enhancement_enabled: bool = False

    def metadata(self) -> dict[str, object]:
        return {
            "narrator_origin": self.narrator_origin,
            "dialect_code": self.dialect_code,
            "dialect_label": self.dialect_label,
            "dialect_strategy": self.strategy,
            "dialect_enhancement_enabled": self.enhancement_enabled,
        }


def resolve_memoir_dialect_profile(narrator_origin: str) -> MemoirDialectProfile:
    origin = " ".join(str(narrator_origin or "").split())
    if "四川" in origin:
        return MemoirDialectProfile(
            narrator_origin=origin,
            dialect_code="zh-CN-sichuanese",
            dialect_label="四川话",
            strategy=DIALECT_STRATEGY_SICHUANESE,
            enhancement_enabled=True,
        )
    if "重庆" in origin:
        return MemoirDialectProfile(
            narrator_origin=origin,
            dialect_code="zh-CN-chongqing",
            dialect_label="重庆话",
            strategy=DIALECT_STRATEGY_CHONGQING,
            enhancement_enabled=True,
        )
    return MemoirDialectProfile(narrator_origin=origin)


def dialect_verification_guidance(profile: MemoirDialectProfile) -> str:
    if profile.enhancement_enabled:
        return (
            f"- 本次启用{profile.dialect_label}加强识别。四川及重庆方言常见词包括“啥子、咋个、"
            "没得、晓得、屋头、婆婆、幺儿、得嘛、噻、喃、耍、不得”；"
            "保留原本方言语义，不要擅自改写成普通话书面表达。"
        )
    if profile.narrator_origin:
        return (
            f"- 说话人籍贯为{profile.narrator_origin}，当前没有启用专门方言词法；"
            "遇到地方用语时只根据多路 ASR 上下文谨慎判断，不要套用四川话规则。"
        )
    return "- 项目没有填写籍贯，不启用特定方言规则；只根据多路 ASR 结果谨慎校对。"
