"""Text completeness evidence, distinct from mere OCR rectangle intersection.

OCR padding and incidental text from a neighbour are harmless. A word belongs to
one panel; only missing foreground in that panel is a clipping candidate. Group
headings in an isolated strip above adjacent labelled panels are optional.
Semantic ownership and non-text content still require the visual reviewer.
"""

import cv2
import numpy as np
from PIL import Image

from .models import Issue, Layout


def _intersection(first, second):
    a, b, c, d = first
    e, f, g, h = second
    return max(a, e), max(b, f), min(c, g), min(d, h)


def _area(rect):
    a, b, c, d = rect
    return max(0, c - a) * max(0, d - b)


def _foreground(gray):
    # Otsu handles both coloured/dark text on light paper and white text on dark
    # images. Use the perimeter to choose background polarity, not a fixed colour.
    if not gray.size or int(gray.max()) - int(gray.min()) < 12:
        return np.zeros(gray.shape, dtype=bool)
    threshold, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    perimeter = np.concatenate((gray[0], gray[-1], gray[:, 0], gray[:, -1]))
    mask = gray <= threshold if np.median(perimeter) > threshold else gray > threshold
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    keep = np.zeros(count, dtype=bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= 2
    return keep[labels]


def _word_ink(gray, word):
    height, width = gray.shape
    rect = _intersection(word["rect"], (0, 0, width, height))
    a, b, c, d = rect
    if not _area(rect):
        return rect, np.zeros((0, 0), dtype=bool)
    # Include a small background border in the threshold calculation, then trim.
    e, f, g, h = max(0, a - 2), max(0, b - 2), min(width, c + 2), min(height, d + 2)
    ink = _foreground(gray[f:h, e:g])[b-f:d-f, a-e:c-e]
    if word.get("polygon"):
        polygon = np.asarray(word["polygon"], dtype=float) - (a, b)
        polygon_mask = np.zeros(ink.shape, dtype=np.uint8)
        cv2.fillPoly(polygon_mask, [np.rint(polygon).astype(np.int32)], 1)
        ink &= polygon_mask.astype(bool)
    # A few anti-aliased grey fringe pixels do not truncate a stroke. Separate
    # stroke cores from their own edge tones instead of tolerating a larger
    # rectangle-intersection percentage (which could hide whole small glyphs).
    values = gray[b:d, a:c][ink]
    if values.size:
        patch = gray[f:h, e:g]
        background = np.median(np.concatenate((patch[0], patch[-1], patch[:, 0], patch[:, -1])))
        if np.median(values) < background:
            core = (float(np.quantile(values, .25)) + int(values.max())) / 2
            ink &= gray[b:d, a:c] <= core
        else:
            core = (float(np.quantile(values, .75)) + int(values.min())) / 2
            ink &= gray[b:d, a:c] >= core
    return rect, ink


def _retained(ink, word_rect, panel_rect):
    a, b, c, d = _intersection(word_rect, panel_rect)
    if not _area((a, b, c, d)):
        return 0
    x, y = word_rect[:2]
    return int(ink[b-y:d-y, a-x:c-x].sum())


def _label_anchors(layout, words):
    anchors = {}
    for panel in layout.panels:
        candidates = []
        for index, word in enumerate(words):
            if word["text"].strip().casefold() != panel.label.casefold():
                continue
            a, b, c, d = word["rect"]
            font = max(1, d - b)
            x, y, right, bottom = panel.rect
            if (abs(a - x) <= max(2 * font, (right-x) * .18)
                    and y - font <= b <= y + (bottom-y) * .35):
                candidates.append((abs(a-x) + abs(b-y), index))
        if candidates:
            anchors[panel.id] = min(candidates)[1]
    return anchors


def _heading_strip_is_clear(word, words, gray, x1, x2):
    _, b, _, d = word["rect"]
    height = max(1, d-b)
    y1, y2 = max(0, b-1), min(gray.shape[0], d+1)
    band = _foreground(gray[y1:y2, x1:x2])
    for other in words:
        e, f, g, h = other["rect"]
        if abs((f+h)-(b+d)) <= height and g-e >= 1.4 * max(1, h-f):
            left, upper, right, lower = _intersection(
                (e-2, f-1, g+2, h+1), (x1, y1, x2, y2))
            if _area((left, upper, right, lower)):
                band[upper-y1:lower-y1, left-x1:right-x1] = False
    return int(band.sum()) <= 2


def _optional_heading(word, layout, words, anchors, gray):
    a, b, c, d = word["rect"]
    height = max(1, d-b)
    if c-a < 1.4 * height:
        return False
    following = []
    for panel in layout.panels:
        index = anchors.get(panel.id)
        if index is None or min(c, panel.rect[2]) <= max(a, panel.rect[0]):
            continue
        label_top = words[index]["rect"][1]
        if 0 <= label_top-d <= 3 * height:
            following.append((panel, label_top))
    # Titles above a vertical group may sit over its first panel alone. Require
    # a centred, isolated heading ABOVE its letter label, not inside its plot.
    for panel, _ in following:
        left, _, right, _ = panel.rect
        if (left <= a < c <= right and c-a < .9 * (right-left)
                and abs((a+c)-(left+right)) <= .4 * (right-left)
                and _heading_strip_is_clear(word, words, gray, left, right)):
            return True
    # Otherwise it must span separate columns whose own labels start on the
    # same row below it. Rotated axis groups and plot content fail the clear strip.
    for first, top in following:
        for second, other_top in following:
            if (second.rect[0] <= first.rect[0] or abs(top-other_top) > height
                    or second.rect[0] < first.rect[2] - height):
                continue
            x1, x2 = min(first.rect[0], second.rect[0]), max(first.rect[2], second.rect[2])
            if _heading_strip_is_clear(word, words, gray, x1, x2):
                return True
    return False


def text_integrity_issues(layout: Layout, text: dict, image: Image.Image | None) -> list[Issue]:
    words = text.get("boxes", [])
    if not words:
        return []
    if image is None:
        return [Issue(code="TEXT_CHECK_UNAVAILABLE", severity="warning",
                      message="缺少原图像素，未执行文字完整性检查，不能自动通过")]
    gray = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
    if tuple(layout.image_size) != image.size:
        raise ValueError("Text check image size does not match layout")
    anchors = _label_anchors(layout, words)
    label_owners = {index: panel_id for panel_id, index in anchors.items()}
    issues = []
    for index, word in enumerate(words):
        rect, ink = _word_ink(gray, word)
        total = int(ink.sum())
        if total < 6:
            continue
        # Existing explicitly kept shared titles are optional too. Do not crop
        # or alter them; just avoid treating their text as panel-owned content.
        if any(_area(_intersection(rect, shared.rect)) == _area(rect) for shared in layout.shared_regions):
            continue
        if _optional_heading(word, layout, words, anchors, gray):
            continue
        retained = {panel.id: _retained(ink, rect, panel.rect) for panel in layout.panels}
        if not retained or not max(retained.values()):
            continue  # Entirely external text is for semantic visual review.
        owner = label_owners.get(index)
        if owner is None:
            eligible = []
            for panel in layout.panels:
                anchor = anchors.get(panel.id)
                top = words[anchor]["rect"][1] if anchor is not None else panel.rect[1]
                if retained[panel.id] and (rect[1] + rect[3]) / 2 >= top:
                    eligible.append(panel)
            owner = max(eligible or layout.panels, key=lambda panel: retained[panel.id]).id
        inside = retained[owner]
        # Count actual missing strokes, including a cut BETWEEN characters.
        # Do not use the OCR box's area fraction, which confuses padding with ink.
        if inside >= 3 and total - inside >= 3:
            issues.append(Issue(code="TEXT_INTERSECTION", severity="warning", region_id=owner,
                                message=f"子图内部文字疑似被截断：{word['text'][:80]}"))
    return issues
