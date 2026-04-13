from operator_use.computer.macos.desktop.config import BROWSER_BUNDLE_IDS, EXCLUDED_BUNDLE_IDS
from operator_use.computer.macos.desktop.views import DesktopState, Size, Window, Status
from operator_use.computer.macos.tree.views import BoundingBox, TreeElementNode
from operator_use.computer.macos.tree.service import Tree
from PIL import Image, ImageDraw, ImageFont, ImageGrab
from typing import Literal, Optional, Tuple, Union
from operator_use.computer.macos import ax
from time import perf_counter
import logging
import random
import io
import os

logger = logging.getLogger(__name__)


class Desktop:
    def __init__(
        self, use_vision: bool = False, use_annotation: bool = False, use_accessibility: bool = True
    ):
        self.use_vision = use_vision
        self.use_annotation = use_annotation
        self.use_accessibility = use_accessibility
        self.tree = Tree()
        self.desktop_state = None

    def get_screen_size(self) -> Size:
        """Return the virtual screen size (all displays combined) in logical points."""
        width, height = ax.GetScreenSize()
        return Size(width=width, height=height)

    def get_state(self, as_bytes: bool = False, scale: float = 1.0):
        start_time = perf_counter()
        windows = self.get_windows()
        active_window = self.get_foreground_window()

        if self.use_accessibility:
            tree_state = self.tree.get_state(active_window=active_window)
        else:
            from operator_use.computer.macos.tree.views import TreeState

            tree_state = TreeState()

        if self.use_vision:
            if self.use_annotation:
                nodes = tree_state.interactive_nodes if tree_state else []
                if nodes:
                    screenshot = self.get_annotated_screenshot(
                        nodes=nodes, as_bytes=as_bytes, scale=scale
                    )
                else:
                    screenshot = self.get_screenshot(as_bytes=as_bytes)
            else:
                screenshot = self.get_screenshot(as_bytes=as_bytes)
        else:
            screenshot = None

        self.desktop_state = DesktopState(
            active_window=active_window,
            windows=windows,
            screenshot=screenshot,
            tree_state=tree_state,
        )
        end_time = perf_counter()
        logger.info(f"[Desktop] Desktop State capture took {end_time - start_time:.2f} seconds")
        return self.desktop_state

    def execute_command(
        self,
        command: str,
        mode: Literal["shell", "osascript"] = "shell",
        timeout: int = 10,
    ) -> Tuple[str, int]:
        """Execute a shell or AppleScript command."""
        return ax.ExecuteCommand(command, mode=mode, timeout=timeout)

    def get_foreground_window(self) -> Optional[Window]:
        app = ax.GetFrontmostApplication()
        if app is None:
            return None
        window = app.MainWindow
        if window is None:
            return None
        is_browser = app.BundleIdentifier in BROWSER_BUNDLE_IDS
        rect = window.BoundingRectangle
        if rect:
            bounding_box = BoundingBox(
                left=int(rect.left),
                top=int(rect.top),
                right=int(rect.right),
                bottom=int(rect.bottom),
                width=int(rect.width),
                height=int(rect.height),
            )
        else:
            bounding_box = BoundingBox(left=0, top=0, right=0, bottom=0, width=0, height=0)
        status_str = app.Status
        try:
            status = Status(status_str)
        except ValueError:
            status = Status.ACTIVE
        return Window(
            name=window.Name,
            is_browser=is_browser,
            status=status,
            bounding_box=bounding_box,
            pid=app.PID,
            bundle_id=app.BundleIdentifier,
        )

    def get_windows(self) -> list[Window]:
        """
        Get list of user-facing application windows on the desktop.
        Uses the ax module's ApplicationControl API for all data.

        Returns:
            windows — list of Window objects
        """
        # Get all regular (Dock-visible) applications
        apps = ax.GetRunningApplications(policy="Regular")

        windows = []
        for app in apps:
            bundle_id = app.BundleIdentifier or ""
            if bundle_id in EXCLUDED_BUNDLE_IDS:
                continue

            app_name = app.Name or ""
            pid = app.PID
            is_browser = bundle_id in BROWSER_BUNDLE_IDS

            # Map ApplicationControl.Status to our Status enum
            status_str = app.Status  # 'Active', 'Fullscreen', 'Visible', etc.
            try:
                status = Status(status_str)
            except ValueError:
                status = Status.WINDOWLESS

            # Get bounding box from the main window (if any)
            if status in (Status.HIDDEN, Status.MINIMIZED, Status.WINDOWLESS):
                bbox = BoundingBox(left=0, top=0, right=0, bottom=0, width=0, height=0)
            else:
                main_window = app.MainWindow
                if main_window:
                    rect = main_window.BoundingRectangle
                    if rect:
                        bbox = BoundingBox(
                            left=int(rect.left),
                            top=int(rect.top),
                            right=int(rect.right),
                            bottom=int(rect.bottom),
                            width=int(rect.width),
                            height=int(rect.height),
                        )
                    else:
                        bbox = BoundingBox(left=0, top=0, right=0, bottom=0, width=0, height=0)
                else:
                    bbox = BoundingBox(left=0, top=0, right=0, bottom=0, width=0, height=0)

            windows.append(
                Window(
                    name=app_name,
                    is_browser=is_browser,
                    status=status,
                    bounding_box=bbox,
                    pid=pid,
                    bundle_id=bundle_id,
                )
            )

        return windows

    def get_screenshot(
        self,
        as_bytes: bool = False,
    ) -> Union[Image.Image, bytes, None]:
        """
        Capture a screenshot of the screen using Pillow ImageGrab.

        Args:
            as_bytes: If True, return PNG bytes.

        Returns:
            PIL Image, PNG bytes, or None on failure.
        """
        image = ImageGrab.grab(all_screens=True)
        if as_bytes:
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            return buf.getvalue()
        return image

    def get_annotated_screenshot(
        self,
        nodes: list[TreeElementNode],
        as_bytes: bool = False,
        scale: float = 1.0,
    ) -> Union[Image.Image, bytes, None]:
        """
        Take a screenshot and annotate it with numbered bounding boxes for each
        interactive element. Captures the screenshot internally. Mirrors Windows-MCP.

        Args:
            nodes: List of TreeElementNode (interactive_nodes from tree state).
            as_bytes: If True, return PNG bytes; otherwise return PIL Image.

        Returns:
            Annotated PIL Image, PNG bytes, or None on failure.
        """
        img = self.get_screenshot()
        if img is None:
            logger.warning(
                "Screenshot capture failed. Grant Screen Recording permission in System Settings > Privacy & Security."
            )
            return None
        padding = 5
        width = int(img.width + 1.5 * padding)
        height = int(img.height + 1.5 * padding)
        padded = Image.new("RGB", (width, height), color=(255, 255, 255))
        padded.paste(img, (padding, padding))

        draw = ImageDraw.Draw(padded)

        # Per-display geometry for accurate logical→pixel coordinate mapping.
        # ImageGrab stitches displays left-to-right in the combined image, each at
        # its native pixel resolution, so we accumulate pixel_widths to find each
        # display's pixel origin rather than using a single global scale factor.
        display_infos = ax.GetPerDisplayInfo()
        pixel_left_acc = 0
        for d in display_infos:
            d["pixel_left"] = pixel_left_acc
            pixel_left_acc += d["pixel_width"]

        virtual_left = display_infos[0]["logical_left"] if display_infos else 0
        virtual_top = min(d["logical_top"] for d in display_infos) if display_infos else 0

        def _find_display(lx: float, ly: float):
            for d in display_infos:
                if (
                    d["logical_left"] <= lx < d["logical_left"] + d["logical_width"]
                    and d["logical_top"] <= ly < d["logical_top"] + d["logical_height"]
                ):
                    return d
            return None

        def _logical_to_pixel(lx: float, ly: float) -> tuple[int, int]:
            d = _find_display(lx, ly)
            if d:
                px = d["pixel_left"] + int((lx - d["logical_left"]) * d["scale"])
                py = int((ly - d["logical_top"]) * d["scale"])
                return px, py
            # Fallback: use average scale across all displays
            avg_scale = img.width / max(sum(d["logical_width"] for d in display_infos), 1)
            return int((lx - virtual_left) * avg_scale), int((ly - virtual_top) * avg_scale)

        # Font sized to main display scale
        dpi_scale = display_infos[0]["scale"] if display_infos else ax.GetDPIScale()
        font_size = max(12, int(14 * dpi_scale))
        try:
            font_path = "/System/Library/Fonts/Helvetica.ttc"
            if not os.path.exists(font_path):
                font_path = "/System/Library/Fonts/Supplemental/Arial.ttf"
            font = ImageFont.truetype(font_path, font_size)
        except Exception:
            font = ImageFont.load_default()

        seen_boxes: set[tuple[int, int, int, int]] = set()

        def draw_annotation(label: int, node: TreeElementNode) -> None:
            box = node.bounding_box
            if box.width <= 0 or box.height <= 0:
                return
            box_key = (box.left, box.top, box.width, box.height)
            if box_key in seen_boxes:
                return
            seen_boxes.add(box_key)

            # Convert logical coordinates to pixel coordinates using per-display scale
            cx = (box.left + box.right) / 2
            cy = (box.top + box.bottom) / 2
            d = _find_display(cx, cy)
            if d:
                s = d["scale"]
                pl = d["pixel_left"]
                dl = d["logical_left"]
                dt = d["logical_top"]
                x1 = pl + int((box.left - dl) * s) + padding
                y1 = int((box.top - dt) * s) + padding
                x2 = pl + int((box.right - dl) * s) + padding
                y2 = int((box.bottom - dt) * s) + padding
            else:
                x1, y1 = _logical_to_pixel(box.left, box.top)
                x2, y2 = _logical_to_pixel(box.right, box.bottom)
                x1 += padding
                y1 += padding
                x2 += padding
                y2 += padding

            # Deterministic color per label
            random.seed(label)
            color = (
                random.randint(50, 255),
                random.randint(50, 255),
                random.randint(50, 255),
            )

            draw.rectangle([x1, y1, x2, y2], outline=color, width=2)

            label_text = str(label)
            try:
                left, top, right, bottom = draw.textbbox((0, 0), label_text, font=font)
                text_w, text_h = right - left, bottom - top
            except Exception:
                text_w, text_h = len(label_text) * 8, font_size

            # Label above box, or below if no room
            tag_x1 = x2 - text_w - 4
            tag_y1 = y1 - text_h - 4
            if tag_y1 < padding:
                tag_y1 = y2
            tag_x2 = tag_x1 + text_w + 4
            tag_y2 = tag_y1 + text_h + 4

            draw.rectangle([tag_x1, tag_y1, tag_x2, tag_y2], fill=color)
            draw.text((tag_x1 + 2, tag_y1 + 2), label_text, font=font, fill=(255, 255, 255))

        for i, node in enumerate(nodes):
            draw_annotation(i, node)

        if scale < 1.0 and scale > 0:
            new_w = max(1, int(padded.width * scale))
            new_h = max(1, int(padded.height * scale))
            padded = padded.resize((new_w, new_h), Image.Resampling.BILINEAR)

        if as_bytes:
            buf = io.BytesIO()
            padded.save(buf, format="PNG")
            return buf.getvalue()
        return padded
