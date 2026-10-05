"""Small procedural paper stage; no raster generation or model-written code.

Characters and places are illustrative. Shared actor palettes, scene clocks,
continuous gait and camera interpolation keep a model's finite shots connected.
The caller owns original audio, captions, branding and video encoding.
"""
from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
import math

from PIL import Image, ImageDraw

from app.services.memoir.share_video_story_plan import ShareVideoStoryPlan, StoryShot


_EDGE = (64, 75, 66)
_SKIN = (208, 170, 129)
_HAIR = (70, 70, 61)
_COATS = ((78, 112, 109), (151, 107, 77), (111, 121, 89), (116, 101, 128))


def _ease(value: float) -> float:
    p = min(1.0, max(0.0, value))
    return p * p * (3 - 2 * p)


def _lerp(a: float, b: float, p: float) -> float:
    return a + (b - a) * p


def _scenery(setting: str, width: int, height: int) -> Image.Image:
    im = Image.new("RGB", (width, height), (214, 210, 187))
    d = ImageDraw.Draw(im)
    def box(x0, y0, x1, y1, fill):
        d.rectangle((round(x0 * width), round(y0 * height), round(x1 * width), round(y1 * height)), fill=fill)
    def polygon(points, fill):
        d.polygon([(round(x * width), round(y * height)) for x, y in points], fill=fill)
    def line(points, fill, weight=2):
        d.line([(round(x * width), round(y * height)) for x, y in points], fill=fill, width=max(1, round(weight * height / 800)))
    if setting in ("plain_room", "workshop"):
        polygon([(0, 0), (0.18, 0.1), (0.18, 0.67), (0, 0.75)], (190, 186, 163))
        box(0.23, 0.19, 0.49, 0.47, (95, 122, 111))
        box(0.24, 0.20, 0.48, 0.46, (173, 198, 179))
        line([(0.35, 0.20), (0.35, 0.46)], (111, 136, 115), 7)
        line([(0.24, 0.33), (0.48, 0.33)], (111, 136, 115), 6)
        box(0.8, 0.26, 0.91, 0.68, (115, 135, 116))
        polygon([(0, 0.75), (0.18, 0.67), (1, 0.69), (1, 1), (0, 1)], (160, 172, 149))
        line([(0, 0.75), (0.18, 0.67), (1, 0.69)], (130, 146, 125), 4)
        if setting == "workshop":
            # Architectural beams alone do not invent tools or a work process.
            for x in (0.59, 0.74):
                box(x, 0.11, x + 0.025, 0.69, (135, 145, 123))
            box(0.57, 0.13, 0.96, 0.155, (130, 139, 119))
    elif setting in ("street", "courtyard"):
        box(0, 0, 1, 0.66, (185, 203, 192))
        polygon([(0, 0.27), (0.34, 0.21), (0.34, 0.66), (0, 0.72)], (186, 182, 155))
        polygon([(0.62, 0.23), (1, 0.29), (1, 0.72), (0.62, 0.65)], (213, 195, 165))
        polygon([(0, 0.25), (0.34, 0.18), (0.37, 0.22), (0, 0.31)], (95, 124, 111))
        polygon([(0.59, 0.22), (0.64, 0.18), (1, 0.27), (1, 0.32)], (99, 123, 107))
        for x in (0.07, 0.23, 0.72, 0.88):
            box(x, 0.37, x + 0.055, 0.48, (117, 148, 132))
            box(x + 0.004, 0.375, x + 0.051, 0.474, (179, 199, 175))
        box(0.1, 0.55, 0.16, 0.70, (100, 121, 103))
        box(0.80, 0.54, 0.86, 0.70, (121, 133, 109))
        polygon([(0, 0.73), (0.34, 0.65), (0.62, 0.65), (1, 0.73), (1, 1), (0, 1)], (164, 174, 152))
        line([(0, 0.73), (0.34, 0.65), (0.62, 0.65), (1, 0.73)], (134, 150, 126), 3)
        if setting == "courtyard":
            box(0.34, 0.25, 0.62, 0.63, (204, 194, 164))
            box(0.42, 0.37, 0.53, 0.63, (122, 141, 118))
    elif setting == "vehicle":
        box(0, 0.15, 1, 0.67, (162, 172, 151))
        box(0.09, 0.22, 0.91, 0.53, (66, 91, 81))
        box(0.10, 0.23, 0.90, 0.52, (179, 201, 184))
        box(0.10, 0.62, 0.90, 0.73, (88, 113, 97))
        box(0.10, 0.71, 0.90, 0.76, (62, 88, 76))
        box(0, 0.76, 1, 1, (147, 165, 144))
    else:
        raise ValueError(f"Unsupported story setting: {setting}")
    for y in (0.81, 0.89, 0.98):
        line([(0, y), (1, y + 0.008)], (150, 162, 140), 1)
    return im


