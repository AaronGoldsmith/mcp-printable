"""Tests for the server-side image compositing functions.

These test _tile_images and _side_by_side without requiring Blender.
"""

import base64
import io
import pytest
from PIL import Image


def _make_test_image(width: int = 100, height: int = 100, color: tuple = (255, 0, 0)) -> str:
    """Create a solid-color test image and return as base64 PNG."""
    img = Image.new('RGB', (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode('ascii')


# Import the compositing functions from server.py
from server import _tile_images, _side_by_side


class TestTileImages:
    """Test the multi-angle tiled render compositing."""

    def test_single_tile(self):
        renders = [{'label': 'iso', 'image': _make_test_image()}]
        result = _tile_images(renders, columns=1, tile_w=200, tile_h=200)

        assert result  # Non-empty base64
        img = Image.open(io.BytesIO(base64.b64decode(result)))
        assert img.width == 200
        assert img.height == 200 + 28  # tile + label

    def test_four_tiles_2x2(self):
        renders = [
            {'label': 'iso', 'image': _make_test_image(color=(255, 0, 0))},
            {'label': 'front', 'image': _make_test_image(color=(0, 255, 0))},
            {'label': 'right', 'image': _make_test_image(color=(0, 0, 255))},
            {'label': 'top', 'image': _make_test_image(color=(255, 255, 0))},
        ]
        result = _tile_images(renders, columns=2, tile_w=200, tile_h=200, label_height=28)

        img = Image.open(io.BytesIO(base64.b64decode(result)))
        assert img.width == 400   # 2 columns * 200
        assert img.height == 456  # 2 rows * (200 + 28)

    def test_three_tiles_default_columns(self):
        """3 tiles with columns=3 should produce a single row."""
        renders = [
            {'label': f'view{i}', 'image': _make_test_image()} for i in range(3)
        ]
        result = _tile_images(renders, columns=3, tile_w=100, tile_h=100)

        img = Image.open(io.BytesIO(base64.b64decode(result)))
        assert img.width == 300   # 3 columns * 100
        assert img.height == 128  # 1 row * (100 + 28)

    def test_five_tiles_wraps_to_two_rows(self):
        renders = [
            {'label': f'view{i}', 'image': _make_test_image()} for i in range(5)
        ]
        result = _tile_images(renders, columns=3, tile_w=100, tile_h=100)

        img = Image.open(io.BytesIO(base64.b64decode(result)))
        assert img.width == 300   # 3 columns
        assert img.height == 256  # 2 rows * (100 + 28)

    def test_empty_renders(self):
        result = _tile_images([], columns=3)
        assert result == ""

    def test_label_colors(self):
        """Verify the label bar has the expected dark background."""
        renders = [{'label': 'test', 'image': _make_test_image()}]
        result = _tile_images(renders, columns=1, tile_w=200, tile_h=200, label_height=28)

        img = Image.open(io.BytesIO(base64.b64decode(result)))
        # Check pixel at (5, 5) — should be in the label bar area (dark background)
        pixel = img.getpixel((5, 5))
        assert pixel[0] < 50 and pixel[1] < 50 and pixel[2] < 50  # Dark

    def test_tile_resizing(self):
        """Input images of different sizes should all be resized to tile dimensions."""
        small = _make_test_image(50, 50)
        large = _make_test_image(1000, 1000)
        renders = [
            {'label': 'small', 'image': small},
            {'label': 'large', 'image': large},
        ]
        result = _tile_images(renders, columns=2, tile_w=300, tile_h=300)

        img = Image.open(io.BytesIO(base64.b64decode(result)))
        assert img.width == 600
        assert img.height == 300 + 28


class TestSideBySide:
    """Test before/after comparison compositing."""

    def test_basic_side_by_side(self):
        before = _make_test_image(200, 200, color=(255, 0, 0))
        after = _make_test_image(200, 200, color=(0, 255, 0))

        result = _side_by_side(before, after, width=300, height=300)

        img = Image.open(io.BytesIO(base64.b64decode(result)))
        assert img.width == 600   # 2 * 300
        assert img.height == 328  # 300 + 28 label

    def test_labels_present(self):
        """Verify BEFORE and AFTER labels are in distinct color regions."""
        before = _make_test_image(100, 100, color=(128, 128, 128))
        after = _make_test_image(100, 100, color=(128, 128, 128))

        result = _side_by_side(before, after, width=200, height=200)
        img = Image.open(io.BytesIO(base64.b64decode(result)))

        # Left label bar should be dark
        left_label = img.getpixel((5, 5))
        assert left_label[0] < 50

        # Right label bar should also be dark
        right_label = img.getpixel((205, 5))
        assert right_label[0] < 50

    def test_images_resized(self):
        """Input images of any size should be resized to the target dimensions."""
        before = _make_test_image(50, 50)
        after = _make_test_image(800, 800)

        result = _side_by_side(before, after, width=400, height=400)
        img = Image.open(io.BytesIO(base64.b64decode(result)))
        assert img.width == 800
        assert img.height == 428
