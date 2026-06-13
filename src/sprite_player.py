"""Sprite-sheet player widget. Plays the firmware's 20x20 pixel-art anims.

The player rotates through a caller-supplied list of animations, advancing
to the next entry every ROTATE_INTERVAL_MS, the same way the firmware
auto-cycles within a rate group. Each animation's per-frame timing comes
from the manifest (mirrors splash_*_holds[] in the firmware).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6.QtCore import Property, QRect, Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QLabel

ROTATE_INTERVAL_MS = 20_000

# Cropped (unscaled) frames keyed by animation slug, shared across ALL
# SpritePlayer instances — the alpha-bbox crop is identical regardless of the
# widget's render size (scaling happens per-frame in _show_frame), so many child
# mascots running the same animation don't each re-run the per-pixel bbox scan.
_FRAME_CACHE: dict[str, list[QPixmap]] = {}


def _alpha_bbox(img: QImage) -> QRect:
    """Tightest rectangle containing all non-transparent pixels."""
    w, h = img.width(), img.height()
    minx, miny, maxx, maxy = w, h, -1, -1
    for y in range(h):
        for x in range(w):
            if (img.pixel(x, y) >> 24) & 0xFF:
                if x < minx:
                    minx = x
                if y < miny:
                    miny = y
                if x > maxx:
                    maxx = x
                if y > maxy:
                    maxy = y
    if maxx < 0:
        return QRect(0, 0, w, h)
    return QRect(minx, miny, maxx - minx + 1, maxy - miny + 1)


def _square_alpha_bbox(images: list[QImage]) -> QRect:
    """Union alpha bbox across all frames, padded to a square (centered) so
    KeepAspectRatio scaling fills the target widget exactly."""
    union = QRect()
    for img in images:
        b = _alpha_bbox(img)
        union = b if union.isEmpty() else union.united(b)
    if union.isEmpty():
        return QRect(0, 0, images[0].width(), images[0].height())

    side = max(union.width(), union.height())
    src_w = images[0].width()
    src_h = images[0].height()
    cx = union.x() + union.width() // 2
    cy = union.y() + union.height() // 2
    x = cx - side // 2
    y = cy - side // 2
    # Clamp so we never crop outside the source frame.
    x = max(0, min(x, src_w - side))
    y = max(0, min(y, src_h - side))
    return QRect(x, y, side, side)


def assets_root() -> Path:
    """Locate assets/ whether running from source or a PyInstaller bundle."""
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        return Path(bundle_dir) / "assets"
    return Path(__file__).resolve().parents[1] / "assets"


class SpritePlayer(QLabel):
    """Cycles through a caller-supplied list of animations at native timing."""

    def __init__(self, size: int = 220, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignCenter)
        self._size = size

        sprites_dir = assets_root() / "sprites"
        manifest_path = sprites_dir / "manifest.json"
        if not manifest_path.exists():
            self.setText("(missing sprites)")
            self._has_sprites = False
            return
        self._has_sprites = True

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self._sprites_dir = sprites_dir
        self._anims: dict[str, dict] = manifest["animations"]

        self._active_key: str | None = None
        self._active_list: list[str] = []
        self._rotation_idx = 0

        self._cur_anim_name: str | None = None
        self._cur_frame_idx = 0
        self._cur_frames: list[QPixmap] = []
        self._cur_holds: list[int] = []

        self._frame_timer = QTimer(self)
        self._frame_timer.setSingleShot(True)
        self._frame_timer.timeout.connect(self._advance_frame)

        self._rotate_timer = QTimer(self)
        self._rotate_timer.setInterval(ROTATE_INTERVAL_MS)
        self._rotate_timer.timeout.connect(self._rotate)

    def set_anims(self, key: str, names: list[str]) -> None:
        """Switch to a new rotation list. If `key` is unchanged, keep state."""
        if not self._has_sprites or not names:
            return
        if key == self._active_key:
            return
        self._active_key = key
        self._active_list = [n for n in names if n in self._anims]
        if not self._active_list:
            return
        self._rotation_idx = 0
        self._start_anim(self._active_list[0])
        if len(self._active_list) > 1:
            self._rotate_timer.start()
        else:
            self._rotate_timer.stop()

    def current_anim(self) -> str | None:
        return self._cur_anim_name

    def set_size(self, px: int) -> None:
        """Change the render size and re-scale the current frame immediately.

        setFixedSize() alone is not enough: frames are scaled to self._size in
        _show_frame(), which otherwise only re-runs on the frame timer — so a
        widget that resizes between animation frames keeps painting the old
        size. Update _size here and re-show the current frame so the mascot
        tracks the new tile size at once (the shelf resizes tiles as the
        session count changes)."""
        if px == self._size:
            return
        self._size = px
        self.setFixedSize(px, px)
        if self._cur_frames:
            self._show_frame()

    def _get_render_size(self) -> int:
        return self._size

    def _set_render_size(self, px: int) -> None:
        self.set_size(int(px))

    # Animatable size, so the shelf can scale a mascot smoothly when the session
    # count (and thus the tile size) changes, instead of popping to the new size.
    renderSize = Property(int, _get_render_size, _set_render_size)

    def resume(self) -> None:
        """Restart the frame/rotation timers for the current animation after a
        stop(). set_anims() no-ops on an unchanged key, so a stopped sprite
        won't restart on its own when the same animation is re-selected — the
        dashboard pauses the hidden hero and needs this to wake it again."""
        if not self._has_sprites or not self._cur_frames:
            return
        self._show_frame()
        if len(self._active_list) > 1:
            self._rotate_timer.start()

    def _rotate(self) -> None:
        if not self._active_list:
            return
        self._rotation_idx = (self._rotation_idx + 1) % len(self._active_list)
        self._start_anim(self._active_list[self._rotation_idx])

    def _start_anim(self, name: str) -> None:
        meta = self._anims.get(name)
        if not meta:
            return
        self._cur_anim_name = name
        self._cur_frames = self._load_frames(meta)
        self._cur_holds = [f["hold_ms"] for f in meta["frames"]]
        self._cur_frame_idx = 0
        self._show_frame()

    def _load_frames(self, meta: dict) -> list[QPixmap]:
        """Load all frames in this animation, cropped to a single shared
        square bbox so the visible sprite fills the widget area instead of
        floating in a transparent margin.
        """
        slug = meta["slug"]
        if slug in _FRAME_CACHE:
            return _FRAME_CACHE[slug]

        images: list[QImage] = []
        for frame in meta["frames"]:
            p = self._sprites_dir / frame["file"]
            img = QImage(str(p))
            if not img.isNull():
                images.append(img.convertToFormat(QImage.Format_ARGB32))

        if not images:
            _FRAME_CACHE[slug] = []
            return []

        crop = _square_alpha_bbox(images)
        frames = [QPixmap.fromImage(img.copy(crop)) for img in images]
        _FRAME_CACHE[slug] = frames
        return frames

    def _show_frame(self) -> None:
        if not self._cur_frames:
            return
        pm = self._cur_frames[self._cur_frame_idx]
        scaled = pm.scaled(
            self._size, self._size,
            Qt.KeepAspectRatio,
            Qt.FastTransformation,
        )
        self.setPixmap(scaled)
        hold = max(1, self._cur_holds[self._cur_frame_idx])
        self._frame_timer.start(hold)

    def _advance_frame(self) -> None:
        if not self._cur_frames:
            return
        self._cur_frame_idx = (self._cur_frame_idx + 1) % len(self._cur_frames)
        self._show_frame()

    def stop(self) -> None:
        self._frame_timer.stop()
        self._rotate_timer.stop()