def _person(height: int, coat: tuple, *, child: bool, seated: bool, gait: float | None,
            gesture: str, progress: float, outside: float, amplitude: float, facing: int) -> Image.Image:
    # Local paper parts are drawn at target pixel density; faces stay readable
    # in both landscape sharing and portrait previews without stretching.
    size = (max(20, round(height * 0.53)), max(40, round(height * 1.08)))
    im = Image.new("RGBA", size)
    d = ImageDraw.Draw(im)
    scale = height / 440
    def pt(x, y):
        return (round(x * scale), round(y * scale))
    def poly(points, fill):
        d.polygon([pt(*point) for point in points], fill=fill)
        d.line([pt(*point) for point in points + [points[0]]], fill=_EDGE, width=max(1, round(2 * scale)))
    def line(points, fill, weight=12):
        pts = [pt(*point) for point in points]
        d.line(pts, fill=_EDGE, width=max(1, round((weight + 3) * scale)), joint="curve")
        d.line(pts, fill=fill, width=max(1, round(weight * scale)), joint="curve")
    swing = math.sin(gait) * 33 * amplitude if gait is not None and not seated else 0
    bob = -3 * (1 - math.cos(gait * 2)) if gait is not None and not seated else 0
    nod = math.sin(progress * math.pi) * 5 if gesture == "nod" else 0
    invite = _ease(progress / 0.22) * (1 - _ease((progress - 0.76) / 0.24)) if gesture == "invite" else 0
    # Fixed ankle positions and counter-swinging limbs make steps legible.
    if seated:
        line([(94, 281), (77, 333), (130, 353)], (114, 101, 81), 23)
        line([(125, 281), (147, 333), (164, 398)], (100, 92, 76), 23)
        line([(155, 400), (181, 400)], _HAIR, 14)
    else:
        line([(94, 282), (85 - swing * 0.32, 345), (76 + swing, 414)], (112, 101, 80), 25)
        line([(127, 282), (143 + swing * 0.32, 345), (151 - swing, 414)], (100, 92, 76), 25)
        line([(65 + swing, 423), (90 + swing, 423)], _HAIR, 14)
        line([(141 - swing, 423), (171 - swing, 423)], _HAIR, 14)
    poly([(78, 176 + bob), (147, 176 + bob), (159, 285 + bob), (70, 285 + bob)], coat)
    line([(80, 185 + bob), (60, 228 + bob), (55 - swing * 0.8, 275 + bob)], coat)
    hand = (164 + swing * 0.8 + 10 * invite, 276 + bob - 81 * invite)
    line([(147, 185 + bob), (169 + 15 * invite, 225 + bob - 24 * invite), hand], coat)
    d.ellipse((*pt(hand[0] - 8, hand[1] - 8), *pt(hand[0] + 8, hand[1] + 8)), fill=_SKIN, outline=_EDGE)
    # One continuous three-quarter silhouette, opening toward a window.
    hy = 88 + bob + nod
    head_scale = 1.10 if child else 1
    hx = 113
    rx, ry = 38 * head_scale, 54 * head_scale
    d.ellipse((*pt(hx - rx, hy - ry), *pt(hx + rx, hy + ry)), fill=_SKIN, outline=_EDGE, width=max(1, round(2 * scale)))
    poly([(hx - rx, hy), (hx - rx, hy - 33), (hx - 19, hy - 53), (hx + 22, hy - 48),
          (hx + rx, hy - 20), (hx + rx - 10, hy - 7), (hx + 17, hy - 29), (hx - 6, hy - 28)], _HAIR)
    nose = 143 + 13 * outside
    poly([(141, hy - 3), (nose, hy + 9), (140, hy + 16)], _SKIN)
    eye_x = _lerp(131, 142, outside)
    d.ellipse((*pt(eye_x - 2, hy - 3), *pt(eye_x + 2, hy + 1)), fill=_EDGE)
    line([(130, hy + 26), (141, hy + 27)], (126, 97, 75), 2)
    poly([(101, hy + 52), (129, hy + 48), (130, 177 + bob), (100, 177 + bob)], _SKIN)
    line([(82, 181 + bob), (107, 194 + bob), (144, 181 + bob)], (175, 182, 151), 2)
    return im if facing > 0 else im.transpose(Image.Transpose.FLIP_LEFT_RIGHT)


