import json
import sqlite3

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from PIL import Image, ImageDraw, ImageFont

from app.gateway import Gateway
from app.models import Layout, Provider, WorkRequest
from app.vision import check_layout, crop_layout
from app.workflow import FigureWorkflow, REVIEW_SYSTEM, SYSTEM


def draw_word(image, text, x, y, *, padding=0, size=18, fill="black"):
    font = ImageFont.load_default(size=size)
    draw = ImageDraw.Draw(image)
    bounds = draw.textbbox((x, y), text, font=font)
    draw.text((x, y), text, font=font, fill=fill)
    return {"text": text, "score": .99,
            "rect": [bounds[0]-padding, bounds[1]-padding, bounds[2]+padding, bounds[3]+padding]}


def make_layout(rects, size=(320, 220), shared=None):
    return Layout(image_size=size, panels=[{"id": f"p_{label}", "label": label, "rect": rect}
                                            for label, rect in rects.items()], shared_regions=shared or [])


def issues(image, layout, *words):
    return check_layout(layout, {"available": True, "boxes": list(words)}, image)


def test_ocr_padding_crossing_is_not_clipped_text():
    image = Image.new("RGB", (320, 220), "white")
    word = draw_word(image, "Time (min)", 30, 90, padding=8)
    bottom = word["rect"][3] - 5  # Cuts padding, keeps every dark text pixel.
    assert issues(image, make_layout({"A": [0, 0, 160, bottom]}), word) == []


