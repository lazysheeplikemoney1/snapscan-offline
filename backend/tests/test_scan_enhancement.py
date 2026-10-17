"""Product scan enhancement must preserve tiny strokes and document geometry."""
import cv2
import numpy as np
import pytest

from app.services import enhance


def run(image, mode="clean"):
    assert callable(getattr(enhance, "enhance_scan", None)), "Product enhancement API is missing"
    return enhance.enhance_scan(image, mode)


def shaded_text():
    background = np.tile(np.linspace(100, 225, 640, dtype=np.float32), (200, 1))
    gray = background.copy()
    ink = np.zeros(gray.shape, dtype=np.uint8)
    for x in range(35, 610, 28):
        cv2.line(ink, (x, 50), (x, 145), 1, 1)
        cv2.line(ink, (x, 50), (x + 15, 50), 1, 1)
        cv2.line(ink, (x, 100), (x + 10, 100), 1, 1)
    gray[ink > 0] -= 22
    return cv2.cvtColor(gray.astype(np.uint8), cv2.COLOR_GRAY2BGR), ink.astype(bool)


@pytest.mark.parametrize("mode", ["color", "clean", "bw"])
def test_modes_preserve_size_dtype_and_do_not_modify_input(mode):
    image, _ = shaded_text()
    original = image.copy()
    result = run(image, mode)
    assert result.shape == image.shape
    assert result.dtype == np.uint8
    assert np.array_equal(image, original)
    assert not np.shares_memory(image, result)


def test_original_color_is_an_exact_color_preserving_choice():
    image = np.full((100, 180, 3), 210, dtype=np.uint8)
    cv2.circle(image, (60, 50), 28, (35, 45, 210), 3)
    assert np.array_equal(run(image, "color"), image)


def test_clean_flattens_gradient_shadow_and_keeps_one_pixel_strokes():
    image, ink = shaded_text()
    cleaned = cv2.cvtColor(run(image), cv2.COLOR_BGR2GRAY)
    before = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    # Blank rows measure illumination independently of text contrast.
    assert np.std(cleaned[10:30]) < np.std(before[10:30]) * .35
    xs = np.arange(35, 610, 28)
    contrast = cleaned[80, xs + 5].astype(float) - cleaned[80, xs].astype(float)
    assert np.percentile(contrast, 10) >= 18, "Faint single-pixel stems must not disappear"
    assert np.mean(cleaned[ink]) < np.mean(cleaned[~ink]) - 15


def test_clean_preserves_red_stamp_color():
    image = np.full((200, 360, 3), 175, dtype=np.uint8)
    cv2.circle(image, (180, 100), 60, (45, 55, 160), 3)
    stamp = (image[:, :, 2] - image[:, :, 0].astype(int)) > 70
    result = run(image, "clean").astype(int)
    assert np.median(result[:, :, 2][stamp] - result[:, :, 1][stamp]) > 80
    assert np.median(result[:, :, 2][stamp] - result[:, :, 0][stamp]) > 80


def test_bw_retains_thin_text_without_eroding_it():
    image, ink = shaded_text()
    result = run(image, "bw")[:, :, 0]
    assert set(np.unique(result)).issubset({0, 255})
    assert np.mean(result[ink] == 0) > .95
    assert np.mean(result[10:30] == 255) > .99


def test_bw_does_not_turn_faint_screen_texture_into_black_specks():
    rows, cols = np.mgrid[:200, :640]
    rng = np.random.default_rng(4)
    paper = 125 + cols / 640 * 70 + 4 * np.sin(cols * 2 * np.pi / 6) + 3 * np.sin(rows * 2 * np.pi / 8)
    paper += rng.normal(0, .8, paper.shape)
    gray = np.clip(paper, 0, 255).astype(np.uint8)
    ink = np.zeros(gray.shape, dtype=np.uint8)
    for x in range(35, 610, 28):
        cv2.line(ink, (x, 65), (x, 145), 1, 1)
    gray[ink > 0] -= 22
    result = run(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR), "bw")[:, :, 0]
    assert np.mean(result[10:40] == 255) > .98
    assert np.mean(result[ink > 0] == 0) > .95


@pytest.mark.parametrize("value", [0, 64, 180, 255])
@pytest.mark.parametrize("mode", ["color", "clean", "bw"])
def test_uniform_pages_do_not_gain_black_specks(value, mode):
    result = run(np.full((90, 150, 3), value, dtype=np.uint8), mode)
    assert np.max(np.std(result.astype(float), axis=(0, 1))) == 0
    if mode == "bw":
        assert np.all(result == 255)


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError):
        run(np.full((50, 80, 3), 200, dtype=np.uint8), "aggressive")


def test_quality_metrics_warn_without_replacing_or_rejecting_an_image():
    assert callable(getattr(enhance, "quality_metrics", None)), "Quality metadata API is missing"
    sharp = np.full((240, 360, 3), 225, dtype=np.uint8)
    cv2.putText(sharp, "Invoice 012345", (10, 100), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (20, 20, 20), 2)
    blurry = cv2.GaussianBlur(sharp, (21, 21), 5)
    first, second = enhance.quality_metrics(sharp), enhance.quality_metrics(blurry)
    assert first["blur_score"] > second["blur_score"]
    assert isinstance(second["is_blurry"], bool)
    assert isinstance(second["warnings"], list)
    assert second["quality_gate"] == "advisory_only"