def _prop(image: Image.Image, kind: str, x: float, y: float, size: float) -> None:
    d = ImageDraw.Draw(image)
    if kind == "bowl":
        d.pieslice((x - size, y - size * 0.35, x + size, y + size), 0, 180, fill=(220, 205, 167), outline=_EDGE, width=2)
        d.ellipse((x - size, y - size * 0.35, x + size, y + size * 0.15), fill=(169, 158, 126), outline=_EDGE, width=2)
    elif kind in ("stone", "crystal"):
        points = [(x - size, y + size * 0.4), (x - size * 0.55, y - size * 0.4),
                  (x + size * 0.22, y - size * 0.8), (x + size, y), (x + size * 0.6, y + size * 0.6)]
        d.polygon(points, fill=(154, 174, 161) if kind == "crystal" else (139, 138, 121))
        d.line(points + [points[0]], fill=_EDGE, width=2)
        if kind == "crystal":
            d.line([points[0], points[2], points[3]], fill=(205, 222, 200), width=2)
    elif kind == "table":
        d.rectangle((x - size * 3, y, x + size * 3, y + size * 0.25), fill=(148, 113, 80), outline=_EDGE, width=2)
        for xx in (x - size * 2.6, x + size * 2.6):
            d.rectangle((xx, y, xx + size * 0.3, y + size * 1.8), fill=(125, 102, 77))
    elif kind == "steps":
        for i in range(3):
            d.rectangle((x - size * (2.6 - i * 0.4), y - size * i * 0.4,
                         x + size * (2.6 - i * 0.4), y + size * 0.4), fill=(170 + i * 10, 174 + i * 7, 151 + i * 5), outline=_EDGE)