def test_only_antialias_fringe_outside_crop_is_harmless():
    image = Image.new("RGB", (320, 220), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((30, 60, 80, 75), fill="black")
    draw.rectangle((35, 76, 38, 76), fill=(130, 130, 130))
    word = {"text": "axis", "rect": [28, 58, 83, 79]}
    assert issues(image, make_layout({"A": [0, 0, 160, 76]}), word) == []


@pytest.mark.parametrize("edge", ["left", "right", "top", "bottom"])
def test_actual_panel_owned_text_clipping_in_each_direction(edge):
    image = Image.new("RGB", (320, 220), "white")
    word = draw_word(image, "Axis title", 65, 85, size=24)
    a, b, c, d = word["rect"]
    rect = [0, 0, 320, 220]
    rect[{"left": 0, "top": 1, "right": 2, "bottom": 3}[edge]] = {
        "left": a+8, "right": c-8, "top": b+5, "bottom": d-5}[edge]
    found = issues(image, make_layout({"A": rect}), word)
    assert [i["region_id"] for i in found] == ["p_A"]


def test_cut_between_characters_still_truncates_the_word():
    image = Image.new("RGB", (320, 220), "white")
    word = draw_word(image, "DOX + PD-1", 30, 100)
    found = issues(image, make_layout({"A": [0, 0, 83, 220]}), word)
    assert len(found) == 1 and found[0]["code"] == "TEXT_INTERSECTION"


def test_neighbour_text_partially_included_does_not_create_a_warning():
    image = Image.new("RGB", (320, 220), "white")
    word = draw_word(image, "8.0", 190, 90, size=24)
    layout = make_layout({"A": [0, 0, 205, 220], "B": [180, 0, 320, 220]})
    assert not any(i["code"] == "TEXT_INTERSECTION" for i in issues(image, layout, word))


def test_neighbour_panel_letter_belongs_only_to_its_matching_panel():
    image = Image.new("RGB", (320, 220), "white")
    letter = draw_word(image, "B", 150, 40, size=24)
    layout = make_layout({"A": [0, 0, 160, 220], "B": [148, 35, 320, 220]})
    assert not any(i["code"] == "TEXT_INTERSECTION" for i in issues(image, layout, letter))


@pytest.mark.parametrize("title", ["MC-3T3-E1", "HUVEC", "Other cell type"])
@pytest.mark.parametrize("mode", ["omitted", "vertical_cut", "horizontal_cut"])
def test_external_group_headings_are_optional_without_name_whitelists(title, mode):
    image = Image.new("RGB", (320, 220), "white")
    heading = draw_word(image, title, 100, 8, size=18)
    letters = [draw_word(image, "A", 10, 55), draw_word(image, "B", 175, 55)]
    top = 45 if mode == "omitted" else (18 if mode == "horizontal_cut" else 0)
    layout = make_layout({"A": [0, top, 160, 220], "B": [160, top, 320, 220]})
    assert issues(image, layout, heading, *letters) == []


def test_heading_above_vertical_group_may_be_partly_included():
    image = Image.new("RGB", (320, 220), "white")
    heading = draw_word(image, "Other cells", 100, 8)
    letter = draw_word(image, "A", 10, 50)
    assert issues(image, make_layout({"A": [0, 20, 320, 220]}), heading, letter) == []


def test_internal_plot_title_with_same_text_is_still_protected():
    image = Image.new("RGB", (320, 220), "white")
    letter = draw_word(image, "A", 2, 1)
    title = draw_word(image, "MC-3T3-E1", 90, 50)
    layout = make_layout({"A": [0, 0, 162, 220]})
    found = issues(image, layout, letter, title)
    assert len(found) == 1 and "MC-3T3-E1" in found[0]["message"]


def test_axis_text_next_to_next_row_is_not_exempted_as_a_heading():
    image = Image.new("RGB", (320, 220), "white")
    axis = draw_word(image, "Concentration", 60, 80, size=24)
    next_label = draw_word(image, "B", 5, 110)
    ImageDraw.Draw(image).line((0, 85, 319, 85), fill="black", width=2)
    layout = make_layout({"A": [0, 0, 320, 97], "B": [0, 95, 320, 220]})
    found = issues(image, layout, axis, next_label)
    assert [i["region_id"] for i in found if i["code"] == "TEXT_INTERSECTION"] == ["p_A"]


def test_explicit_legacy_shared_heading_is_optional_and_export_still_supported(tmp_path):
    image = Image.new("RGB", (320, 220), "white")
    heading = draw_word(image, "Shared heading", 90, 8)
    shared = [{"id": "s_1", "label": "heading", "rect": heading["rect"], "panel_ids": ["p_A", "p_B"]}]
    layout = make_layout({"A": [0, 0, 160, 220], "B": [160, 0, 320, 220]}, shared=shared)
    assert issues(image, layout, heading) == []
    source = tmp_path / "source.png"
    image.save(source)
    result = crop_layout(str(source), layout, tmp_path / "version", {"available": True, "boxes": [heading]})
    assert result["layout"]["files"]["s_1"] == "shared_s_1.png"


def test_white_internal_text_on_dark_background_is_checked():
    image = Image.new("RGB", (320, 220), "black")
    word = draw_word(image, "Scale 100 um", 80, 100, fill="white")
    found = issues(image, make_layout({"A": [0, 0, 150, 220]}), word)
    assert len(found) == 1 and found[0]["code"] == "TEXT_INTERSECTION"


def test_polygon_avoids_unrelated_ink_in_rotated_text_bbox():
    image = Image.new("RGB", (320, 220), "white")
    draw = ImageDraw.Draw(image)
    draw.polygon([(20, 95), (90, 25), (100, 35), (30, 105)], fill="black")
    draw.rectangle((90, 90, 103, 105), fill="black")  # Outside the text polygon.
    word = {"text": "rotated", "rect": [15, 20, 105, 110],
            "polygon": [[20, 95], [90, 25], [100, 35], [30, 105]]}
    assert issues(image, make_layout({"A": [0, 0, 101, 220]}), word) == []


@pytest.mark.parametrize("overlap", [0, 5])
def test_real_graph_accepts_optional_heading_without_shared_crops(monkeypatch, tmp_path, overlap):
    image = Image.new("RGB", (320, 220), "white")
    words = [draw_word(image, "External group", 90, 8),
             draw_word(image, "A", 10, 55), draw_word(image, "B", 175, 55)]
    source = tmp_path / "source.png"
    image.save(source)
    layout = make_layout({"A": [0, 0, 160, 220], "B": [160-overlap, 0, 320, 220]})
    request = WorkRequest(image_id="optional_heading", source=str(source), output_dir=str(tmp_path / "run"),
                          provider=Provider(base_url="http://fixture.invalid", model="synthetic", verified=True))
    monkeypatch.setattr("app.workflow.detect_text", lambda path: {"available": True, "boxes": words})
    actions = [("propose_layout", {"layout": layout.model_dump()}),
               ("inspect_crops", {"panel_ids": ["p_A", "p_B"]}), None]

    def fixture(self, messages, tools, force=None):
        if force:
            assert messages[0]["content"] == REVIEW_SYSTEM
            action = ("submit_review", {"accepted": True, "issues": []})
        else:
            assert messages[0]["content"] == SYSTEM
            action = actions.pop(0)
        calls = [] if action is None else [{"id": "fixture", "name": action[0], "arguments": json.dumps(action[1])}]
        return {"calls": calls, "native": {"role": "assistant", "content": "fixture"}, "text": ""}

    monkeypatch.setattr(Gateway, "invoke", fixture)
    with sqlite3.connect(":memory:", check_same_thread=False) as connection:
        flow = FigureWorkflow(request, SqliteSaver(connection))
        result = flow.graph.invoke({}, flow.config())
    assert result["status"] == "AUTO_ACCEPTED"
    assert all(issue["severity"] == "info" for issue in result["issues"])
    assert bool(result["issues"]) == bool(overlap)
    assert not list((tmp_path / "run").rglob("shared_*.png"))


def test_visual_acceptance_cannot_override_clipped_internal_text(monkeypatch, tmp_path):
    image = Image.new("RGB", (320, 220), "white")
    word = draw_word(image, "DOX+PD-1", 90, 80, size=24)
    source = tmp_path / "source.png"
    image.save(source)
    layout = make_layout({"A": [0, 0, 160, 220]})
    request = WorkRequest(image_id="clipped_axis", source=str(source), output_dir=str(tmp_path / "run"),
                          max_repairs=0, provider=Provider(base_url="http://fixture.invalid", model="synthetic", verified=True))
    monkeypatch.setattr("app.workflow.detect_text", lambda path: {"available": True, "boxes": [word]})
    actions = [("propose_layout", {"layout": layout.model_dump()}), None]

    def fixture(self, messages, tools, force=None):
        action = ("submit_review", {"accepted": True, "issues": []}) if force else actions.pop(0)
        calls = [] if action is None else [{"id": "fixture", "name": action[0], "arguments": json.dumps(action[1])}]
        return {"calls": calls, "native": {"role": "assistant", "content": "fixture"}, "text": ""}

    monkeypatch.setattr(Gateway, "invoke", fixture)
    with sqlite3.connect(":memory:", check_same_thread=False) as connection:
        flow = FigureWorkflow(request, SqliteSaver(connection))
        result = flow.graph.invoke({}, flow.config())
        assert "human" in flow.graph.get_state(flow.config()).next
    assert result["status"] != "AUTO_ACCEPTED"
    assert any(i["code"] == "TEXT_INTERSECTION" for i in result["issues"])


def test_resumed_checkpoint_receives_current_policy(monkeypatch, tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (320, 220), "white").save(source)
    request = WorkRequest(image_id="resume_policy", source=str(source), output_dir=str(tmp_path / "run"),
                          provider=Provider(base_url="http://fixture.invalid", model="synthetic", verified=True))

    def fixture(self, messages, *args):
        assert messages[0] == {"role": "system", "content": SYSTEM}
        assert messages[1]["content"] == "existing user feedback"
        return {"calls": [], "native": {"role": "assistant", "content": "fixture"}, "text": ""}

    monkeypatch.setattr(Gateway, "invoke", fixture)
    with sqlite3.connect(":memory:", check_same_thread=False) as connection:
        state = FigureWorkflow(request, SqliteSaver(connection)).agent({
            "messages": [{"role": "system", "content": "OLD mandatory shared title rule"},
                         {"role": "user", "content": "existing user feedback"}]})
    assert state["route"] == "human" and "error" not in state