def create_story_renderer(plan: ShareVideoStoryPlan, width: int, height: int) -> Callable[[float], Image.Image]:
    """Cache a stage per render; return RGB frames with a caption-safe foreground."""
    if width <= 0 or height <= 0:
        raise ValueError("Story frame dimensions must be positive")
    ww, hh = round(width * 1.2), round(height * 1.2)
    backgrounds = {shot.setting: _scenery(shot.setting, ww, hh) for shot in plan.shots}
    actors = {actor.id: actor for actor in plan.actors}
    members = [(actor.id, n, actor.figure) for actor in plan.actors for n in range(2 if actor.figure == "group" else 1)]
    origins, group_starts, group_ends = [], [], []
    scene_start, group_start = 0, 0
    for i, shot in enumerate(plan.shots):
        if i == 0 or plan.shots[i - 1].setting != shot.setting:
            scene_start = shot.start_ms
        if i == 0 or plan.shots[i - 1].sentence_ids != shot.sentence_ids:
            group_start = shot.start_ms
        origins.append(scene_start / 1000)
        group_starts.append(group_start / 1000)
        j = i
        while j + 1 < len(plan.shots) and plan.shots[j + 1].sentence_ids == shot.sentence_ids:
            j += 1
        group_ends.append(plan.shots[j].end_ms / 1000)

    def camera(index: int, t: float) -> tuple[float, float, float]:
        shot = plan.shots[index]
        p = _ease((t * 1000 - shot.start_ms) / (shot.end_ms - shot.start_ms))
        # Lower the viewport centre while leaving actors on the drawn ground.
        # This keeps even a detail shot's feet above 550 on a 720px frame, with
        # room for two shared caption lines near y=600 and the top brand label.
        cx, cy = ww * 0.5, hh * 0.55
        zoom = {"wide": 1.0, "medium": 1.06, "detail": 1.13}[shot.framing]
        if shot.camera == "pan":
            cx += (p - 0.5) * ww * 0.035
        elif shot.camera == "track":
            cx += math.sin(p * math.pi) * ww * 0.014
        elif shot.camera == "slow_push":
            zoom += 0.025 * p
        elif shot.camera == "reveal":
            zoom += 0.025 * (1 - p)
        return cx, cy, zoom

    def stage(index: int, t: float) -> Image.Image:
        shot: StoryShot = plan.shots[index]
        elapsed = max(0, t - shot.start_ms / 1000)
        scene_t = max(0, t - origins[index])
        group_t = max(0, t - group_starts[index])
        group_duration = max(0.001, group_ends[index] - group_starts[index])
        progress = _ease(elapsed / max(0.001, (shot.end_ms - shot.start_ms) / 1000))
        amplitude = shot.movement_scale
        if index and plan.shots[index - 1].setting == shot.setting:
            amplitude = _lerp(plan.shots[index - 1].movement_scale, amplitude, _ease(elapsed / 0.65))
        image = backgrounds[shot.setting].copy()
        if shot.setting == "vehicle":
            # Periodic plain silhouettes move continuously behind the glass;
            # no route, vehicle category or geographic landmark is asserted.
            d = ImageDraw.Draw(image)
            for n in range(7):
                x = ((n * 0.16 - scene_t * 0.045) % 1.12 - 0.11) * ww
                left, right = max(ww * 0.105, x), min(ww * 0.895, x + ww * 0.10)
                if right > left:
                    d.rectangle((left, hh * 0.34, right, hh * 0.51), fill=(150 + (n % 3) * 10, 166, 141))
            d.line((ww * 0.10, hh * 0.52, ww * 0.90, hh * 0.52), fill=(69, 94, 80), width=max(2, round(hh * 0.006)))
        cast = [member for member in members if member[0] in shot.actor_ids]
        count = len(cast)
        # Keep all heads and active hands above the shared caption area even
        # with several representatives in a narrow portrait viewport.
        person_h = min(hh * 0.48, ww * 0.8 / max(1, count) / 0.50)
        for place, (actor_id, member, figure) in enumerate(cast):
            x = ww * (0.50 + (place - (count - 1) / 2) * min(0.19, 0.65 / max(1, count)))
            foot = hh * 0.74 - (place % 2) * hh * 0.015
            h = person_h * (0.75 if figure == "child" else 1 - (place % 2) * 0.06)
            gait = None
            if shot.action == "walk":
                gait = scene_t * math.tau / 1.65 + place * 0.8
                x += (_ease(group_t / group_duration) - 0.5) * ww * 0.04
            elif shot.action == "gather" and place:
                approach = _ease(group_t / min(3, group_duration))
                x += (-1 if place % 2 else 1) * ww * 0.08 * (1 - approach)
                if approach < 1:
                    gait = scene_t * math.tau / 1.75 + place * 0.8
            active = actor_id == shot.active_actor_id and member == 0
            gesture = shot.gesture if active else "relaxed"
            outside = _ease(elapsed / 1.2) if active and shot.action == "look_outside" else 0
            palette = int(sha256(actor_id.encode()).hexdigest()[:8], 16) % len(_COATS)
            actor_image = _person(round(h), _COATS[palette], child=actors[actor_id].figure == "child",
                                  seated=shot.action in ("sit", "ride", "look_outside"), gait=gait,
                                  gesture=gesture, progress=progress, outside=outside,
                                  amplitude=amplitude, facing=-1 if place % 2 and shot.action == "gather" else 1)
            image.paste(actor_image, (round(x - actor_image.width / 2), round(foot - h)), actor_image)
            if active and shot.action == "hold_object":
                for prop in shot.props:
                    if prop.kind in ("bowl", "stone", "crystal"):
                        _prop(image, prop.kind, x + h * 0.12, foot - h * 0.36, h * 0.055)
        for prop in shot.props:
            if prop.kind in ("table", "steps"):
                _prop(image, prop.kind, ww * 0.5, hh * 0.70, min(ww * 0.08, hh * 0.07))
        control = camera(index, t)
        if index and plan.shots[index - 1].setting == shot.setting:
            p = _ease(elapsed / min(1.0, (shot.end_ms - shot.start_ms) / 1000))
            control = tuple(_lerp(a, b, p) for a, b in zip(camera(index - 1, shot.start_ms / 1000), control))
        cx, cy, zoom = control
        crop_w, crop_h = width / zoom, height / zoom
        cx = max(crop_w / 2, min(ww - crop_w / 2, cx))
        cy = max(crop_h / 2, min(hh - crop_h / 2, cy))
        return image.resize((width, height), Image.Resampling.LANCZOS,
                            box=(cx - crop_w / 2, cy - crop_h / 2, cx + crop_w / 2, cy + crop_h / 2))

    def render(t_seconds: float) -> Image.Image:
        if not math.isfinite(t_seconds):
            raise ValueError("Story frame time must be finite")
        t = min(plan.duration_ms / 1000, max(0, t_seconds))
        index = next((i for i, shot in enumerate(plan.shots) if t * 1000 < shot.end_ms), len(plan.shots) - 1)
        frame = stage(index, t)
        if index:
            shot, previous = plan.shots[index], plan.shots[index - 1]
            # Camera-only shots keep the same continuous performance. Scene,
            # cast or action changes dissolve briefly while the voice continues.
            if (shot.setting, shot.action, shot.actor_ids, shot.gesture) != (
                previous.setting, previous.action, previous.actor_ids, previous.gesture
            ) and t - shot.start_ms / 1000 < 0.35:
                frame = Image.blend(stage(index - 1, shot.start_ms / 1000), frame,
                                    _ease((t - shot.start_ms / 1000) / 0.35))
        return frame.convert("RGB")
    return render


def render_story_frame(plan: ShareVideoStoryPlan, t_seconds: float, width: int, height: int) -> Image.Image:
    return create_story_renderer(plan, width, height)(t_seconds)
