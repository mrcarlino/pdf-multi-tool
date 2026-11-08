#!/usr/bin/env python3
import os
import subprocess
import sys
import ctypes
import threading
import time
import queue
import tkinter as tk
from tkinter import filedialog, messagebox
import tkinter.font as tkfont
from pypdf import PdfReader, PdfWriter, Transformation
import pymupdf as fitz  # PyMuPDF for visual previews

# Enable high-DPI awareness on Windows to prevent blurry GUI text
if sys.platform == "win32":
    try:
        # V2 awareness (Per-Monitor DPI Aware V2)
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            # V1 awareness (System DPI Aware)
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


class ModernProgressBar(tk.Canvas):
    """A custom, clean canvas-based progress bar matching the dark theme."""
    def __init__(self, master, bg_color="#181825", fill_color="#89b4fa", height=8, **kwargs):
        super().__init__(master, height=height, bg=bg_color, highlightthickness=0, **kwargs)
        self.fill_color = fill_color
        self.bg_color = bg_color
        self.progress = 0.0  # Float between 0.0 and 1.0
        self.bind("<Configure>", self._draw)

    def set_progress(self, val):
        self.progress = max(0.0, min(1.0, val))
        self._draw()

    def _draw(self, event=None):
        self.delete("all")
        width = self.winfo_width()
        height = self.winfo_height()
        
        # Draw background track
        self.create_rectangle(0, 0, width, height, fill=self.bg_color, width=0)
        
        # Draw completed progress fill
        fill_width = int(width * self.progress)
        if fill_width > 0:
            self.create_rectangle(0, 0, fill_width, height, fill=self.fill_color, width=0)


def format_file_size(bytes_size):
    """Format file size into human-readable units."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_size < 1024:
            return f"{bytes_size:.1f} {unit}"
            
        bytes_size /= 1024
    return f"{bytes_size:.1f} TB"


def parse_range_string(range_str, total_pages):
    """
    Parse a range string like '1-3, 5, 7-10' and return a list of 1-indexed page integers.
    Handles boundaries and raises ValueError with clear reasons for malformed inputs.
    """
    pages = set()
    parts = range_str.split(",")
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            try:
                start_str, end_str = part.split("-")
                start = int(start_str.strip())
                end = int(end_str.strip())
                if start <= 0 or end <= 0:
                    raise ValueError(f"Page numbers must be positive: '{part}'")
                if start <= end:
                    for p in range(start, end + 1):
                        if p <= total_pages:
                            pages.add(p)
                        else:
                            raise ValueError(f"Page number {p} exceeds document length ({total_pages}).")
                else:
                    for p in range(start, end - 1, -1):
                        if p <= total_pages:
                            pages.add(p)
                        else:
                            raise ValueError(f"Page number {p} exceeds document length ({total_pages}).")
            except ValueError as e:
                if "exceeds" in str(e) or "positive" in str(e):
                    raise
                raise ValueError(f"Invalid range format: '{part}'")
        else:
            try:
                p = int(part)
                if p <= 0:
                    raise ValueError(f"Page number must be positive: '{part}'")
                if p <= total_pages:
                    pages.add(p)
                else:
                    raise ValueError(f"Page number {p} exceeds document length ({total_pages}).")
            except ValueError as e:
                if "exceeds" in str(e) or "positive" in str(e):
                    raise
                raise ValueError(f"Invalid page number: '{part}'")
    return sorted(list(pages))


def run_pdf_transform(input_path, output_path, flip_h, flip_v, rotation, message_queue):
    """Worker function to flip and rotate PDF pages globally. Runs in a background thread."""
    try:
        reader = PdfReader(input_path)
        writer = PdfWriter()
        total_pages = len(reader.pages)
        
        for idx, page in enumerate(reader.pages):
            # Apply flip transformation first
            if flip_h or flip_v:
                left = float(page.mediabox.left)
                right = float(page.mediabox.right)
                bottom = float(page.mediabox.bottom)
                top = float(page.mediabox.top)
                
                sx = -1.0 if flip_h else 1.0
                sy = -1.0 if flip_v else 1.0
                
                tx = (left + right) if flip_h else 0.0
                ty = (bottom + top) if flip_v else 0.0
                
                op = Transformation().scale(sx=sx, sy=sy).translate(tx=tx, ty=ty)
                page.add_transformation(op)
                
            # Apply global rotation
            if rotation != 0:
                page.rotation = (page.rotation + rotation) % 360
                
            writer.add_page(page)
            message_queue.put(("progress", (idx + 1) / total_pages))
            
        with open(output_path, "wb") as f:
            writer.write(f)
            
        message_queue.put(("success", output_path))
    except Exception as e:
        message_queue.put(("error", str(e)))


def run_pdf_merge(input_paths, output_path, message_queue):
    """Worker function to merge multiple PDF files. Runs in a background thread."""
    try:
        writer = PdfWriter()
        total_files = len(input_paths)
        
        for idx, path in enumerate(input_paths):
            writer.append(path)
            message_queue.put(("progress", (idx + 1) / total_files))
            
        with open(output_path, "wb") as f:
            writer.write(f)
        writer.close()
        
        message_queue.put(("success", output_path))
    except Exception as e:
        message_queue.put(("error", str(e)))


def run_pdf_split(input_path, output_dir, mode, range_str, interval, message_queue):
    """Worker function to split a PDF file. Runs in a background thread."""
    try:
        reader = PdfReader(input_path)
        total_pages = len(reader.pages)
        base_name = os.path.splitext(os.path.basename(input_path))[0]
        
        if mode == "all":
            for idx in range(total_pages):
                writer = PdfWriter()
                writer.add_page(reader.pages[idx])
                out_path = os.path.join(output_dir, f"{base_name}_page_{idx + 1}.pdf")
                with open(out_path, "wb") as f:
                    writer.write(f)
                writer.close()
                message_queue.put(("progress", (idx + 1) / total_pages))
                
        elif mode == "range":
            pages_to_extract = parse_range_string(range_str, total_pages)
            if not pages_to_extract:
                raise ValueError("No pages specified in range selection.")
                
            writer = PdfWriter()
            for idx, page_num in enumerate(pages_to_extract):
                writer.add_page(reader.pages[page_num - 1])
                message_queue.put(("progress", (idx + 1) / len(pages_to_extract)))
                
            out_path = os.path.join(output_dir, f"{base_name}_extracted.pdf")
            with open(out_path, "wb") as f:
                writer.write(f)
            writer.close()
            
        elif mode == "interval":
            if interval <= 0:
                raise ValueError("Page interval must be a positive integer.")
            
            part_count = 0
            for start_idx in range(0, total_pages, interval):
                writer = PdfWriter()
                end_idx = min(start_idx + interval, total_pages)
                for idx in range(start_idx, end_idx):
                    writer.add_page(reader.pages[idx])
                
                part_count += 1
                out_path = os.path.join(output_dir, f"{base_name}_part_{part_count}.pdf")
                with open(out_path, "wb") as f:
                    writer.write(f)
                writer.close()
                
                message_queue.put(("progress", end_idx / total_pages))
                
        message_queue.put(("success", output_dir))
    except Exception as e:
        message_queue.put(("error", str(e)))


def run_pdf_reorder(input_path, output_path, page_order, message_queue):
    """Worker function to reorder, rotate, and flip PDF pages sequence-wise. Runs in a background thread."""
    try:
        reader = PdfReader(input_path)
        writer = PdfWriter()
        total_pages = len(page_order)
        
        for idx, item in enumerate(page_order):
            page_idx = item["idx"]
            rot = item["rotation"]
            fh = item["flip_h"]
            fv = item["flip_v"]
            
            # Fetch target page
            page = reader.pages[page_idx]
            
            # Apply page flip transformation
            if fh or fv:
                left = float(page.mediabox.left)
                right = float(page.mediabox.right)
                bottom = float(page.mediabox.bottom)
                top = float(page.mediabox.top)
                
                sx = -1.0 if fh else 1.0
                sy = -1.0 if fv else 1.0
                
                tx = (left + right) if fh else 0.0
                ty = (bottom + top) if fv else 0.0
                
                op = Transformation().scale(sx=sx, sy=sy).translate(tx=tx, ty=ty)
                page.add_transformation(op)
                
            # Apply page rotation
            if rot != 0:
                page.rotation = (page.rotation + rot) % 360
                
            writer.add_page(page)
            message_queue.put(("progress", (idx + 1) / total_pages))
            
        with open(output_path, "wb") as f:
            writer.write(f)
        writer.close()
        
        message_queue.put(("success", output_path))
    except Exception as e:
        message_queue.put(("error", str(e)))


def run_img_to_pdf(image_paths, output_path, message_queue):
    """Worker thread function to convert multiple images to a single PDF."""
    try:
        doc = fitz.open()
        total = len(image_paths)
        for idx, img_path in enumerate(image_paths):
            if not os.path.exists(img_path):
                raise FileNotFoundError(f"Image not found: {img_path}")
            
            imgdoc = fitz.open(img_path)
            pdfbytes = imgdoc.convert_to_pdf()
            imgpdf = fitz.open("pdf", pdfbytes)
            doc.insert_pdf(imgpdf)
            imgdoc.close()
            imgpdf.close()
            
            message_queue.put(("progress", (idx + 1) / total))
            
        doc.save(output_path)
        doc.close()
        message_queue.put(("success", output_path))
    except Exception as e:
        message_queue.put(("error", str(e)))


def run_pdf_compress(input_path, output_path, level, message_queue):
    """Worker function to compress a PDF. Runs in a background thread."""
    try:
        doc = fitz.open(input_path)
        
        # 1. Image Optimization
        if level == 1:
            pass
        elif level == 2:
            doc.rewrite_images(dpi_threshold=151, dpi_target=150, quality=75)
        elif level == 3:
            doc.rewrite_images(dpi_threshold=73, dpi_target=72, quality=50)
            
        message_queue.put(("progress", 0.5))
        
        # 2. Scrub metadata, thumbnails, etc.
        doc.scrub(
            metadata=True,
            xml_metadata=True,
            attached_files=True,
            thumbnails=True,
            reset_fields=True
        )
        
        message_queue.put(("progress", 0.8))
        
        # 3. Save with garbage collection and deflation
        doc.save(
            output_path,
            garbage=4,
            deflate=True
        )
        doc.close()
        
        message_queue.put(("success", output_path))
    except Exception as e:
        message_queue.put(("error", str(e)))


def run_pdf_edit(input_path, output_path, placed_items, message_queue):
    """Worker function to apply signatures and redactions to PDF. Runs in a background thread."""
    temp_files_to_cleanup = []
    try:
        # 1. Compile signature pad strokes to transparent PNGs
        sig_pngs = {}
        for idx, item in enumerate(placed_items):
            if item["type"] == "signature":
                strokes = item["strokes"]
                all_x = [pt[0] for stroke in strokes for pt in stroke]
                all_y = [pt[1] for stroke in strokes for pt in stroke]
                if not all_x or not all_y:
                    continue
                min_x, max_x = min(all_x), max(all_x)
                min_y, max_y = min(all_y), max(all_y)
                w = max(1.0, max_x - min_x)
                h = max(1.0, max_y - min_y)
                
                temp_doc = fitz.open()
                padding = 2
                temp_page = temp_doc.new_page(width=w + 2*padding, height=h + 2*padding)
                shape = temp_page.new_shape()
                for stroke in strokes:
                    if len(stroke) < 2:
                        continue
                    points = [fitz.Point(pt[0] - min_x + padding, pt[1] - min_y + padding) for pt in stroke]
                    shape.draw_polyline(points)
                shape.finish(color=(0.0, 0.2, 0.6), width=2.5, lineCap=1, lineJoin=1)
                shape.commit()
                
                pix = temp_page.get_pixmap(alpha=True)
                temp_png_path = f"temp_sig_{idx}_{os.getpid()}.png"
                pix.save(temp_png_path)
                temp_files_to_cleanup.append(temp_png_path)
                sig_pngs[idx] = temp_png_path
                temp_doc.close()
                
        message_queue.put(("progress", 0.2))
        
        # 2. Open source PDF and apply changes
        doc = fitz.open(input_path)
        total_pages = len(doc)
        
        for page_idx in range(total_pages):
            page = doc.load_page(page_idx)
            page_items = [item for item in placed_items if item["page"] == page_idx]
            
            # 2a. Place signatures
            for idx, item in enumerate(placed_items):
                if item["page"] == page_idx and item["type"] == "signature":
                    png_path = sig_pngs.get(idx)
                    if png_path and os.path.exists(png_path):
                        page.insert_image(item["rect"], filename=png_path)
            
            # 2b. Place redactions
            redact_count = 0
            for item in page_items:
                if item["type"] == "redaction":
                    page.add_redact_annot(item["rect"], fill=(0, 0, 0))
                    redact_count += 1
            
            if redact_count > 0:
                page.apply_redactions()
                
            message_queue.put(("progress", 0.2 + 0.6 * ((page_idx + 1) / total_pages)))
            
        # 3. Save output PDF
        doc.save(
            output_path,
            garbage=4,
            deflate=True
        )
        doc.close()
        
        message_queue.put(("success", output_path))
        
    except Exception as e:
        message_queue.put(("error", str(e)))
    finally:
        for path in temp_files_to_cleanup:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass


class PDFFlipperApp:
    def __init__(self, root):
        self.root = root
        self.root.title("PDF Multi-Tool")
        
        # Determine window dimensions based on screen resolution
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        
        # Occupy 75% of screen width and height as fallback size
        width = int(screen_width * 0.75)
        height = int(screen_height * 0.75)
        width = max(1024, width)
        height = max(768, height)
        
        # Calculate coordinates to center window on screen
        x = (screen_width - width) // 2
        y = (screen_height - height) // 2
        
        if sys.platform == "win32":
            self.root.geometry(f"{width}x{height}+{x}+{y}")
        else:
            # X11 reports all monitors as one screen at a fixed 96 DPI, so maximize on the
            # current monitor, then derive the UI scale from the real window width.
            self.root.attributes("-zoomed", True)
            self.root.update()
            time.sleep(0.1)
            self.root.update()
            scale = float(os.environ.get("TK_SCALING") or min(3.0, max(1.0, (self.root.winfo_width() - 80) / 900)))
            self.root.tk.call("tk", "scaling", scale)
        
        self.root.minsize(800, 600)
        
        # Open Maximized on Windows
        try:
            self.root.state('zoomed')
        except Exception:
            pass
        
        # Color Palette - Catppuccin Mocha Theme (Dark)
        self.BG_COLOR = "#1e1e2e"
        self.CARD_COLOR = "#252538"
        self.TEXT_COLOR = "#cdd6f4"
        self.MUTED_COLOR = "#a6adc8"
        self.ACCENT_COLOR = "#89b4fa"      # Pastel Blue
        self.ACCENT_HOVER = "#b4befe"      # Pastel Lavender
        self.SUCCESS_COLOR = "#a6e3a1"     # Pastel Green
        self.ERROR_COLOR = "#f38ba8"       # Pastel Red
        self.INPUT_BG = "#181825"
        self.INPUT_BORDER = "#313244"
        
        # Configure window background
        self.root.config(bg=self.BG_COLOR)
        
        # Font setup with fallback checks
        available_fonts = tkfont.families()
        self.font_family = "Segoe UI" if "Segoe UI" in available_fonts else ("Arial" if "Arial" in available_fonts else "sans-serif")
        
        # App state variables
        self.current_tab = "merge"  # "merge", "split", "reorder", "transform"
        self.is_processing = False
        self.msg_queue = queue.Queue()
        
        # State: Transform PDF (Document-Wide Operations)
        self.input_file_path = tk.StringVar(value="")
        self.output_file_path = tk.StringVar(value="")
        self.flip_horizontal = tk.BooleanVar(value=False)
        self.flip_vertical = tk.BooleanVar(value=False)
        self.transform_rotate = tk.IntVar(value=0)  # Rotation: 0, 90, 180, 270
        self.pdf_pages = 0
        self.pdf_size_str = ""
        
        # State: Merge PDFs
        self.merge_files = []  # List of dicts: [{"path": str, "pages": int, "size": int}]
        self.merge_output_path = tk.StringVar(value="")
        
        # State: Split PDF
        self.split_input_path = tk.StringVar(value="")
        self.split_output_dir = tk.StringVar(value="")
        self.split_mode = tk.StringVar(value="all")  # "all", "range", "interval"
        self.split_range_str = tk.StringVar(value="")
        self.split_interval = tk.IntVar(value=2)
        self.split_pdf_pages = 0
        self.split_pdf_size_str = ""
        
        # State: Reorder Pages (Page-Specific Operations)
        self.reorder_input_path = tk.StringVar(value="")
        self.reorder_output_path = tk.StringVar(value="")
        self.reorder_pages_list = []  # List of page dicts: [{"idx": int, "rotation": int, "flip_h": bool, "flip_v": bool}]
        self.reorder_pdf_pages = 0
        self.reorder_pdf_size_str = ""
        
        # State: Images to PDF
        self.img_to_pdf_files = []          # List of image file paths
        self.img_to_pdf_save_path = tk.StringVar(value="")
        
        # State: Compress PDF
        self.compress_input_path = tk.StringVar(value="")
        self.compress_save_path = tk.StringVar(value="")
        self.compress_pdf_pages = 0
        self.compress_pdf_size_bytes = 0
        self.compress_level = tk.IntVar(value=2)  # 1 = Low, 2 = Medium, 3 = High
        
        # State: Sign & Redact
        self.edit_input_path = tk.StringVar(value="")
        self.edit_save_path = tk.StringVar(value="")
        self.edit_pdf_pages = 0
        self.edit_pdf_size_str = ""
        self.edit_active_tool = "pan"       # "pan", "sign", "redact"
        self.signature_strokes = []         # List of stroke lists: [[(x1,y1), ...], ...]
        self.placed_items = []              # List of placed overlay dicts
        
        # State: Visual layout tracking (for coordinate mapping)
        self.preview_x = 0
        self.preview_y = 0
        self.preview_w = 0
        self.preview_h = 0
        self.preview_scale = 1.0
        
        # Edit selection and resize state
        self.active_edit_item = None        # Reference to a placed item dict
        self.is_resizing = False
        self.drag_start = (0, 0)
        self.redact_start = None
        
        # State: Unified Document Previewer
        self.preview_doc = None             # fitz.Document
        self.preview_file_path = ""         # path to loaded document
        self.preview_page_idx = 0           # current page index (0-indexed)
        self.preview_zoom_factor = 1.0      # zoom multiplier
        self.preview_thumbnails = {}        # cache: {page_idx: PhotoImage}
        
        # State: Status Messaging
        self.status_message = tk.StringVar(value="Select a PDF file to begin.")
        
        # Bind delete key for deleting selected items
        self.root.bind("<Delete>", self.on_delete_key)
        
        # Build UI
        self.setup_ui()
        self.switch_tab("merge")

    def setup_ui(self):
        # Main container with padding
        main_frame = tk.Frame(self.root, bg=self.BG_COLOR, padx=20, pady=12)
        main_frame.pack(fill="both", expand=True)

        # 55/45 Grid Configuration
        main_frame.columnconfigure(0, weight=55)
        main_frame.columnconfigure(1, weight=45)
        main_frame.rowconfigure(0, weight=1)

        # Left Column Frame (Controls Panel)
        left_panel = tk.Frame(main_frame, bg=self.BG_COLOR)
        left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 15))

        # 1. Header Banner
        header_frame = tk.Frame(left_panel, bg=self.BG_COLOR)
        header_frame.pack(fill="x", pady=(0, 10))
        
        title_lbl = tk.Label(
            header_frame, 
            text="📄 PDF Multi-Tool", 
            fg=self.ACCENT_COLOR, 
            bg=self.BG_COLOR,
            font=(self.font_family, 18, "bold")
        )
        title_lbl.pack(anchor="w")
        
        subtitle_lbl = tk.Label(
            header_frame, 
            text="Transform files, merge documents, split pages, or reorder/rotate/flip pages in a beautiful workspace.", 
            fg=self.MUTED_COLOR, 
            bg=self.BG_COLOR,
            font=(self.font_family, 9)
        )
        subtitle_lbl.pack(anchor="w", pady=(2, 0))

        # 2. Custom Tabs Bar
        self.tab_frame = tk.Frame(left_panel, bg=self.BG_COLOR)
        self.tab_frame.pack(fill="x", pady=(0, 8))
        
        self.merge_tab_btn = tk.Button(
            self.tab_frame, text="🔗 Merge PDFs", command=lambda: self.switch_tab("merge"),
            bd=0, relief="flat", font=(self.font_family, 10, "bold"), padx=8, pady=5, cursor="hand2"
        )

        self.split_tab_btn = tk.Button(
            self.tab_frame, text="✂️ Split PDF", command=lambda: self.switch_tab("split"),
            bd=0, relief="flat", font=(self.font_family, 10, "bold"), padx=8, pady=5, cursor="hand2"
        )

        self.reorder_tab_btn = tk.Button(
            self.tab_frame, text="🔄 Reorder Pages", command=lambda: self.switch_tab("reorder"),
            bd=0, relief="flat", font=(self.font_family, 10, "bold"), padx=8, pady=5, cursor="hand2"
        )

        self.flip_tab_btn = tk.Button(
            self.tab_frame, text="↔️ Transform PDF", command=lambda: self.switch_tab("transform"),
            bd=0, relief="flat", font=(self.font_family, 10, "bold"), padx=8, pady=5, cursor="hand2"
        )

        self.img_to_pdf_tab_btn = tk.Button(
            self.tab_frame, text="🖼️ Images to PDF", command=lambda: self.switch_tab("img_to_pdf"),
            bd=0, relief="flat", font=(self.font_family, 10, "bold"), padx=8, pady=5, cursor="hand2"
        )

        self.compress_tab_btn = tk.Button(
            self.tab_frame, text="🗜️ Compress PDF", command=lambda: self.switch_tab("compress"),
            bd=0, relief="flat", font=(self.font_family, 10, "bold"), padx=8, pady=5, cursor="hand2"
        )

        self.edit_tab_btn = tk.Button(
            self.tab_frame, text="✍️ Sign & Redact", command=lambda: self.switch_tab("edit"),
            bd=0, relief="flat", font=(self.font_family, 10, "bold"), padx=8, pady=5, cursor="hand2"
        )

        # Two rows of equal-width tabs so the left panel stays narrow
        for i, name in enumerate(["merge", "split", "reorder", "flip", "img_to_pdf", "compress", "edit"]):
            getattr(self, name + "_tab_btn").grid(row=i // 4, column=i % 4, sticky="ew", padx=(0, 5), pady=(0, 5))
        for col in range(4):
            self.tab_frame.columnconfigure(col, weight=1, uniform="tabs")

        # 3. Content Frames
        # 3a. Transform Tab Content (previously Flip UI)
        self.flip_content_frame = tk.Frame(left_panel, bg=self.BG_COLOR)
        self.build_transform_ui()
        
        # 3b. Merge Tab Content
        self.merge_content_frame = tk.Frame(left_panel, bg=self.BG_COLOR)
        self.build_merge_ui()

        # 3c. Split Tab Content
        self.split_content_frame = tk.Frame(left_panel, bg=self.BG_COLOR)
        self.build_split_ui()

        # 3d. Reorder Tab Content
        self.reorder_content_frame = tk.Frame(left_panel, bg=self.BG_COLOR)
        self.build_reorder_ui()

        # 3e. Images to PDF Content
        self.img_to_pdf_content_frame = tk.Frame(left_panel, bg=self.BG_COLOR)
        self.build_img_to_pdf_ui()

        # 3f. Compress Content
        self.compress_content_frame = tk.Frame(left_panel, bg=self.BG_COLOR)
        self.build_compress_ui()

        # 3g. Sign & Redact Content
        self.edit_content_frame = tk.Frame(left_panel, bg=self.BG_COLOR)
        self.build_edit_ui()

        # 4. Progress and Shared Action Button Area (bottom of Left Panel)
        bottom_frame = tk.Frame(left_panel, bg=self.BG_COLOR)
        bottom_frame.pack(fill="x", side="bottom", pady=(10, 0))
        
        # Status details Label
        self.status_lbl = tk.Label(
            bottom_frame,
            textvariable=self.status_message,
            fg=self.MUTED_COLOR,
            bg=self.BG_COLOR,
            font=(self.font_family, 10)
        )
        self.status_lbl.pack(anchor="w", pady=(0, 6))
        
        # Custom modern progress bar
        self.progress_bar = ModernProgressBar(bottom_frame, bg_color=self.INPUT_BG, fill_color=self.ACCENT_COLOR)
        self.progress_bar.pack(fill="x", pady=(0, 8))
        
        # Action Button row
        action_row = tk.Frame(bottom_frame, bg=self.BG_COLOR)
        action_row.pack(fill="x")
        
        self.open_folder_btn = self.create_button(
            action_row,
            text="📂 Open Output Folder",
            command=self.open_output_folder,
            bg_color=self.CARD_COLOR,
            fg_color=self.TEXT_COLOR,
            hover_color=self.INPUT_BORDER
        )
        self.open_folder_btn.pack_forget()
        
        self.action_btn = self.create_button(
            action_row, 
            text="MERGE PDFs", 
            command=self.start_merging,
            bg_color=self.SUCCESS_COLOR,
            fg_color="#11111b",
            hover_color="#c2f0c2"
        )
        self.action_btn.pack(side="right")
        self.action_btn.config(state="disabled")

        # Right Column Frame (Unified Previewer Panel)
        right_panel = tk.Frame(main_frame, bg=self.CARD_COLOR, padx=15, pady=15)
        right_panel.grid(row=0, column=1, sticky="nsew")

        # Visual Page Preview Header Row
        header_row = tk.Frame(right_panel, bg=self.CARD_COLOR)
        header_row.pack(fill="x", pady=(0, 8))
        
        preview_header = tk.Label(
            header_row, text="👁️ Document Preview", fg=self.TEXT_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 11, "bold")
        )
        preview_header.pack(side="left")
        
        # Zoom controls
        self.zoom_lbl = tk.Label(
            header_row, text="100%", fg=self.MUTED_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 9, "bold")
        )
        self.zoom_lbl.pack(side="right", padx=(5, 0))
        
        self.zoom_reset_btn = self.create_button(
            header_row, text="🔄 Fit", command=self.zoom_reset,
            bg_color=self.CARD_COLOR, fg_color=self.TEXT_COLOR, hover_color=self.INPUT_BORDER
        )
        self.zoom_reset_btn.pack(side="right", padx=(5, 0))
        
        self.zoom_in_btn = self.create_button(
            header_row, text=" + ", command=lambda: self.zoom_preview(1.2),
            bg_color=self.CARD_COLOR, fg_color=self.TEXT_COLOR, hover_color=self.INPUT_BORDER
        )
        self.zoom_in_btn.pack(side="right", padx=(5, 0))
        
        self.zoom_out_btn = self.create_button(
            header_row, text=" − ", command=lambda: self.zoom_preview(1/1.2),
            bg_color=self.CARD_COLOR, fg_color=self.TEXT_COLOR, hover_color=self.INPUT_BORDER
        )
        self.zoom_out_btn.pack(side="right", padx=(10, 0))
        
        # Page Navigation toolbar
        self.next_page_btn = self.create_button(
            header_row, text=" ▶ ", command=self.go_to_next_page,
            bg_color=self.CARD_COLOR, fg_color=self.TEXT_COLOR, hover_color=self.INPUT_BORDER
        )
        self.next_page_btn.pack(side="right", padx=(5, 0))
        
        self.page_nav_lbl = tk.Label(
            header_row, text="Page 0 of 0", fg=self.TEXT_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 9, "bold")
        )
        self.page_nav_lbl.pack(side="right", padx=(5, 0))
        
        self.prev_page_btn = self.create_button(
            header_row, text=" ◀ ", command=self.go_to_prev_page,
            bg_color=self.CARD_COLOR, fg_color=self.TEXT_COLOR, hover_color=self.INPUT_BORDER
        )
        self.prev_page_btn.pack(side="right")
        
        # Preview container Frame
        self.preview_container = tk.Frame(
            right_panel, bg=self.INPUT_BG, highlightthickness=1, highlightbackground=self.INPUT_BORDER
        )
        self.preview_container.pack(fill="both", expand=True)
        
        # Canvas with native drag panning
        self.preview_canvas = tk.Canvas(
            self.preview_container, bg=self.INPUT_BG, highlightthickness=0, cursor="hand2"
        )
        self.preview_canvas.pack(fill="both", expand=True)
        
        # Bind events for resizing, panning and scroll zooming
        self.preview_canvas.bind("<Configure>", self.on_preview_resize)
        self.preview_canvas.bind("<ButtonPress-1>", self.on_pan_start)
        self.preview_canvas.bind("<B1-Motion>", self.on_pan_drag)
        self.preview_canvas.bind("<ButtonRelease-1>", self.on_pan_stop)
        self.preview_canvas.bind("<MouseWheel>", self.on_mousewheel)

    def build_transform_ui(self):
        """Build Transform PDF UI (previously Flip UI, now with Rotation)."""
        # Input File Card Frame
        file_card = tk.Frame(self.flip_content_frame, bg=self.CARD_COLOR, padx=15, pady=15, bd=0)
        file_card.pack(fill="x", pady=(0, 15))
        
        file_header_lbl = tk.Label(
            file_card, text="📄 Input PDF Document", fg=self.TEXT_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 11, "bold")
        )
        file_header_lbl.pack(anchor="w", pady=(0, 8))
        
        file_row = tk.Frame(file_card, bg=self.CARD_COLOR)
        file_row.pack(fill="x")
        
        self.file_entry = tk.Entry(
            file_row,
            textvariable=self.input_file_path,
            state="readonly",
            readonlybackground=self.INPUT_BG,
            fg=self.TEXT_COLOR,
            bd=0,
            highlightthickness=1,
            highlightbackground=self.INPUT_BORDER,
            highlightcolor=self.ACCENT_COLOR,
            font=(self.font_family, 10),
            width=40
        )
        self.file_entry.pack(side="left", fill="x", expand=True, padx=(0, 10), ipady=5)
        
        self.select_btn = self.create_button(
            file_row, text="Browse...", command=self.browse_input_file,
            bg_color=self.ACCENT_COLOR, fg_color="#11111b", hover_color=self.ACCENT_HOVER
        )
        self.select_btn.pack(side="right")
        
        self.file_details_lbl = tk.Label(
            file_card, text="No PDF file loaded.", fg=self.MUTED_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 9, "italic")
        )
        self.file_details_lbl.pack(anchor="w", pady=(6, 0))

        # Operations Card Frame (Settings)
        settings_card = tk.Frame(self.flip_content_frame, bg=self.CARD_COLOR, padx=15, pady=15)
        settings_card.pack(fill="x", pady=(0, 15))
        
        settings_header_lbl = tk.Label(
            settings_card, text="⚙️ Transformation Options (Document-Wide)", fg=self.TEXT_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 11, "bold")
        )
        settings_header_lbl.pack(anchor="w", pady=(0, 8))
        
        # Mirror Checkboxes
        checkbox_row = tk.Frame(settings_card, bg=self.CARD_COLOR)
        checkbox_row.pack(fill="x", pady=(0, 10))
        
        self.cb_horiz = tk.Checkbutton(
            checkbox_row, text="Horizontal Mirror (Left-Right)", variable=self.flip_horizontal,
            bg=self.CARD_COLOR, fg=self.TEXT_COLOR, selectcolor=self.INPUT_BG,
            activebackground=self.CARD_COLOR, activeforeground=self.TEXT_COLOR,
            font=(self.font_family, 10), cursor="hand2", padx=10, command=self.on_flip_option_changed
        )
        self.cb_horiz.pack(side="left", padx=(0, 20))
        
        self.cb_vert = tk.Checkbutton(
            checkbox_row, text="Vertical Mirror (Top-Bottom)", variable=self.flip_vertical,
            bg=self.CARD_COLOR, fg=self.TEXT_COLOR, selectcolor=self.INPUT_BG,
            activebackground=self.CARD_COLOR, activeforeground=self.TEXT_COLOR,
            font=(self.font_family, 10), cursor="hand2", padx=10, command=self.on_flip_option_changed
        )
        self.cb_vert.pack(side="left")

        # Document Rotation Selection (Flat Buttons Row)
        rotate_row = tk.Frame(settings_card, bg=self.CARD_COLOR)
        rotate_row.pack(fill="x", pady=(10, 0))
        
        rotate_lbl = tk.Label(
            rotate_row, text="Rotate Document:", fg=self.TEXT_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 10, "bold")
        )
        rotate_lbl.pack(side="left", padx=(0, 15))
        
        self.rot_buttons = []
        angles = [0, 270, 90, 180]
        labels = ["Normal", "Rotate Left ↺", "Rotate Right ↻", "Upside Down"]
        for angle, lbl in zip(angles, labels):
            btn = tk.Button(
                rotate_row, text=lbl, bd=0, relief="flat", padx=12, pady=4, cursor="hand2",
                font=(self.font_family, 9, "bold")
            )
            btn.config(command=lambda a=angle, b=btn: self.set_transform_rotation(a, b))
            btn.pack(side="left", padx=(0, 5))
            self.rot_buttons.append((angle, btn))
            
        self.set_transform_rotation(0, self.rot_buttons[0][1])

        # Save Location Card Frame
        save_card = tk.Frame(self.flip_content_frame, bg=self.CARD_COLOR, padx=15, pady=15)
        save_card.pack(fill="x")
        
        save_header_lbl = tk.Label(
            save_card, text="💾 Save Destination", fg=self.TEXT_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 11, "bold")
        )
        save_header_lbl.pack(anchor="w", pady=(0, 8))
        
        save_row = tk.Frame(save_card, bg=self.CARD_COLOR)
        save_row.pack(fill="x")
        
        self.save_entry = tk.Entry(
            save_row,
            textvariable=self.output_file_path,
            state="readonly",
            readonlybackground=self.INPUT_BG,
            fg=self.TEXT_COLOR,
            bd=0,
            highlightthickness=1,
            highlightbackground=self.INPUT_BORDER,
            highlightcolor=self.ACCENT_COLOR,
            font=(self.font_family, 10),
            width=40
        )
        self.save_entry.pack(side="left", fill="x", expand=True, padx=(0, 10), ipady=5)
        
        self.save_btn = self.create_button(
            save_row, text="Change...", command=self.browse_output_file,
            bg_color=self.ACCENT_COLOR, fg_color="#11111b", hover_color=self.ACCENT_HOVER
        )
        self.save_btn.pack(side="right")
        self.save_btn.config(state="disabled")

    def set_transform_rotation(self, angle, button):
        """Set global transformation rotation and update visual button states."""
        self.transform_rotate.set(angle)
        for ang, btn in self.rot_buttons:
            if ang == angle:
                btn.config(bg=self.ACCENT_COLOR, fg="#11111b")
            else:
                btn.config(bg=self.INPUT_BG, fg=self.TEXT_COLOR)
        self.on_flip_option_changed()

    def build_merge_ui(self):
        # Files List Card Frame
        merge_card = tk.Frame(self.merge_content_frame, bg=self.CARD_COLOR, padx=15, pady=15)
        merge_card.pack(fill="both", expand=True, pady=(0, 15))
        
        list_header_lbl = tk.Label(
            merge_card, text="📋 PDFs to Merge (order from top to bottom)", fg=self.TEXT_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 11, "bold")
        )
        list_header_lbl.pack(anchor="w", pady=(0, 8))
        
        list_row = tk.Frame(merge_card, bg=self.CARD_COLOR)
        list_row.pack(fill="both", expand=True)
        
        list_container = tk.Frame(list_row, bg=self.CARD_COLOR)
        list_container.pack(side="left", fill="both", expand=True, padx=(0, 12))
        
        self.merge_listbox = tk.Listbox(
            list_container,
            bg=self.INPUT_BG,
            fg=self.TEXT_COLOR,
            selectbackground=self.ACCENT_COLOR,
            selectforeground="#11111b",
            bd=0,
            highlightthickness=1,
            highlightbackground=self.INPUT_BORDER,
            highlightcolor=self.ACCENT_COLOR,
            font=(self.font_family, 10),
            selectmode="browse",
            height=6
        )
        self.merge_listbox.pack(side="left", fill="both", expand=True)
        self.merge_listbox.bind("<<ListboxSelect>>", self.on_merge_select)
        
        scrollbar = tk.Scrollbar(list_container, orient="vertical", command=self.merge_listbox.yview, bg=self.CARD_COLOR, bd=0)
        scrollbar.pack(side="right", fill="y")
        self.merge_listbox.config(yscrollcommand=scrollbar.set)
        
        controls_frame = tk.Frame(list_row, bg=self.CARD_COLOR)
        controls_frame.pack(side="right", fill="y")
        
        self.add_files_btn = self.create_button(
            controls_frame, text="➕ Add PDF(s)...", command=self.add_merge_files,
            bg_color=self.ACCENT_COLOR, fg_color="#11111b", hover_color=self.ACCENT_HOVER
        )
        self.add_files_btn.pack(fill="x", pady=(0, 5))
        
        self.remove_file_btn = self.create_button(
            controls_frame, text="➖ Remove", command=self.remove_merge_file,
            bg_color=self.CARD_COLOR, fg_color=self.TEXT_COLOR, hover_color=self.INPUT_BORDER
        )
        self.remove_file_btn.pack(fill="x", pady=(0, 5))
        
        self.move_up_btn = self.create_button(
            controls_frame, text="⬆️ Move Up", command=lambda: self.move_merge_file(-1),
            bg_color=self.CARD_COLOR, fg_color=self.TEXT_COLOR, hover_color=self.INPUT_BORDER
        )
        self.move_up_btn.pack(fill="x", pady=(0, 5))
        
        self.move_down_btn = self.create_button(
            controls_frame, text="⬇️ Move Down", command=lambda: self.move_merge_file(1),
            bg_color=self.CARD_COLOR, fg_color=self.TEXT_COLOR, hover_color=self.INPUT_BORDER
        )
        self.move_down_btn.pack(fill="x", pady=(0, 5))
        
        self.clear_btn = self.create_button(
            controls_frame, text="🧹 Clear All", command=self.clear_merge_files,
            bg_color=self.CARD_COLOR, fg_color=self.ERROR_COLOR, hover_color=self.INPUT_BORDER
        )
        self.clear_btn.pack(fill="x")
        
        self.merge_details_lbl = tk.Label(
            merge_card, text="No PDFs added yet.", fg=self.MUTED_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 9, "italic")
        )
        self.merge_details_lbl.pack(anchor="w", pady=(6, 0))

        # Save Destination Frame
        merge_save_card = tk.Frame(self.merge_content_frame, bg=self.CARD_COLOR, padx=15, pady=15)
        merge_save_card.pack(fill="x")
        
        merge_save_header_lbl = tk.Label(
            merge_save_card, text="💾 Save Destination", fg=self.TEXT_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 11, "bold")
        )
        merge_save_header_lbl.pack(anchor="w", pady=(0, 8))
        
        merge_save_row = tk.Frame(merge_save_card, bg=self.CARD_COLOR)
        merge_save_row.pack(fill="x")
        
        self.merge_save_entry = tk.Entry(
            merge_save_row,
            textvariable=self.merge_output_path,
            state="readonly",
            readonlybackground=self.INPUT_BG,
            fg=self.TEXT_COLOR,
            bd=0,
            highlightthickness=1,
            highlightbackground=self.INPUT_BORDER,
            highlightcolor=self.ACCENT_COLOR,
            font=(self.font_family, 10),
            width=40
        )
        self.merge_save_entry.pack(side="left", fill="x", expand=True, padx=(0, 10), ipady=5)
        
        self.merge_save_btn = self.create_button(
            merge_save_row, text="Change...", command=self.browse_merge_output_file,
            bg_color=self.ACCENT_COLOR, fg_color="#11111b", hover_color=self.ACCENT_HOVER
        )
        self.merge_save_btn.pack(side="right")
        self.merge_save_btn.config(state="disabled")

    def build_split_ui(self):
        # 1. File Picker Card Frame
        split_file_card = tk.Frame(self.split_content_frame, bg=self.CARD_COLOR, padx=15, pady=15)
        split_file_card.pack(fill="x", pady=(0, 15))
        
        split_file_header = tk.Label(
            split_file_card, text="📄 Input PDF Document", fg=self.TEXT_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 11, "bold")
        )
        split_file_header.pack(anchor="w", pady=(0, 8))
        
        split_file_row = tk.Frame(split_file_card, bg=self.CARD_COLOR)
        split_file_row.pack(fill="x")
        
        self.split_file_entry = tk.Entry(
            split_file_row,
            textvariable=self.split_input_path,
            state="readonly",
            readonlybackground=self.INPUT_BG,
            fg=self.TEXT_COLOR,
            bd=0,
            highlightthickness=1,
            highlightbackground=self.INPUT_BORDER,
            highlightcolor=self.ACCENT_COLOR,
            font=(self.font_family, 10),
            width=40
        )
        self.split_file_entry.pack(side="left", fill="x", expand=True, padx=(0, 10), ipady=5)
        
        self.split_select_btn = self.create_button(
            split_file_row, text="Browse...", command=self.browse_split_input_file,
            bg_color=self.ACCENT_COLOR, fg_color="#11111b", hover_color=self.ACCENT_HOVER
        )
        self.split_select_btn.pack(side="right")
        
        self.split_details_lbl = tk.Label(
            split_file_card, text="No PDF file loaded.", fg=self.MUTED_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 9, "italic")
        )
        self.split_details_lbl.pack(anchor="w", pady=(6, 0))

        # 2. Configuration Settings Card Frame
        split_settings_card = tk.Frame(self.split_content_frame, bg=self.CARD_COLOR, padx=15, pady=15)
        split_settings_card.pack(fill="both", expand=True, pady=(0, 15))
        
        split_settings_header = tk.Label(
            split_settings_card, text="✂️ Split Configurations", fg=self.TEXT_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 11, "bold")
        )
        split_settings_header.pack(anchor="w", pady=(0, 8))
        
        # Radio 1: Extract All
        self.rb_split_all = tk.Radiobutton(
            split_settings_card, text="Extract all pages as individual files", variable=self.split_mode, value="all",
            bg=self.CARD_COLOR, fg=self.TEXT_COLOR, selectcolor=self.INPUT_BG,
            activebackground=self.CARD_COLOR, activeforeground=self.TEXT_COLOR,
            font=(self.font_family, 10), cursor="hand2", command=self.update_split_ui_states
        )
        self.rb_split_all.pack(anchor="w", pady=(0, 6))
        
        # Radio 2: Page Range
        range_row = tk.Frame(split_settings_card, bg=self.CARD_COLOR)
        range_row.pack(fill="x", pady=(0, 6))
        
        self.rb_split_range = tk.Radiobutton(
            range_row, text="Extract custom page range(s):", variable=self.split_mode, value="range",
            bg=self.CARD_COLOR, fg=self.TEXT_COLOR, selectcolor=self.INPUT_BG,
            activebackground=self.CARD_COLOR, activeforeground=self.TEXT_COLOR,
            font=(self.font_family, 10), cursor="hand2", command=self.update_split_ui_states
        )
        self.rb_split_range.pack(side="left", padx=(0, 10))
        
        self.range_entry = tk.Entry(
            range_row,
            textvariable=self.split_range_str,
            bg=self.INPUT_BG,
            fg=self.TEXT_COLOR,
            insertbackground=self.TEXT_COLOR,
            bd=0,
            highlightthickness=1,
            highlightbackground=self.INPUT_BORDER,
            highlightcolor=self.ACCENT_COLOR,
            font=(self.font_family, 10),
            width=15,
            state="disabled"
        )
        self.range_entry.pack(side="left", padx=(0, 8), ipady=2)
        self.range_entry.bind("<KeyRelease>", lambda e: self.update_split_action_state())
        
        self.range_hint_lbl = tk.Label(
            range_row, text="(e.g., 1-3, 5, 8-12)", fg=self.MUTED_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 9, "italic")
        )
        self.range_hint_lbl.pack(side="left")
        
        # Radio 3: Interval
        interval_row = tk.Frame(split_settings_card, bg=self.CARD_COLOR)
        interval_row.pack(fill="x")
        
        self.rb_split_interval = tk.Radiobutton(
            interval_row, text="Split into parts of size:", variable=self.split_mode, value="interval",
            bg=self.CARD_COLOR, fg=self.TEXT_COLOR, selectcolor=self.INPUT_BG,
            activebackground=self.CARD_COLOR, activeforeground=self.TEXT_COLOR,
            font=(self.font_family, 10), cursor="hand2", command=self.update_split_ui_states
        )
        self.rb_split_interval.pack(side="left", padx=(0, 10))
        
        self.spin_interval = tk.Spinbox(
            interval_row,
            from_=1, to=1000,
            textvariable=self.split_interval,
            width=5,
            bg=self.INPUT_BG,
            fg=self.TEXT_COLOR,
            buttonbackground=self.CARD_COLOR,
            bd=0,
            highlightthickness=1,
            highlightbackground=self.INPUT_BORDER,
            highlightcolor=self.ACCENT_COLOR,
            font=(self.font_family, 10),
            state="disabled"
        )
        self.spin_interval.pack(side="left", padx=(0, 8), ipady=2)
        
        self.split_interval.trace_add("write", lambda name, index, mode: self.update_split_action_state())
        
        self.interval_lbl = tk.Label(
            interval_row, text="pages each", fg=self.TEXT_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 10)
        )
        self.interval_lbl.pack(side="left")

        # 3. Output Folder Card Frame
        split_save_card = tk.Frame(self.split_content_frame, bg=self.CARD_COLOR, padx=15, pady=15)
        split_save_card.pack(fill="x")
        
        split_save_header = tk.Label(
            split_save_card, text="💾 Save Destination Directory", fg=self.TEXT_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 11, "bold")
        )
        split_save_header.pack(anchor="w", pady=(0, 8))
        
        split_save_row = tk.Frame(split_save_card, bg=self.CARD_COLOR)
        split_save_row.pack(fill="x")
        
        self.split_save_entry = tk.Entry(
            split_save_row,
            textvariable=self.split_output_dir,
            state="readonly",
            readonlybackground=self.INPUT_BG,
            fg=self.TEXT_COLOR,
            bd=0,
            highlightthickness=1,
            highlightbackground=self.INPUT_BORDER,
            highlightcolor=self.ACCENT_COLOR,
            font=(self.font_family, 10),
            width=40
        )
        self.split_save_entry.pack(side="left", fill="x", expand=True, padx=(0, 10), ipady=5)
        
        self.split_save_btn = self.create_button(
            split_save_row, text="Select Folder...", command=self.browse_split_output_dir,
            bg_color=self.ACCENT_COLOR, fg_color="#11111b", hover_color=self.ACCENT_HOVER
        )
        self.split_save_btn.pack(side="right")
        self.split_save_btn.config(state="disabled")

    def build_reorder_ui(self):
        # 1. File Selection Card Frame
        reorder_file_card = tk.Frame(self.reorder_content_frame, bg=self.CARD_COLOR, padx=15, pady=10)
        reorder_file_card.pack(fill="x", pady=(0, 15))
        
        reorder_file_header = tk.Label(
            reorder_file_card, text="📄 Input PDF Document", fg=self.TEXT_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 11, "bold")
        )
        reorder_file_header.pack(anchor="w", pady=(0, 8))
        
        reorder_file_row = tk.Frame(reorder_file_card, bg=self.CARD_COLOR)
        reorder_file_row.pack(fill="x")
        
        self.reorder_file_entry = tk.Entry(
            reorder_file_row,
            textvariable=self.reorder_input_path,
            state="readonly",
            readonlybackground=self.INPUT_BG,
            fg=self.TEXT_COLOR,
            bd=0,
            highlightthickness=1,
            highlightbackground=self.INPUT_BORDER,
            highlightcolor=self.ACCENT_COLOR,
            font=(self.font_family, 10),
            width=40
        )
        self.reorder_file_entry.pack(side="left", fill="x", expand=True, padx=(0, 10), ipady=5)
        
        self.reorder_select_btn = self.create_button(
            reorder_file_row, text="Browse...", command=self.browse_reorder_input_file,
            bg_color=self.ACCENT_COLOR, fg_color="#11111b", hover_color=self.ACCENT_HOVER
        )
        self.reorder_select_btn.pack(side="right")
        
        self.reorder_details_lbl = tk.Label(
            reorder_file_card, text="No PDF file loaded.", fg=self.MUTED_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 9, "italic")
        )
        self.reorder_details_lbl.pack(anchor="w", pady=(6, 0))

        # 2. Reordering Workspace Card Frame (Page list + operations)
        workspace_card = tk.Frame(self.reorder_content_frame, bg=self.CARD_COLOR, padx=15, pady=10)
        workspace_card.pack(fill="both", expand=True, pady=(0, 15))
        
        list_header = tk.Label(
            workspace_card, text="📋 Page Sequence Order", fg=self.TEXT_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 11, "bold")
        )
        list_header.pack(anchor="w", pady=(0, 8))
        
        list_btn_row = tk.Frame(workspace_card, bg=self.CARD_COLOR)
        list_btn_row.pack(fill="both", expand=True)
        
        list_container = tk.Frame(list_btn_row, bg=self.CARD_COLOR)
        list_container.pack(side="left", fill="both", expand=True, padx=(0, 10))
        
        self.reorder_listbox = tk.Listbox(
            list_container,
            bg=self.INPUT_BG,
            fg=self.TEXT_COLOR,
            selectbackground=self.ACCENT_COLOR,
            selectforeground="#11111b",
            bd=0,
            highlightthickness=1,
            highlightbackground=self.INPUT_BORDER,
            highlightcolor=self.ACCENT_COLOR,
            font=(self.font_family, 10),
            selectmode="browse",
            height=6
        )
        self.reorder_listbox.pack(side="left", fill="both", expand=True)
        self.reorder_listbox.bind("<<ListboxSelect>>", self.on_reorder_select)
        
        scrollbar_reorder = tk.Scrollbar(list_container, orient="vertical", command=self.reorder_listbox.yview, bg=self.CARD_COLOR, bd=0)
        scrollbar_reorder.pack(side="right", fill="y")
        self.reorder_listbox.config(yscrollcommand=scrollbar_reorder.set)
        
        reorder_btn_frame = tk.Frame(list_btn_row, bg=self.CARD_COLOR)
        reorder_btn_frame.pack(side="right", fill="y")
        
        reorder_btn_frame.columnconfigure((0, 1), weight=1, uniform="rb")
        self.reorder_up_btn = self.create_button(
            reorder_btn_frame, text="⬆️ Move Up", command=lambda: self.move_reorder_page(-1),
            bg_color=self.CARD_COLOR, fg_color=self.TEXT_COLOR, hover_color=self.INPUT_BORDER
        )
        self.reorder_up_btn.grid(row=0, column=0, sticky="ew", padx=(0, 3), pady=3)
        
        self.reorder_down_btn = self.create_button(
            reorder_btn_frame, text="⬇️ Move Down", command=lambda: self.move_reorder_page(1),
            bg_color=self.CARD_COLOR, fg_color=self.TEXT_COLOR, hover_color=self.INPUT_BORDER
        )
        self.reorder_down_btn.grid(row=0, column=1, sticky="ew", padx=(3, 0), pady=3)
        
        # Page-specific transformation buttons
        self.reorder_rot_ccw_btn = self.create_button(
            reorder_btn_frame, text="↩️ Rotate CCW", command=lambda: self.transform_selected_page("rot_ccw"),
            bg_color=self.CARD_COLOR, fg_color=self.TEXT_COLOR, hover_color=self.INPUT_BORDER
        )
        self.reorder_rot_ccw_btn.grid(row=1, column=0, sticky="ew", padx=(0, 3), pady=3)
        
        self.reorder_rot_cw_btn = self.create_button(
            reorder_btn_frame, text="↪️ Rotate CW", command=lambda: self.transform_selected_page("rot_cw"),
            bg_color=self.CARD_COLOR, fg_color=self.TEXT_COLOR, hover_color=self.INPUT_BORDER
        )
        self.reorder_rot_cw_btn.grid(row=1, column=1, sticky="ew", padx=(3, 0), pady=3)
        
        self.reorder_flip_h_btn = self.create_button(
            reorder_btn_frame, text="↔️ Flip Horiz", command=lambda: self.transform_selected_page("flip_h"),
            bg_color=self.CARD_COLOR, fg_color=self.TEXT_COLOR, hover_color=self.INPUT_BORDER
        )
        self.reorder_flip_h_btn.grid(row=2, column=0, sticky="ew", padx=(0, 3), pady=3)

        self.reorder_flip_v_btn = self.create_button(
            reorder_btn_frame, text="↕️ Flip Vert", command=lambda: self.transform_selected_page("flip_v"),
            bg_color=self.CARD_COLOR, fg_color=self.TEXT_COLOR, hover_color=self.INPUT_BORDER
        )
        self.reorder_flip_v_btn.grid(row=2, column=1, sticky="ew", padx=(3, 0), pady=3)
        
        self.reorder_delete_btn = self.create_button(
            reorder_btn_frame, text="🗑️ Delete Page", command=self.delete_selected_page,
            bg_color=self.CARD_COLOR, fg_color=self.ERROR_COLOR, hover_color=self.INPUT_BORDER
        )
        self.reorder_delete_btn.grid(row=3, column=0, sticky="ew", padx=(0, 3), pady=3)
        
        self.reorder_reset_btn = self.create_button(
            reorder_btn_frame, text="🧹 Reset Order", command=self.reset_reorder_pages,
            bg_color=self.CARD_COLOR, fg_color=self.ERROR_COLOR, hover_color=self.INPUT_BORDER
        )
        self.reorder_reset_btn.grid(row=3, column=1, sticky="ew", padx=(3, 0), pady=3)
        
        # 3. Save Destination Card Frame
        reorder_save_card = tk.Frame(self.reorder_content_frame, bg=self.CARD_COLOR, padx=15, pady=10)
        reorder_save_card.pack(fill="x")
        
        reorder_save_header = tk.Label(
            reorder_save_card, text="💾 Save Destination", fg=self.TEXT_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 11, "bold")
        )
        reorder_save_header.pack(anchor="w", pady=(0, 8))
        
        reorder_save_row = tk.Frame(reorder_save_card, bg=self.CARD_COLOR)
        reorder_save_row.pack(fill="x")
        
        self.reorder_save_entry = tk.Entry(
            reorder_save_row,
            textvariable=self.reorder_output_path,
            state="readonly",
            readonlybackground=self.INPUT_BG,
            fg=self.TEXT_COLOR,
            bd=0,
            highlightthickness=1,
            highlightbackground=self.INPUT_BORDER,
            highlightcolor=self.ACCENT_COLOR,
            font=(self.font_family, 10),
            width=40
        )
        self.reorder_save_entry.pack(side="left", fill="x", expand=True, padx=(0, 10), ipady=5)
        
        self.reorder_save_btn = self.create_button(
            reorder_save_row, text="Change...", command=self.browse_reorder_output_file,
            bg_color=self.ACCENT_COLOR, fg_color="#11111b", hover_color=self.ACCENT_HOVER
        )
        self.reorder_save_btn.pack(side="right")
        self.reorder_save_btn.config(state="disabled")

    def create_button(self, parent, text, command, bg_color, fg_color, hover_color):
        """Helper to create flat, modern buttons with uniform hover behaviors."""
        btn = tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg_color,
            fg=fg_color,
            activebackground=hover_color,
            activeforeground=fg_color,
            disabledforeground="#45475a" if bg_color[1:3] > "7f" else "#9399b2",
            bd=0,
            relief="flat",
            font=(self.font_family, 9, "bold"),
            padx=15,
            pady=6,
            cursor="hand2"
        )
        btn.bind("<Enter>", lambda e: self.on_button_hover(btn, hover_color))
        btn.bind("<Leave>", lambda e: self.on_button_hover(btn, bg_color))
        return btn

    def on_button_hover(self, button, color):
        """Adjusts background color on hover if widget is enabled."""
        if str(button["state"]) != "disabled":
            button.config(bg=color)

    def switch_tab(self, tab):
        """Toggle between operations, updating views and bottom buttons."""
        if self.is_processing:
            return
            
        self.current_tab = tab
        self.progress_bar.set_progress(0.0)
        self.open_folder_btn.pack_forget()
        
        # Reset tab button background colors to inactive state
        self.merge_tab_btn.config(bg=self.BG_COLOR, fg=self.MUTED_COLOR)
        self.split_tab_btn.config(bg=self.BG_COLOR, fg=self.MUTED_COLOR)
        self.reorder_tab_btn.config(bg=self.BG_COLOR, fg=self.MUTED_COLOR)
        self.flip_tab_btn.config(bg=self.BG_COLOR, fg=self.MUTED_COLOR)
        self.img_to_pdf_tab_btn.config(bg=self.BG_COLOR, fg=self.MUTED_COLOR)
        self.compress_tab_btn.config(bg=self.BG_COLOR, fg=self.MUTED_COLOR)
        self.edit_tab_btn.config(bg=self.BG_COLOR, fg=self.MUTED_COLOR)
        
        # Unpack all content frames
        self.flip_content_frame.pack_forget()
        self.merge_content_frame.pack_forget()
        self.split_content_frame.pack_forget()
        self.reorder_content_frame.pack_forget()
        self.img_to_pdf_content_frame.pack_forget()
        self.compress_content_frame.pack_forget()
        self.edit_content_frame.pack_forget()
        
        if tab == "transform":
            self.flip_tab_btn.config(bg=self.CARD_COLOR, fg=self.ACCENT_COLOR)
            self.flip_content_frame.pack(fill="both", expand=True)
            self.action_btn.config(text="TRANSFORM PDF", command=self.start_transforming)
            
            if self.input_file_path.get():
                self.action_btn.config(state="normal")
                self.status_message.set("Set transformation options and click 'TRANSFORM' to execute.")
                self.status_lbl.config(fg=self.TEXT_COLOR)
                self.load_preview_document(self.input_file_path.get())
            else:
                self.action_btn.config(state="disabled")
                self.status_message.set("Select a PDF file to begin.")
                self.status_lbl.config(fg=self.MUTED_COLOR)
                self.clear_preview_document()
                
        elif tab == "merge":
            self.merge_tab_btn.config(bg=self.CARD_COLOR, fg=self.ACCENT_COLOR)
            self.merge_content_frame.pack(fill="both", expand=True)
            self.action_btn.config(text="MERGE PDFs", command=self.start_merging)
            self.update_merge_ui_state()
            
            selected = self.merge_listbox.curselection()
            if selected:
                idx = selected[0]
                self.load_preview_document(self.merge_files[idx]["path"])
            elif self.merge_files:
                self.merge_listbox.select_set(0)
                self.merge_listbox.activate(0)
                self.load_preview_document(self.merge_files[0]["path"])
            else:
                self.clear_preview_document()
            
        elif tab == "split":
            self.split_tab_btn.config(bg=self.CARD_COLOR, fg=self.ACCENT_COLOR)
            self.split_content_frame.pack(fill="both", expand=True)
            self.action_btn.config(text="SPLIT PDF", command=self.start_splitting)
            self.update_split_ui_states()
            
            if self.split_input_path.get():
                self.load_preview_document(self.split_input_path.get())
            else:
                self.clear_preview_document()
            
        elif tab == "reorder":
            self.reorder_tab_btn.config(bg=self.CARD_COLOR, fg=self.ACCENT_COLOR)
            self.reorder_content_frame.pack(fill="both", expand=True)
            self.action_btn.config(text="REORDER PAGES", command=self.start_reordering)
            self.update_reorder_listbox()
            
            if self.reorder_input_path.get():
                self.load_preview_document(self.reorder_input_path.get())
                selected = self.reorder_listbox.curselection()
                if selected:
                    idx = selected[0]
                    orig_page_idx = self.reorder_pages_list[idx]["idx"]
                    self.show_page(orig_page_idx)
                else:
                    self.reorder_listbox.select_set(0)
                    self.reorder_listbox.activate(0)
                    self.on_reorder_select()
            else:
                self.clear_preview_document()
                
        elif tab == "img_to_pdf":
            self.img_to_pdf_tab_btn.config(bg=self.CARD_COLOR, fg=self.ACCENT_COLOR)
            self.img_to_pdf_content_frame.pack(fill="both", expand=True)
            self.action_btn.config(text="COMPILE PDF", command=self.start_img_to_pdf)
            self.update_img_to_pdf_ui_state()
            
            selected = self.img_listbox.curselection()
            if selected:
                idx = selected[0]
                self.load_preview_document(self.img_to_pdf_files[idx])
            elif self.img_to_pdf_files:
                self.img_listbox.select_set(0)
                self.img_listbox.activate(0)
                self.load_preview_document(self.img_to_pdf_files[0])
            else:
                self.clear_preview_document()
                
        elif tab == "compress":
            self.compress_tab_btn.config(bg=self.CARD_COLOR, fg=self.ACCENT_COLOR)
            self.compress_content_frame.pack(fill="both", expand=True)
            self.action_btn.config(text="COMPRESS PDF", command=self.start_compression)
            self.update_compress_ui_state()
            
            if self.compress_input_path.get():
                self.load_preview_document(self.compress_input_path.get())
            else:
                self.clear_preview_document()
                
        elif tab == "edit":
            self.edit_tab_btn.config(bg=self.CARD_COLOR, fg=self.ACCENT_COLOR)
            self.edit_content_frame.pack(fill="both", expand=True)
            self.action_btn.config(text="SAVE EDITS", command=self.start_editing)
            self.set_edit_tool("sign")
            self.update_edit_ui_state()
            
            if self.edit_input_path.get():
                self.load_preview_document(self.edit_input_path.get())
            else:
                self.clear_preview_document()

    def browse_input_file(self):
        """Trigger native dialog to select a source PDF for mirroring."""
        file_path = filedialog.askopenfilename(
            title="Select PDF Document", filetypes=[("PDF Files", "*.pdf")]
        )
        if file_path:
            self.input_file_path.set(file_path)
            
            try:
                reader = PdfReader(file_path)
                self.pdf_pages = len(reader.pages)
                file_size = os.path.getsize(file_path)
                self.pdf_size_str = format_file_size(file_size)
                
                self.file_details_lbl.config(
                    text=f"Pages: {self.pdf_pages}  |  File Size: {self.pdf_size_str}", fg=self.SUCCESS_COLOR
                )
                
                dir_name, file_name = os.path.split(file_path)
                name_wo_ext, ext = os.path.splitext(file_name)
                suggested_out = os.path.join(dir_name, f"{name_wo_ext}_transformed{ext}")
                self.output_file_path.set(suggested_out)
                
                self.save_btn.config(state="normal")
                self.action_btn.config(state="normal")
                self.status_message.set("Set transformation options and click 'TRANSFORM' to execute.")
                self.status_lbl.config(fg=self.TEXT_COLOR)
                self.progress_bar.set_progress(0.0)
                self.open_folder_btn.pack_forget()
                
                # Load document in visual previewer
                self.load_preview_document(file_path)
                
            except Exception as e:
                messagebox.showerror("Error Reading PDF", f"Failed to load PDF metadata:\n{str(e)}")
                self.reset_file_selection()

    def reset_file_selection(self):
        self.input_file_path.set("")
        self.output_file_path.set("")
        self.file_details_lbl.config(text="No PDF file loaded.", fg=self.MUTED_COLOR)
        self.save_btn.config(state="disabled")
        self.action_btn.config(state="disabled")
        self.status_message.set("Select a PDF file to begin.")
        self.status_lbl.config(fg=self.MUTED_COLOR)
        self.open_folder_btn.pack_forget()
        self.clear_preview_document()

    def browse_output_file(self):
        file_path = filedialog.asksaveasfilename(
            title="Save Transformed PDF As", defaultextension=".pdf", filetypes=[("PDF Files", "*.pdf")],
            initialfile=os.path.basename(self.output_file_path.get())
        )
        if file_path:
            self.output_file_path.set(file_path)

    # --- Merge Logic and Handlers ---
    def add_merge_files(self):
        file_paths = filedialog.askopenfilenames(
            title="Select PDF Documents to Merge", filetypes=[("PDF Files", "*.pdf")]
        )
        if file_paths:
            for path in file_paths:
                try:
                    reader = PdfReader(path)
                    pages = len(reader.pages)
                    size = os.path.getsize(path)
                    self.merge_files.append({
                        "path": path, "pages": pages, "size": size
                    })
                except Exception as e:
                    messagebox.showerror("Error Loading PDF", f"Could not load '{os.path.basename(path)}':\n{str(e)}")
            
            if not self.merge_output_path.get() and self.merge_files:
                first_file_path = self.merge_files[0]["path"]
                dir_name = os.path.dirname(first_file_path)
                self.merge_output_path.set(os.path.join(dir_name, "merged.pdf"))
                
            self.update_merge_ui_state()
            
            if self.merge_files:
                self.merge_listbox.select_set(0)
                self.merge_listbox.activate(0)
                self.on_merge_select()

    def remove_merge_file(self):
        selected = self.merge_listbox.curselection()
        if selected:
            idx = selected[0]
            self.merge_files.pop(idx)
            self.update_merge_ui_state()
            
            if self.merge_files:
                new_idx = min(idx, len(self.merge_files) - 1)
                self.merge_listbox.select_set(new_idx)
                self.merge_listbox.activate(new_idx)
                self.on_merge_select()
            else:
                self.clear_preview_document()

    def move_merge_file(self, direction):
        selected = self.merge_listbox.curselection()
        if selected:
            idx = selected[0]
            new_idx = idx + direction
            if 0 <= new_idx < len(self.merge_files):
                self.merge_files[idx], self.merge_files[new_idx] = self.merge_files[new_idx], self.merge_files[idx]
                self.update_merge_ui_state()
                self.merge_listbox.select_set(new_idx)
                self.merge_listbox.activate(new_idx)
                self.on_merge_select()

    def clear_merge_files(self):
        self.merge_files = []
        self.merge_output_path.set("")
        self.update_merge_ui_state()
        self.clear_preview_document()

    def browse_merge_output_file(self):
        initial_file = "merged.pdf"
        initial_dir = None
        if self.merge_output_path.get():
            initial_dir = os.path.dirname(self.merge_output_path.get())
            initial_file = os.path.basename(self.merge_output_path.get())
        elif self.merge_files:
            initial_dir = os.path.dirname(self.merge_files[0]["path"])
            
        file_path = filedialog.asksaveasfilename(
            title="Save Merged PDF As", defaultextension=".pdf", filetypes=[("PDF Files", "*.pdf")],
            initialdir=initial_dir, initialfile=initial_file
        )
        if file_path:
            self.merge_output_path.set(file_path)
            self.update_merge_ui_state()

    def update_merge_ui_state(self):
        self.merge_listbox.delete(0, "end")
        for item in self.merge_files:
            self.merge_listbox.insert("end", os.path.basename(item["path"]))
            
        total_files = len(self.merge_files)
        total_pages = sum(item["pages"] for item in self.merge_files)
        total_size = sum(item["size"] for item in self.merge_files)
        
        if total_files > 0:
            self.merge_details_lbl.config(
                text=f"Files: {total_files}  |  Total Pages: {total_pages}  |  Total Size: {format_file_size(total_size)}",
                fg=self.SUCCESS_COLOR
            )
            self.merge_save_btn.config(state="normal")
        else:
            self.merge_details_lbl.config(
                text="No PDFs added yet.", fg=self.MUTED_COLOR
            )
            self.merge_save_btn.config(state="disabled")
            
        if self.current_tab == "merge":
            if total_files >= 2:
                if self.merge_output_path.get():
                    self.action_btn.config(state="normal")
                    self.status_message.set("Ready to merge. Click 'MERGE PDFs' to execute.")
                    self.status_lbl.config(fg=self.TEXT_COLOR)
                else:
                    self.action_btn.config(state="disabled")
                    self.status_message.set("Specify a save destination.")
                    self.status_lbl.config(fg=self.MUTED_COLOR)
            else:
                self.action_btn.config(state="disabled")
                self.status_message.set("Add at least 2 PDF files to merge.")
                self.status_lbl.config(fg=self.MUTED_COLOR)

    def on_merge_select(self, event=None):
        selected = self.merge_listbox.curselection()
        if selected:
            idx = selected[0]
            file_path = self.merge_files[idx]["path"]
            if file_path != self.preview_file_path:
                self.load_preview_document(file_path)

    # --- Split Logic and Handlers ---
    def browse_split_input_file(self):
        file_path = filedialog.askopenfilename(
            title="Select PDF to Split", filetypes=[("PDF Files", "*.pdf")]
        )
        if file_path:
            self.split_input_path.set(file_path)
            
            try:
                reader = PdfReader(file_path)
                self.split_pdf_pages = len(reader.pages)
                file_size = os.path.getsize(file_path)
                self.split_pdf_size_str = format_file_size(file_size)
                
                self.split_details_lbl.config(
                    text=f"Pages: {self.split_pdf_pages}  |  File Size: {self.split_pdf_size_str}", fg=self.SUCCESS_COLOR
                )
                
                dir_name = os.path.dirname(file_path)
                self.split_output_dir.set(dir_name)
                
                self.split_save_btn.config(state="normal")
                self.update_split_ui_states()
                self.progress_bar.set_progress(0.0)
                self.open_folder_btn.pack_forget()
                
                # Load PDF into visual previewer
                self.load_preview_document(file_path)
                
            except Exception as e:
                messagebox.showerror("Error Reading PDF", f"Failed to load PDF metadata:\n{str(e)}")
                self.reset_split_selection()

    def reset_split_selection(self):
        self.split_input_path.set("")
        self.split_output_dir.set("")
        self.split_details_lbl.config(text="No PDF file loaded.", fg=self.MUTED_COLOR)
        self.split_save_btn.config(state="disabled")
        self.action_btn.config(state="disabled")
        self.status_message.set("Select a PDF file to begin.")
        self.status_lbl.config(fg=self.MUTED_COLOR)
        self.open_folder_btn.pack_forget()
        self.clear_preview_document()

    def browse_split_output_dir(self):
        initial_dir = self.split_output_dir.get() if self.split_output_dir.get() else None
        dir_path = filedialog.askdirectory(
            title="Select Save Directory", initialdir=initial_dir
        )
        if dir_path:
            self.split_output_dir.set(dir_path)
            self.update_split_action_state()

    def update_split_ui_states(self):
        mode = self.split_mode.get()
        
        if mode == "all":
            self.range_entry.config(state="disabled")
            self.spin_interval.config(state="disabled")
        elif mode == "range":
            self.range_entry.config(state="normal")
            self.spin_interval.config(state="disabled")
        elif mode == "interval":
            self.range_entry.config(state="disabled")
            self.spin_interval.config(state="normal")
            
        self.update_split_action_state()

    def update_split_action_state(self):
        if self.current_tab != "split":
            return
            
        if not self.split_input_path.get() or not self.split_output_dir.get():
            self.action_btn.config(state="disabled")
            self.status_message.set("Select an input file and target folder.")
            self.status_lbl.config(fg=self.MUTED_COLOR)
            return
            
        mode = self.split_mode.get()
        
        if mode == "range":
            range_val = self.split_range_str.get().strip()
            if not range_val:
                self.action_btn.config(state="disabled")
                self.status_message.set("Enter a page range.")
                self.status_lbl.config(fg=self.MUTED_COLOR)
                return
                
        elif mode == "interval":
            try:
                interval_val = self.split_interval.get()
                if interval_val <= 0:
                    self.action_btn.config(state="disabled")
                    self.status_message.set("Interval must be a positive integer.")
                    self.status_lbl.config(fg=self.MUTED_COLOR)
                    return
            except tk.TclError:
                self.action_btn.config(state="disabled")
                self.status_message.set("Enter a valid interval number.")
                self.status_lbl.config(fg=self.MUTED_COLOR)
                return
                
        self.action_btn.config(state="normal")
        self.status_message.set("Ready to split. Click 'SPLIT PDF' to execute.")
        self.status_lbl.config(fg=self.TEXT_COLOR)

    # --- Reorder Logic and Handlers ---
    def browse_reorder_input_file(self):
        """Trigger native dialog to select a source PDF for page reordering."""
        file_path = filedialog.askopenfilename(
            title="Select PDF Document to Reorder", filetypes=[("PDF Files", "*.pdf")]
        )
        if file_path:
            self.reorder_input_path.set(file_path)
            
            try:
                reader = PdfReader(file_path)
                self.reorder_pdf_pages = len(reader.pages)
                file_size = os.path.getsize(file_path)
                self.reorder_pdf_size_str = format_file_size(file_size)
                
                self.reorder_details_lbl.config(
                    text=f"Pages: {self.reorder_pdf_pages}  |  File Size: {self.reorder_pdf_size_str}", fg=self.SUCCESS_COLOR
                )
                
                # Propose output file
                dir_name, file_name = os.path.split(file_path)
                name_wo_ext, ext = os.path.splitext(file_name)
                suggested_out = os.path.join(dir_name, f"{name_wo_ext}_reordered{ext}")
                self.reorder_output_path.set(suggested_out)
                
                # Setup page indexing tracking (including page-specific rotations/flips)
                self.reorder_pages_list = [
                    {"idx": i, "rotation": 0, "flip_h": False, "flip_v": False} for i in range(self.reorder_pdf_pages)
                ]
                
                # Enable elements
                self.reorder_save_btn.config(state="normal")
                self.action_btn.config(state="normal")
                self.progress_bar.set_progress(0.0)
                self.open_folder_btn.pack_forget()
                
                # Redraw listbox
                self.update_reorder_listbox()
                
                # Load PDF into visual previewer
                self.load_preview_document(file_path)
                
                # Autoselect first page & trigger preview
                self.reorder_listbox.select_set(0)
                self.reorder_listbox.activate(0)
                self.on_reorder_select()
                
            except Exception as e:
                messagebox.showerror("Error Reading PDF", f"Failed to load PDF metadata:\n{str(e)}")
                self.reset_reorder_selection()

    def reset_reorder_selection(self):
        self.reorder_input_path.set("")
        self.reorder_output_path.set("")
        self.reorder_pages_list = []
        self.reorder_details_lbl.config(text="No PDF file loaded.", fg=self.MUTED_COLOR)
        self.reorder_listbox.delete(0, "end")
        self.reorder_save_btn.config(state="disabled")
        self.action_btn.config(state="disabled")
        self.status_message.set("Select a PDF file to begin.")
        self.status_lbl.config(fg=self.MUTED_COLOR)
        self.open_folder_btn.pack_forget()
        self.clear_preview_document()

    def browse_reorder_output_file(self):
        file_path = filedialog.asksaveasfilename(
            title="Save Reordered PDF As", defaultextension=".pdf", filetypes=[("PDF Files", "*.pdf")],
            initialfile=os.path.basename(self.reorder_output_path.get())
        )
        if file_path:
            self.reorder_output_path.set(file_path)

    def reset_reorder_pages(self):
        """Restore pages back to original sequential order and clear modifications."""
        if self.reorder_pdf_pages > 0:
            self.reorder_pages_list = [
                {"idx": i, "rotation": 0, "flip_h": False, "flip_v": False} for i in range(self.reorder_pdf_pages)
            ]
            self.preview_thumbnails = {}  # Reset visual render cache
            self.update_reorder_listbox()
            self.reorder_listbox.select_set(0)
            self.reorder_listbox.activate(0)
            self.on_reorder_select()

    def move_reorder_page(self, direction):
        """Move selected page item up or down in the sequence list."""
        selected = self.reorder_listbox.curselection()
        if selected:
            idx = selected[0]
            new_idx = idx + direction
            if 0 <= new_idx < len(self.reorder_pages_list):
                # Swap page mapping dicts
                self.reorder_pages_list[idx], self.reorder_pages_list[new_idx] = self.reorder_pages_list[new_idx], self.reorder_pages_list[idx]
                self.update_reorder_listbox()
                self.reorder_listbox.select_set(new_idx)
                self.reorder_listbox.activate(new_idx)
                self.reorder_listbox.see(new_idx)
                
                # Update visual preview
                self.on_reorder_select()

    def transform_selected_page(self, action):
        """Apply page-level rotation or flipping to the selected item."""
        selected = self.reorder_listbox.curselection()
        if selected:
            idx = selected[0]
            item = self.reorder_pages_list[idx]
            
            if action == "rot_cw":
                item["rotation"] = (item["rotation"] + 90) % 360
            elif action == "rot_ccw":
                item["rotation"] = (item["rotation"] - 90) % 360
            elif action == "flip_h":
                item["flip_h"] = not item["flip_h"]
            elif action == "flip_v":
                item["flip_v"] = not item["flip_v"]
                
            # Clear preview cache for this page so it re-renders
            orig_page_idx = item["idx"]
            if orig_page_idx in self.preview_thumbnails:
                del self.preview_thumbnails[orig_page_idx]
                
            self.update_reorder_listbox()
            self.reorder_listbox.select_set(idx)
            self.reorder_listbox.activate(idx)
            
            # Re-render canvas preview
            self.show_page(orig_page_idx)

    def delete_selected_page(self):
        """Remove selected page from reordering sequence."""
        selected = self.reorder_listbox.curselection()
        if selected:
            idx = selected[0]
            self.reorder_pages_list.pop(idx)
            self.update_reorder_listbox()
            
            if self.reorder_pages_list:
                new_idx = min(idx, len(self.reorder_pages_list) - 1)
                self.reorder_listbox.select_set(new_idx)
                self.reorder_listbox.activate(new_idx)
                self.on_reorder_select()
            else:
                self.clear_preview_document()

    def update_reorder_listbox(self):
        """Redraw reorder page elements and validate action controls."""
        self.reorder_listbox.delete(0, "end")
        for item in self.reorder_pages_list:
            idx_str = f"Page {item['idx'] + 1}"
            mods = []
            if item["rotation"] == 90:
                mods.append("Rotated Right ↻")
            elif item["rotation"] == 180:
                mods.append("Upside Down")
            elif item["rotation"] == 270:
                mods.append("Rotated Left ↺")
            if item["flip_h"]:
                mods.append("Flipped H")
            if item["flip_v"]:
                mods.append("Flipped V")
            if mods:
                idx_str += f" ({', '.join(mods)})"
            self.reorder_listbox.insert("end", idx_str)
            
        if self.current_tab == "reorder":
            if len(self.reorder_pages_list) > 0:
                if self.reorder_output_path.get():
                    self.action_btn.config(state="normal")
                    self.status_message.set("Ready to reorder. Click 'REORDER PAGES' to execute.")
                    self.status_lbl.config(fg=self.TEXT_COLOR)
                else:
                    self.action_btn.config(state="disabled")
                    self.status_message.set("Specify a save destination.")
                    self.status_lbl.config(fg=self.MUTED_COLOR)
            elif self.reorder_pdf_pages == 0:
                self.action_btn.config(state="disabled")
                self.status_message.set("Select a PDF file to begin.")
                self.status_lbl.config(fg=self.MUTED_COLOR)
            else:
                self.action_btn.config(state="disabled")
                self.status_message.set("All pages deleted. Reset order to restore.")
                self.status_lbl.config(fg=self.ERROR_COLOR)

    def on_preview_resize(self, event=None):
        """Re-render page preview on container resize for a responsive UI."""
        if self.preview_doc:
            self.preview_thumbnails = {}  # Clear cache so pages render at the new container size
            self.load_page_preview(self.preview_page_idx)
        else:
            self.show_canvas_message("Load a PDF to preview pages.")

    def on_reorder_select(self, event=None):
        """Listbox selection binding handler to update the live preview image."""
        selected = self.reorder_listbox.curselection()
        if selected:
            idx = selected[0]
            item = self.reorder_pages_list[idx]
            orig_page_idx = item["idx"]
            
            # Force clearing cache for this page so it gets re-rendered with active transformations
            if orig_page_idx in self.preview_thumbnails:
                del self.preview_thumbnails[orig_page_idx]
                
            self.show_page(orig_page_idx)

    # --- Unified Previewer Engines and Handlers ---
    def load_preview_document(self, file_path):
        """Load a PDF document into the previewer."""
        if self.preview_file_path == file_path and self.preview_doc is not None:
            return
            
        self.clear_preview_document()
        self.preview_file_path = file_path
        
        if not file_path or not os.path.exists(file_path):
            self.show_canvas_message("No document loaded.")
            return
            
        try:
            self.preview_doc = fitz.open(file_path)
            self.preview_page_idx = 0
            self.preview_zoom_factor = 1.0
            self.zoom_lbl.config(text="100%")
            self.update_nav_controls()
            self.load_page_preview(0)
        except Exception as e:
            self.preview_doc = None
            self.preview_file_path = ""
            self.show_canvas_message(f"Error loading preview:\n{str(e)}", is_error=True)
            self.update_nav_controls()

    def clear_preview_document(self):
        """Close current document and clear preview canvas."""
        if self.preview_doc:
            try:
                self.preview_doc.close()
            except Exception:
                pass
        self.preview_doc = None
        self.preview_file_path = ""
        self.preview_page_idx = 0
        self.preview_thumbnails = {}
        self.preview_zoom_factor = 1.0
        self.zoom_lbl.config(text="100%")
        self.show_canvas_message("Load a PDF to preview pages.")
        self.update_nav_controls()

    def update_nav_controls(self):
        """Update active states and label of page navigation controls."""
        if self.current_tab == "reorder":
            if self.reorder_pages_list:
                total_pages = len(self.reorder_pages_list)
                selected = self.reorder_listbox.curselection()
                seq_idx = selected[0] if selected else 0
                self.page_nav_lbl.config(text=f"Page {seq_idx + 1} of {total_pages}")
                self.prev_page_btn.config(state="normal" if seq_idx > 0 else "disabled")
                self.next_page_btn.config(state="normal" if seq_idx < total_pages - 1 else "disabled")
            else:
                self.page_nav_lbl.config(text="Page 0 of 0")
                self.prev_page_btn.config(state="disabled")
                self.next_page_btn.config(state="disabled")
        else:
            if self.preview_doc:
                total_pages = len(self.preview_doc)
                self.page_nav_lbl.config(text=f"Page {self.preview_page_idx + 1} of {total_pages}")
                
                # Enable/disable buttons based on page index bounds
                self.prev_page_btn.config(state="normal" if self.preview_page_idx > 0 else "disabled")
                self.next_page_btn.config(state="normal" if self.preview_page_idx < total_pages - 1 else "disabled")
            else:
                self.page_nav_lbl.config(text="Page 0 of 0")
                self.prev_page_btn.config(state="disabled")
                self.next_page_btn.config(state="disabled")

    def go_to_prev_page(self):
        if self.current_tab == "reorder":
            selected = self.reorder_listbox.curselection()
            seq_idx = selected[0] if selected else 0
            if seq_idx > 0:
                self.reorder_listbox.selection_clear(0, "end")
                self.reorder_listbox.selection_set(seq_idx - 1)
                self.reorder_listbox.activate(seq_idx - 1)
                self.reorder_listbox.see(seq_idx - 1)
                self.on_reorder_select()
        else:
            if self.preview_doc and self.preview_page_idx > 0:
                self.show_page(self.preview_page_idx - 1)

    def go_to_next_page(self):
        if self.current_tab == "reorder":
            selected = self.reorder_listbox.curselection()
            seq_idx = selected[0] if selected else 0
            if seq_idx < len(self.reorder_pages_list) - 1:
                self.reorder_listbox.selection_clear(0, "end")
                self.reorder_listbox.selection_set(seq_idx + 1)
                self.reorder_listbox.activate(seq_idx + 1)
                self.reorder_listbox.see(seq_idx + 1)
                self.on_reorder_select()
        else:
            if self.preview_doc:
                total_pages = len(self.preview_doc)
                if self.preview_page_idx < total_pages - 1:
                    self.show_page(self.preview_page_idx + 1)

    def show_page(self, page_idx):
        if self.preview_doc:
            total_pages = len(self.preview_doc)
            if 0 <= page_idx < total_pages:
                self.preview_page_idx = page_idx
                self.update_nav_controls()
                self.load_page_preview(page_idx)

    def show_canvas_message(self, message, is_error=False):
        """Display text message centered in the canvas."""
        self.preview_canvas.delete("all")
        w = self.preview_canvas.winfo_width()
        h = self.preview_canvas.winfo_height()
        if w <= 1 or h <= 1:
            w = 260
            h = 360
        fill_color = self.ERROR_COLOR if is_error else self.MUTED_COLOR
        font_style = (self.font_family, 9, "bold") if is_error else (self.font_family, 9, "italic")
        self.preview_canvas.create_text(
            w // 2, h // 2,
            text=message,
            fill=fill_color,
            font=font_style,
            width=200,
            justify="center"
        )

    def on_flip_option_changed(self):
        """Re-render preview instantly when flip/rotate options are toggled."""
        if self.preview_doc:
            self.preview_thumbnails = {}  # Clear cache
            self.load_page_preview(self.preview_page_idx)

    def load_page_preview(self, page_idx):
        """Render and display the PDF page visual thumbnail inside the preview canvas."""
        if not self.preview_doc:
            return
            
        if page_idx in self.preview_thumbnails:
            tk_img = self.preview_thumbnails[page_idx]
            self.draw_preview_image(tk_img)
            return

        # Render visual page dynamically using PyMuPDF
        try:
            page = self.preview_doc.load_page(page_idx)
            
            # Dimensions
            rect = page.rect
            width = rect.width
            height = rect.height
            
            # Get actual container size
            container_w = self.preview_canvas.winfo_width()
            container_h = self.preview_canvas.winfo_height()
            
            # Fallback if not yet drawn/mapped
            if container_w <= 1 or container_h <= 1:
                container_w = 260
                container_h = 360
                
            # Subtract padding to ensure it fits comfortably within borders
            target_w = max(50, container_w - 20)
            target_h = max(50, container_h - 20)
            
            # Base scaling factor to fit the longest side of the document
            scale_w = target_w / width
            scale_h = target_h / height
            base_scale = min(scale_w, scale_h)
            
            # Apply user zoom multiplier
            scale = base_scale * self.preview_zoom_factor
            self.preview_scale = scale
            
            # Compute matrix based on active tab transformations
            if self.current_tab == "reorder":
                selected = self.reorder_listbox.curselection()
                rot = 0
                fh = False
                fv = False
                if selected:
                    seq_idx = selected[0]
                    item = self.reorder_pages_list[seq_idx]
                    if item["idx"] == page_idx:
                        rot = item["rotation"]
                        fh = item["flip_h"]
                        fv = item["flip_v"]
                
                sx = -1.0 if fh else 1.0
                sy = -1.0 if fv else 1.0
                matrix = fitz.Matrix(scale * sx, scale * sy)
                if rot != 0:
                    matrix.prerotate(rot)
            else:
                if self.current_tab == "transform":
                    sx = -1.0 if self.flip_horizontal.get() else 1.0
                    sy = -1.0 if self.flip_vertical.get() else 1.0
                    rot = self.transform_rotate.get()
                else:
                    sx, sy, rot = 1.0, 1.0, 0
                    
                matrix = fitz.Matrix(scale * sx, scale * sy)
                if rot != 0:
                    matrix.prerotate(rot)
            
            pix = page.get_pixmap(matrix=matrix)
            
            # Convert directly to Tkinter-supported PPM bytes representation
            img_data = pix.tobytes("ppm")
            tk_img = tk.PhotoImage(data=img_data)
            
            # Cache the PhotoImage object
            self.preview_thumbnails[page_idx] = tk_img
            
            # Render to canvas
            self.draw_preview_image(tk_img)
        except Exception as e:
            self.show_canvas_message(f"Preview unavailable:\n{str(e)}", is_error=True)

    def draw_preview_image(self, tk_img):
        """Draw the PhotoImage centered on the canvas and configure scroll boundaries."""
        self.preview_canvas.delete("all")
        
        canvas_w = self.preview_canvas.winfo_width()
        canvas_h = self.preview_canvas.winfo_height()
        if canvas_w <= 1 or canvas_h <= 1:
            canvas_w = 260
            canvas_h = 360
            
        img_w = tk_img.width()
        img_h = tk_img.height()
        
        # Center the image NW coordinate if smaller than the canvas bounds
        x = max(0, (canvas_w - img_w) // 2)
        y = max(0, (canvas_h - img_h) // 2)
        
        # Draw image anchored NW at calculated coordinates
        self.preview_canvas.create_image(x, y, image=tk_img, anchor="nw")
        self.preview_canvas.image = tk_img  # Prevent garbage collection
        
        # Save placement coordinate details for map operations
        self.preview_x = x
        self.preview_y = y
        self.preview_w = img_w
        self.preview_h = img_h
        
        if self.current_tab == "edit":
            self.update_canvas_overlays()
            
        # Set the scrollregion boundaries so drag panning works correctly
        scroll_w = max(canvas_w, img_w + x * 2)
        scroll_h = max(canvas_h, img_h + y * 2)
        self.preview_canvas.config(scrollregion=(0, 0, scroll_w, scroll_h))

    # --- Zoom and Panning Handlers ---
    def zoom_preview(self, multiplier):
        """Adjust the page preview zoom multiplier."""
        if not self.preview_doc:
            return
            
        new_zoom = self.preview_zoom_factor * multiplier
        self.preview_zoom_factor = max(0.2, min(5.0, new_zoom))
        
        # Update indicator label
        self.zoom_lbl.config(text=f"{int(self.preview_zoom_factor * 100)}%")
        
        # Clear cached thumbnail for the active page so it re-renders at new zoom
        if self.preview_page_idx in self.preview_thumbnails:
            del self.preview_thumbnails[self.preview_page_idx]
            
        self.load_page_preview(self.preview_page_idx)

    def zoom_reset(self):
        """Reset page zoom to fit container height/width (100% relative)."""
        if not self.preview_doc:
            return
            
        self.preview_zoom_factor = 1.0
        self.zoom_lbl.config(text="100%")
        
        if self.preview_page_idx in self.preview_thumbnails:
            del self.preview_thumbnails[self.preview_page_idx]
            
        self.load_page_preview(self.preview_page_idx)

    def on_mousewheel(self, event):
        """Zoom page in/out on Ctrl + Scroll Wheel."""
        ctrl_pressed = (event.state & 0x0004) != 0
        if ctrl_pressed:
            if event.delta > 0:
                self.zoom_preview(1.2)
            else:
                self.zoom_preview(1 / 1.2)
            return "break"  # Prevent default scroll events

    def on_pan_start(self, event):
        """Record click coordinates to drag-pan canvas images, or handle editing."""
        if self.current_tab == "edit":
            cx = self.preview_canvas.canvasx(event.x)
            cy = self.preview_canvas.canvasy(event.y)
            item, action = self.find_item_at_coords(cx, cy)
            if item or self.edit_active_tool != "pan":
                self.on_canvas_click(event)
                return "break"
            else:
                self.active_edit_item = None
                self.update_canvas_overlays()
        self.preview_canvas.scan_mark(event.x, event.y)
        self.preview_canvas.config(cursor="fleur")

    def on_pan_drag(self, event):
        """Shift the canvas scroll view according to mouse drag motion, or handle editing."""
        if self.current_tab == "edit":
            if self.active_edit_item or self.redact_start or self.edit_active_tool != "pan":
                self.on_canvas_drag(event)
                return "break"
        self.preview_canvas.scan_dragto(event.x, event.y, gain=1)

    def on_pan_stop(self, event=None):
        """Reset cursor back to drag indicator hand, or handle editing."""
        if self.current_tab == "edit":
            if self.active_edit_item or self.redact_start or self.edit_active_tool != "pan":
                if event:
                    self.on_canvas_release(event)
                return "break"
        self.preview_canvas.config(cursor="hand2")

    # --- Processing Execution Handlers ---
    def start_transforming(self):
        """Initiate global document flipping & rotation processing."""
        if not self.input_file_path.get():
            messagebox.showwarning("No Input File", "Please select an input PDF file first.")
            return
            
        if not (self.flip_horizontal.get() or self.flip_vertical.get() or self.transform_rotate.get() != 0):
            messagebox.showwarning(
                "No Transformations", "Please select at least one transform option (Mirror or Rotation)."
            )
            return

        self.is_processing = True
        self.set_ui_state("disabled")
        self.open_folder_btn.pack_forget()
        self.status_message.set("Transforming document...")
        self.status_lbl.config(fg=self.ACCENT_COLOR)
        self.progress_bar.set_progress(0.0)
        
        threading.Thread(
            target=run_pdf_transform,
            args=(
                self.input_file_path.get(), self.output_file_path.get(),
                self.flip_horizontal.get(), self.flip_vertical.get(),
                self.transform_rotate.get(), self.msg_queue
            ),
            daemon=True
        ).start()
        
        self.root.after(100, self.check_progress)

    def start_merging(self):
        if len(self.merge_files) < 2:
            messagebox.showwarning("Insufficient Files", "Please add at least 2 PDF files to merge.")
            return
            
        if not self.merge_output_path.get():
            messagebox.showwarning("No Save Destination", "Please specify a save destination.")
            return

        self.is_processing = True
        self.set_ui_state("disabled")
        self.open_folder_btn.pack_forget()
        self.status_message.set("Merging documents...")
        self.status_lbl.config(fg=self.ACCENT_COLOR)
        self.progress_bar.set_progress(0.0)
        
        input_paths = [item["path"] for item in self.merge_files]
        
        threading.Thread(
            target=run_pdf_merge,
            args=(input_paths, self.merge_output_path.get(), self.msg_queue),
            daemon=True
        ).start()
        
        self.root.after(100, self.check_progress)

    def start_splitting(self):
        if not self.split_input_path.get():
            messagebox.showwarning("No Input File", "Please select an input PDF file first.")
            return
            
        if not self.split_output_dir.get():
            messagebox.showwarning("No Save Directory", "Please select a target folder.")
            return
            
        mode = self.split_mode.get()
        range_str = self.split_range_str.get()
        interval = 2
        
        if mode == "interval":
            try:
                interval = self.split_interval.get()
                if interval <= 0:
                    messagebox.showwarning("Invalid Interval", "Interval size must be at least 1.")
                    return
            except tk.TclError:
                messagebox.showwarning("Invalid Interval", "Interval must be a valid integer.")
                return

        if mode == "range":
            try:
                parse_range_string(range_str, self.split_pdf_pages)
            except Exception as e:
                messagebox.showerror("Invalid Range Selection", f"Error parsing range configuration:\n{str(e)}")
                return

        self.is_processing = True
        self.set_ui_state("disabled")
        self.open_folder_btn.pack_forget()
        self.status_message.set("Splitting document...")
        self.status_lbl.config(fg=self.ACCENT_COLOR)
        self.progress_bar.set_progress(0.0)
        
        threading.Thread(
            target=run_pdf_split,
            args=(
                self.split_input_path.get(), self.split_output_dir.get(),
                mode, range_str, interval, self.msg_queue
            ),
            daemon=True
        ).start()
        
        self.root.after(100, self.check_progress)

    def start_reordering(self):
        """Prepare UI and spin up PDF page sequence reordering tasks."""
        if not self.reorder_input_path.get():
            messagebox.showwarning("No Input File", "Please select an input PDF document first.")
            return
            
        if not self.reorder_output_path.get():
            messagebox.showwarning("No Save Destination", "Please specify a save destination.")
            return

        self.is_processing = True
        self.set_ui_state("disabled")
        self.open_folder_btn.pack_forget()
        self.status_message.set("Reordering and modifying pages...")
        self.status_lbl.config(fg=self.ACCENT_COLOR)
        self.progress_bar.set_progress(0.0)
        
        threading.Thread(
            target=run_pdf_reorder,
            args=(
                self.reorder_input_path.get(),
                self.reorder_output_path.get(),
                self.reorder_pages_list,
                self.msg_queue
            ),
            daemon=True
        ).start()
        
        self.root.after(100, self.check_progress)

    def set_ui_state(self, state):
        """Lock or unlock interactive controls during running operations."""
        self.flip_tab_btn.config(state=state)
        self.merge_tab_btn.config(state=state)
        self.split_tab_btn.config(state=state)
        self.reorder_tab_btn.config(state=state)
        self.img_to_pdf_tab_btn.config(state=state)
        self.compress_tab_btn.config(state=state)
        self.edit_tab_btn.config(state=state)
        
        # Transform Controls
        self.select_btn.config(state=state)
        self.save_btn.config(state=state)
        self.cb_horiz.config(state=state)
        self.cb_vert.config(state=state)
        for ang, btn in self.rot_buttons:
            btn.config(state=state)
        
        # Merge Controls
        self.add_files_btn.config(state=state)
        self.remove_file_btn.config(state=state)
        self.move_up_btn.config(state=state)
        self.move_down_btn.config(state=state)
        self.clear_btn.config(state=state)
        self.merge_save_btn.config(state=state)
        
        # Split Controls
        self.split_select_btn.config(state=state)
        self.split_save_btn.config(state=state)
        self.rb_split_all.config(state=state)
        self.rb_split_range.config(state=state)
        self.rb_split_interval.config(state=state)
        
        # Reorder Controls
        self.reorder_select_btn.config(state=state)
        self.reorder_save_btn.config(state=state)
        self.reorder_up_btn.config(state=state)
        self.reorder_down_btn.config(state=state)
        self.reorder_rot_ccw_btn.config(state=state)
        self.reorder_rot_cw_btn.config(state=state)
        self.reorder_flip_h_btn.config(state=state)
        self.reorder_flip_v_btn.config(state=state)
        self.reorder_delete_btn.config(state=state)
        self.reorder_reset_btn.config(state=state)
        
        # Images to PDF Controls
        self.img_add_btn.config(state=state)
        self.img_up_btn.config(state=state)
        self.img_down_btn.config(state=state)
        self.img_remove_btn.config(state=state)
        self.img_clear_btn.config(state=state)
        self.img_save_btn.config(state=state)

        # Compress Controls
        self.comp_select_btn.config(state=state)
        self.comp_low_btn.config(state=state)
        self.comp_med_btn.config(state=state)
        self.comp_high_btn.config(state=state)
        self.comp_save_btn.config(state=state)
        
        # Edit Controls
        self.edit_select_btn.config(state=state)
        self.edit_save_browse_btn.config(state=state)
        self.clear_sig_btn.config(state=state)
        for tid, btn in self.edit_tool_buttons.items():
            btn.config(state=state)
        
        # Nav toolbar elements
        self.prev_page_btn.config(state=state)
        self.next_page_btn.config(state=state)
        self.zoom_in_btn.config(state=state)
        self.zoom_out_btn.config(state=state)
        self.zoom_reset_btn.config(state=state)
        
        if state == "normal":
            self.update_split_ui_states()
            self.update_reorder_listbox()
            self.update_img_to_pdf_ui_state()
            self.update_compress_ui_state()
            self.update_edit_ui_state()
            self.update_nav_controls()
        else:
            self.range_entry.config(state=state)
            self.spin_interval.config(state=state)
            self.reorder_listbox.config(state=state)
            self.img_listbox.config(state=state)
            
        self.action_btn.config(state=state)

    def check_progress(self):
        """Check the message queue for worker thread updates."""
        try:
            while True:
                msg_type, msg_val = self.msg_queue.get_nowait()
                if msg_type == "progress":
                    self.progress_bar.set_progress(msg_val)
                    self.status_message.set(f"Processing... {int(msg_val * 100)}%")
                elif msg_type == "success":
                    self.finish_processing(success=True, result=msg_val)
                    return
                elif msg_type == "error":
                    self.finish_processing(success=False, result=msg_val)
                    return
        except queue.Empty:
            if self.is_processing:
                self.root.after(100, self.check_progress)

    def finish_processing(self, success, result):
        """Unlock interface controls and present result notifications."""
        self.is_processing = False
        self.set_ui_state("normal")
        
        if success:
            self.progress_bar.set_progress(1.0)
            if self.current_tab == "compress":
                try:
                    new_size = os.path.getsize(result)
                    old_size = self.compress_pdf_size_bytes
                    savings = old_size - new_size
                    savings_pct = (savings / old_size) * 100 if old_size > 0 else 0
                    
                    old_txt = format_file_size(old_size)
                    new_txt = format_file_size(new_size)
                    
                    status_text = f"Success! Compressed from {old_txt} to {new_txt} ({savings_pct:.1f}% savings)"
                    self.status_message.set(status_text)
                    self.status_lbl.config(fg=self.SUCCESS_COLOR)
                    
                    # Also reload preview document to show the compressed output
                    self.load_preview_document(result)
                    
                    messagebox.showinfo(
                        "Success", 
                        f"Compression complete!\n\n"
                        f"Original Size: {old_txt}\n"
                        f"Compressed Size: {new_txt}\n"
                        f"Savings: {savings_pct:.1f}%\n\n"
                        f"Saved to:\n{result}"
                    )
                except Exception:
                    self.status_message.set("Success! Operation completed.")
                    self.status_lbl.config(fg=self.SUCCESS_COLOR)
                    messagebox.showinfo("Success", f"Operation completed successfully. Saved to:\n{result}")
            elif self.current_tab == "edit":
                self.status_message.set("Success! Applied signatures & redactions successfully.")
                self.status_lbl.config(fg=self.SUCCESS_COLOR)
                
                # Load the edited document to show what was changed
                self.load_preview_document(result)
                # Clear placed items so they don't overlay on the new saved file preview
                self.placed_items = []
                self.active_edit_item = None
                self.update_canvas_overlays()
                
                messagebox.showinfo("Success", f"PDF edits applied successfully.\n\nSaved to:\n{result}")
            else:
                self.status_message.set("Success! Operation completed.")
                self.status_lbl.config(fg=self.SUCCESS_COLOR)
                messagebox.showinfo("Success", f"Operation completed successfully. Saved to:\n{result}")
            self.open_folder_btn.pack(side="left")
        else:
            self.progress_bar.set_progress(0.0)
            self.status_message.set("Failed: Error occurred.")
            self.status_lbl.config(fg=self.ERROR_COLOR)
            messagebox.showerror("Error", f"An error occurred during operation:\n{result}")

    def open_output_folder(self):
        """Open the output file's containing folder in native file explorer."""
        if self.current_tab == "transform":
            out_path = self.output_file_path.get()
        elif self.current_tab == "merge":
            out_path = self.merge_output_path.get()
        elif self.current_tab == "split":
            out_path = self.split_output_dir.get()
        elif self.current_tab == "reorder":
            out_path = self.reorder_output_path.get()
        elif self.current_tab == "img_to_pdf":
            out_path = self.img_to_pdf_save_path.get()
        elif self.current_tab == "compress":
            out_path = self.compress_save_path.get()
        elif self.current_tab == "edit":
            out_path = self.edit_save_path.get()
        else:
            out_path = None
            
        if out_path:
            dir_path = out_path if self.current_tab == "split" else os.path.dirname(os.path.abspath(out_path))
            if os.path.exists(dir_path):
                try:
                    if sys.platform == "win32":
                        os.startfile(dir_path)
                    else:
                        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", dir_path])
                except Exception:
                    messagebox.showinfo("Output Folder", f"Saved in:\n{dir_path}")

    def build_img_to_pdf_ui(self):
        """Construct the Images to PDF tab workspace layout."""
        # 1. Title/Header Frame
        img_title_card = tk.Frame(self.img_to_pdf_content_frame, bg=self.CARD_COLOR, padx=15, pady=15)
        img_title_card.pack(fill="x", pady=(0, 15))
        
        img_title_lbl = tk.Label(
            img_title_card, text="🖼️ Convert Images to PDF", fg=self.TEXT_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 12, "bold")
        )
        img_title_lbl.pack(anchor="w")
        
        img_desc_lbl = tk.Label(
            img_title_card, text="Compile multiple images (PNG, JPG, BMP, GIF) into a single PDF document. Drag/move to reorder.",
            fg=self.MUTED_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 9)
        )
        img_desc_lbl.pack(anchor="w", pady=(5, 0))

        # 2. File List / Selection Card
        list_card = tk.Frame(self.img_to_pdf_content_frame, bg=self.CARD_COLOR, padx=15, pady=15)
        list_card.pack(fill="both", expand=True, pady=(0, 15))
        
        list_header_row = tk.Frame(list_card, bg=self.CARD_COLOR)
        list_header_row.pack(fill="x", pady=(0, 8))
        
        list_lbl = tk.Label(
            list_header_row, text="Image Queue", fg=self.TEXT_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 10, "bold")
        )
        list_lbl.pack(side="left")
        
        self.img_add_btn = self.create_button(
            list_header_row, text="➕ Add Images...", command=self.browse_img_to_pdf_images,
            bg_color=self.ACCENT_COLOR, fg_color="#11111b", hover_color=self.ACCENT_HOVER
        )
        self.img_add_btn.pack(side="right")
        
        # Listbox with Scrollbar
        list_container = tk.Frame(list_card, bg=self.CARD_COLOR)
        list_container.pack(fill="both", expand=True)
        
        list_btn_row = tk.Frame(list_container, bg=self.CARD_COLOR)
        list_btn_row.pack(fill="both", expand=True)
        
        self.img_listbox = tk.Listbox(
            list_btn_row,
            selectmode="single",
            bg=self.INPUT_BG,
            fg=self.TEXT_COLOR,
            bd=0,
            highlightthickness=1,
            highlightbackground=self.INPUT_BORDER,
            highlightcolor=self.ACCENT_COLOR,
            font=(self.font_family, 9),
            height=6
        )
        self.img_listbox.pack(side="left", fill="both", expand=True)
        self.img_listbox.bind("<<ListboxSelect>>", lambda e: self.on_img_to_pdf_select())
        
        scrollbar_img = tk.Scrollbar(list_container, orient="vertical", command=self.img_listbox.yview, bg=self.CARD_COLOR, bd=0)
        scrollbar_img.pack(side="right", fill="y")
        self.img_listbox.config(yscrollcommand=scrollbar_img.set)
        
        img_btn_frame = tk.Frame(list_btn_row, bg=self.CARD_COLOR)
        img_btn_frame.pack(side="right", fill="y", padx=(10, 0))
        
        self.img_up_btn = self.create_button(
            img_btn_frame, text="⬆️ Move Up", command=lambda: self.move_img_to_pdf_file(-1),
            bg_color=self.CARD_COLOR, fg_color=self.TEXT_COLOR, hover_color=self.INPUT_BORDER
        )
        self.img_up_btn.pack(fill="x", pady=(0, 5))
        
        self.img_down_btn = self.create_button(
            img_btn_frame, text="⬇️ Move Down", command=lambda: self.move_img_to_pdf_file(1),
            bg_color=self.CARD_COLOR, fg_color=self.TEXT_COLOR, hover_color=self.INPUT_BORDER
        )
        self.img_down_btn.pack(fill="x", pady=(0, 15))
        
        self.img_remove_btn = self.create_button(
            img_btn_frame, text="🗑️ Remove", command=self.remove_img_to_pdf_file,
            bg_color=self.CARD_COLOR, fg_color=self.ERROR_COLOR, hover_color=self.INPUT_BORDER
        )
        self.img_remove_btn.pack(fill="x", pady=(0, 5))
        
        self.img_clear_btn = self.create_button(
            img_btn_frame, text="🧹 Clear All", command=self.clear_img_to_pdf_files,
            bg_color=self.CARD_COLOR, fg_color=self.ERROR_COLOR, hover_color=self.INPUT_BORDER
        )
        self.img_clear_btn.pack(fill="x")
        
        # 3. Save Destination Card
        img_save_card = tk.Frame(self.img_to_pdf_content_frame, bg=self.CARD_COLOR, padx=15, pady=15)
        img_save_card.pack(fill="x")
        
        img_save_header = tk.Label(
            img_save_card, text="💾 Save Destination", fg=self.TEXT_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 11, "bold")
        )
        img_save_header.pack(anchor="w", pady=(0, 8))
        
        img_save_row = tk.Frame(img_save_card, bg=self.CARD_COLOR)
        img_save_row.pack(fill="x")
        
        self.img_save_entry = tk.Entry(
            img_save_row,
            textvariable=self.img_to_pdf_save_path,
            state="readonly",
            readonlybackground=self.INPUT_BG,
            fg=self.TEXT_COLOR,
            bd=0,
            highlightthickness=1,
            highlightbackground=self.INPUT_BORDER,
            highlightcolor=self.ACCENT_COLOR,
            font=(self.font_family, 10),
            width=40
        )
        self.img_save_entry.pack(side="left", fill="x", expand=True, padx=(0, 10), ipady=5)
        
        self.img_save_btn = self.create_button(
            img_save_row, text="Change...", command=self.browse_img_to_pdf_output,
            bg_color=self.ACCENT_COLOR, fg_color="#11111b", hover_color=self.ACCENT_HOVER
        )
        self.img_save_btn.pack(side="right")
        self.img_save_btn.config(state="disabled")

    def build_compress_ui(self):
        """Construct the PDF Compression tab workspace layout."""
        # 1. Title/Header Frame
        comp_title_card = tk.Frame(self.compress_content_frame, bg=self.CARD_COLOR, padx=15, pady=15)
        comp_title_card.pack(fill="x", pady=(0, 15))
        
        comp_title_lbl = tk.Label(
            comp_title_card, text="🗜️ Compress PDF", fg=self.TEXT_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 12, "bold")
        )
        comp_title_lbl.pack(anchor="w")
        
        comp_desc_lbl = tk.Label(
            comp_title_card, text="Reduce file size of your PDF document. Ideal for emails and website uploads.",
            fg=self.MUTED_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 9)
        )
        comp_desc_lbl.pack(anchor="w", pady=(5, 0))

        # 2. File Selection Card
        file_card = tk.Frame(self.compress_content_frame, bg=self.CARD_COLOR, padx=15, pady=15)
        file_card.pack(fill="x", pady=(0, 15))
        
        file_header = tk.Label(
            file_card, text="📂 Select PDF File", fg=self.TEXT_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 11, "bold")
        )
        file_header.pack(anchor="w", pady=(0, 8))
        
        file_row = tk.Frame(file_card, bg=self.CARD_COLOR)
        file_row.pack(fill="x")
        
        self.comp_input_entry = tk.Entry(
            file_row,
            textvariable=self.compress_input_path,
            state="readonly",
            readonlybackground=self.INPUT_BG,
            fg=self.TEXT_COLOR,
            bd=0,
            highlightthickness=1,
            highlightbackground=self.INPUT_BORDER,
            highlightcolor=self.ACCENT_COLOR,
            font=(self.font_family, 10),
            width=40
        )
        self.comp_input_entry.pack(side="left", fill="x", expand=True, padx=(0, 10), ipady=5)
        
        self.comp_select_btn = self.create_button(
            file_row, text="Browse...", command=self.browse_compress_input,
            bg_color=self.ACCENT_COLOR, fg_color="#11111b", hover_color=self.ACCENT_HOVER
        )
        self.comp_select_btn.pack(side="right")
        
        # Info Label (original size, page count)
        self.comp_info_lbl = tk.Label(
            file_card, text="No PDF loaded.", fg=self.MUTED_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 9)
        )
        self.comp_info_lbl.pack(anchor="w", pady=(8, 0))

        # 3. Compression Settings Card (Slider)
        settings_card = tk.Frame(self.compress_content_frame, bg=self.CARD_COLOR, padx=15, pady=15)
        settings_card.pack(fill="x", pady=(0, 15))
        
        settings_header = tk.Label(
            settings_card, text="🗜️ Compression Level", fg=self.TEXT_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 11, "bold")
        )
        settings_header.pack(anchor="w", pady=(0, 5))
        
        # Segmented Button Frame
        slider_frame = tk.Frame(settings_card, bg=self.CARD_COLOR)
        slider_frame.pack(fill="x", pady=10)
        
        self.comp_low_btn = tk.Button(
            slider_frame, text="Low (Max Quality)", command=lambda: self.set_compression_level(1),
            bd=0, relief="flat", font=(self.font_family, 9, "bold"), padx=10, pady=8, cursor="hand2"
        )
        self.comp_low_btn.pack(side="left", fill="x", expand=True, padx=(0, 5))
        
        self.comp_med_btn = tk.Button(
            slider_frame, text="Medium (Recommended)", command=lambda: self.set_compression_level(2),
            bd=0, relief="flat", font=(self.font_family, 9, "bold"), padx=10, pady=8, cursor="hand2"
        )
        self.comp_med_btn.pack(side="left", fill="x", expand=True, padx=5)
        
        self.comp_high_btn = tk.Button(
            slider_frame, text="High (Smallest Size)", command=lambda: self.set_compression_level(3),
            bd=0, relief="flat", font=(self.font_family, 9, "bold"), padx=10, pady=8, cursor="hand2"
        )
        self.comp_high_btn.pack(side="left", fill="x", expand=True, padx=(5, 0))
        
        # Dynamic Description Labels
        self.comp_level_lbl = tk.Label(
            settings_card, text="Medium (Standard)", fg=self.ACCENT_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 10, "bold")
        )
        self.comp_level_lbl.pack(anchor="w")
        
        self.comp_desc_lbl = tk.Label(
            settings_card, 
            text="Recommended. Downscales high-resolution images to 150 DPI (optimal resolution for standard screens/emails) and compresses them to 75% quality.\n\nGreat size reduction with virtually no visible difference.",
            fg=self.MUTED_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 9), justify="left", wraplength=400
        )
        self.comp_desc_lbl.pack(anchor="w", pady=(5, 0))
        
        # Bind parent frame Configure to dynamically update description wrap width to fill the card
        settings_card.bind("<Configure>", lambda e: self.comp_desc_lbl.config(wraplength=e.width - 30))

        # Set default active state
        self.set_compression_level(2)

        # 4. Save Destination Card
        comp_save_card = tk.Frame(self.compress_content_frame, bg=self.CARD_COLOR, padx=15, pady=15)
        comp_save_card.pack(fill="x")
        
        comp_save_header = tk.Label(
            comp_save_card, text="💾 Save Destination", fg=self.TEXT_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 11, "bold")
        )
        comp_save_header.pack(anchor="w", pady=(0, 8))
        
        comp_save_row = tk.Frame(comp_save_card, bg=self.CARD_COLOR)
        comp_save_row.pack(fill="x")
        
        self.comp_save_entry = tk.Entry(
            comp_save_row,
            textvariable=self.compress_save_path,
            state="readonly",
            readonlybackground=self.INPUT_BG,
            fg=self.TEXT_COLOR,
            bd=0,
            highlightthickness=1,
            highlightbackground=self.INPUT_BORDER,
            highlightcolor=self.ACCENT_COLOR,
            font=(self.font_family, 10),
            width=40
        )
        self.comp_save_entry.pack(side="left", fill="x", expand=True, padx=(0, 10), ipady=5)
        
        self.comp_save_btn = self.create_button(
            comp_save_row, text="Change...", command=self.browse_compress_output,
            bg_color=self.ACCENT_COLOR, fg_color="#11111b", hover_color=self.ACCENT_HOVER
        )
        self.comp_save_btn.pack(side="right")
        self.comp_save_btn.config(state="disabled")

    def browse_img_to_pdf_images(self):
        """Browse and add multiple image files to the queue."""
        files = filedialog.askopenfilenames(
            title="Select Images to Convert",
            filetypes=[("Image Files", "*.png;*.jpg;*.jpeg;*.bmp;*.gif")]
        )
        if files:
            for file in files:
                if file not in self.img_to_pdf_files:
                    self.img_to_pdf_files.append(file)
            self.update_img_to_pdf_listbox()
            # Set default save path if not set yet
            if not self.img_to_pdf_save_path.get() and self.img_to_pdf_files:
                base_dir = os.path.dirname(self.img_to_pdf_files[0])
                self.img_to_pdf_save_path.set(os.path.join(base_dir, "converted_images.pdf"))
                self.img_save_btn.config(state="normal")
            self.update_img_to_pdf_ui_state()
            # Preview the first added/selected image
            if self.img_to_pdf_files:
                self.img_listbox.select_clear(0, "end")
                self.img_listbox.select_set(0)
                self.img_listbox.activate(0)
                self.on_img_to_pdf_select()

    def browse_img_to_pdf_output(self):
        """Browse output path for the converted PDF."""
        file_path = filedialog.asksaveasfilename(
            title="Save Converted PDF As",
            defaultextension=".pdf",
            filetypes=[("PDF Files", "*.pdf")],
            initialfile=os.path.basename(self.img_to_pdf_save_path.get())
        )
        if file_path:
            self.img_to_pdf_save_path.set(file_path)

    def update_img_to_pdf_listbox(self):
        """Redraw listbox with short names of queued image files."""
        self.img_listbox.delete(0, "end")
        for file in self.img_to_pdf_files:
            self.img_listbox.insert("end", os.path.basename(file))

    def move_img_to_pdf_file(self, direction):
        """Move selected image up/down in sequence."""
        selected = self.img_listbox.curselection()
        if selected:
            idx = selected[0]
            new_idx = idx + direction
            if 0 <= new_idx < len(self.img_to_pdf_files):
                # Swap elements
                self.img_to_pdf_files[idx], self.img_to_pdf_files[new_idx] = self.img_to_pdf_files[new_idx], self.img_to_pdf_files[idx]
                self.update_img_to_pdf_listbox()
                self.img_listbox.select_set(new_idx)
                self.img_listbox.activate(new_idx)
                self.on_img_to_pdf_select()

    def remove_img_to_pdf_file(self):
        """Remove selected image from the queue."""
        selected = self.img_listbox.curselection()
        if selected:
            idx = selected[0]
            self.img_to_pdf_files.pop(idx)
            self.update_img_to_pdf_listbox()
            if self.img_to_pdf_files:
                new_idx = min(idx, len(self.img_to_pdf_files) - 1)
                self.img_listbox.select_set(new_idx)
                self.img_listbox.activate(new_idx)
                self.on_img_to_pdf_select()
            else:
                self.clear_preview_document()
            self.update_img_to_pdf_ui_state()

    def clear_img_to_pdf_files(self):
        """Clear all queued images."""
        self.img_to_pdf_files = []
        self.img_to_pdf_save_path.set("")
        self.update_img_to_pdf_listbox()
        self.clear_preview_document()
        self.update_img_to_pdf_ui_state()

    def on_img_to_pdf_select(self):
        """Triggered when user clicks an image item in the queue."""
        selected = self.img_listbox.curselection()
        if selected:
            idx = selected[0]
            img_path = self.img_to_pdf_files[idx]
            self.load_preview_document(img_path)

    def update_img_to_pdf_ui_state(self):
        """Validate input/output configuration and update action button state."""
        if self.current_tab == "img_to_pdf":
            if len(self.img_to_pdf_files) > 0:
                if self.img_to_pdf_save_path.get():
                    self.action_btn.config(state="normal")
                    self.status_message.set("Ready to compile PDF. Click 'COMPILE PDF' to execute.")
                    self.status_lbl.config(fg=self.TEXT_COLOR)
                else:
                    self.action_btn.config(state="disabled")
                    self.status_message.set("Specify a save destination.")
                    self.status_lbl.config(fg=self.MUTED_COLOR)
            else:
                self.action_btn.config(state="disabled")
                self.status_message.set("Add images to begin.")
                self.status_lbl.config(fg=self.MUTED_COLOR)

    def browse_compress_input(self):
        """Browse and select a PDF file to compress."""
        file_path = filedialog.askopenfilename(
            title="Select PDF to Compress",
            filetypes=[("PDF Files", "*.pdf")]
        )
        if file_path:
            self.compress_input_path.set(file_path)
            try:
                doc = fitz.open(file_path)
                self.compress_pdf_pages = len(doc)
                doc.close()
                self.compress_pdf_size_bytes = os.path.getsize(file_path)
                self.comp_info_lbl.config(
                    text=f"Loaded: {self.compress_pdf_pages} pages | Original size: {format_file_size(self.compress_pdf_size_bytes)}",
                    fg=self.TEXT_COLOR
                )
                # Auto set save destination
                base_dir = os.path.dirname(file_path)
                name, ext = os.path.splitext(os.path.basename(file_path))
                self.compress_save_path.set(os.path.join(base_dir, f"{name}_compressed.pdf"))
                self.comp_save_btn.config(state="normal")
                # Preview document
                self.load_preview_document(file_path)
            except Exception as e:
                self.comp_info_lbl.config(text=f"Error reading file: {str(e)}", fg=self.ERROR_COLOR)
                self.compress_pdf_pages = 0
                self.compress_pdf_size_bytes = 0
                self.clear_preview_document()
            self.update_compress_ui_state()

    def browse_compress_output(self):
        """Select save location for compressed PDF."""
        file_path = filedialog.asksaveasfilename(
            title="Save Compressed PDF As",
            defaultextension=".pdf",
            filetypes=[("PDF Files", "*.pdf")],
            initialfile=os.path.basename(self.compress_save_path.get())
        )
        if file_path:
            self.compress_save_path.set(file_path)

    def set_compression_level(self, level):
        """Set active compression level and update button styles."""
        self.compress_level.set(level)
        self.update_compression_description()
        
        # Reset all button styles
        for lvl, btn in [(1, self.comp_low_btn), (2, self.comp_med_btn), (3, self.comp_high_btn)]:
            if lvl == level:
                btn.config(bg=self.ACCENT_COLOR, fg="#11111b")
                # Bind hover colors to active color so it doesn't flicker
                btn.bind("<Enter>", lambda e, b=btn: self.on_button_hover(b, self.ACCENT_COLOR))
                btn.bind("<Leave>", lambda e, b=btn: self.on_button_hover(b, self.ACCENT_COLOR))
            else:
                btn.config(bg=self.INPUT_BG, fg=self.TEXT_COLOR)
                btn.bind("<Enter>", lambda e, b=btn: self.on_button_hover(b, self.INPUT_BORDER))
                btn.bind("<Leave>", lambda e, b=btn: self.on_button_hover(b, self.INPUT_BG))

    def update_compression_description(self, value=None):
        """Update dynamic text explanation based on slider level value."""
        level = self.compress_level.get()
        if level == 1:
            self.comp_level_lbl.config(text="Low Compression (Maximum Quality)")
            self.comp_desc_lbl.config(text="Cleans up background metadata, merges duplicate fonts, and deflates text streams.\n\nKeeps 100% of original image quality.")
        elif level == 2:
            self.comp_level_lbl.config(text="Medium Compression (Recommended)")
            self.comp_desc_lbl.config(text="Recommended. Downscales high-resolution images to 150 DPI (optimal resolution for standard screens/emails) and compresses them to 75% quality.\n\nGreat size reduction with virtually no visible difference.")
        elif level == 3:
            self.comp_level_lbl.config(text="High Compression (Smallest Size)")
            self.comp_desc_lbl.config(text="Maximum compression. Downscales images heavily to 72 DPI (low resolution) and compresses them to 50% quality.\n\nUse this to bypass strict web upload limits when text readability is key, but image clarity is less critical.")

    def update_compress_ui_state(self):
        """Validate compression configuration and update action button state."""
        if self.current_tab == "compress":
            if self.compress_input_path.get():
                if self.compress_save_path.get():
                    self.action_btn.config(state="normal")
                    self.status_message.set("Ready to compress PDF. Click 'COMPRESS PDF' to execute.")
                    self.status_lbl.config(fg=self.TEXT_COLOR)
                else:
                    self.action_btn.config(state="disabled")
                    self.status_message.set("Specify a save destination.")
                    self.status_lbl.config(fg=self.MUTED_COLOR)
            else:
                self.action_btn.config(state="disabled")
                self.status_message.set("Select a PDF to begin.")
                self.status_lbl.config(fg=self.MUTED_COLOR)

    def start_img_to_pdf(self):
        """Start background Images to PDF compilation."""
        if not self.img_to_pdf_files:
            return
            
        save_path = self.img_to_pdf_save_path.get()
        if not save_path:
            messagebox.showwarning("Save Destination", "Please specify a save destination.")
            return
            
        self.is_processing = True
        self.set_ui_state("disabled")
        self.open_folder_btn.pack_forget()
        self.status_message.set("Compiling images to PDF...")
        self.status_lbl.config(fg=self.ACCENT_COLOR)
        self.progress_bar.set_progress(0.0)
        
        threading.Thread(
            target=run_img_to_pdf,
            args=(
                self.img_to_pdf_files,
                save_path,
                self.msg_queue
            ),
            daemon=True
        ).start()
        
        self.root.after(100, self.check_progress)

    def start_compression(self):
        """Start background PDF compression."""
        input_path = self.compress_input_path.get()
        if not input_path:
            return
            
        save_path = self.compress_save_path.get()
        if not save_path:
            messagebox.showwarning("Save Destination", "Please specify a save destination.")
            return
            
        self.is_processing = True
        self.set_ui_state("disabled")
        self.open_folder_btn.pack_forget()
        self.status_message.set("Compressing PDF file...")
        self.status_lbl.config(fg=self.ACCENT_COLOR)
        self.progress_bar.set_progress(0.0)
        
        threading.Thread(
            target=run_pdf_compress,
            args=(
                input_path,
                save_path,
                self.compress_level.get(),
                self.msg_queue
            ),
            daemon=True
        ).start()
        
        self.root.after(100, self.check_progress)

    def build_edit_ui(self):
        """Build the Sign & Redact UI Panel inside self.edit_content_frame."""
        # 1. File Input Card
        file_card = tk.Frame(self.edit_content_frame, bg=self.CARD_COLOR, padx=15, pady=15)
        file_card.pack(fill="x", pady=(0, 15))
        
        file_header_lbl = tk.Label(
            file_card, text="📄 Input PDF Document", fg=self.TEXT_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 11, "bold")
        )
        file_header_lbl.pack(anchor="w", pady=(0, 8))
        
        file_row = tk.Frame(file_card, bg=self.CARD_COLOR)
        file_row.pack(fill="x")
        
        self.edit_file_entry = tk.Entry(
            file_row,
            textvariable=self.edit_input_path,
            state="readonly",
            readonlybackground=self.INPUT_BG,
            fg=self.TEXT_COLOR,
            bd=0,
            highlightthickness=1,
            highlightbackground=self.INPUT_BORDER,
            highlightcolor=self.ACCENT_COLOR,
            font=(self.font_family, 10),
            width=40
        )
        self.edit_file_entry.pack(side="left", fill="x", expand=True, padx=(0, 10), ipady=5)
        
        self.edit_select_btn = self.create_button(
            file_row, text="Browse...", command=self.browse_edit_input_file,
            bg_color=self.ACCENT_COLOR, fg_color="#11111b", hover_color=self.ACCENT_HOVER
        )
        self.edit_select_btn.pack(side="right")
        
        self.edit_file_details_lbl = tk.Label(
            file_card, text="No PDF file loaded.", fg=self.MUTED_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 9, "italic")
        )
        self.edit_file_details_lbl.pack(anchor="w", pady=(6, 0))

        # 2. Tool Segmented Selector Card
        tools_card = tk.Frame(self.edit_content_frame, bg=self.CARD_COLOR, padx=15, pady=15)
        tools_card.pack(fill="x", pady=(0, 15))
        
        tools_header_lbl = tk.Label(
            tools_card, text="🛠️ Edit Tools", fg=self.TEXT_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 11, "bold")
        )
        tools_header_lbl.pack(anchor="w", pady=(0, 8))
        
        tools_row = tk.Frame(tools_card, bg=self.CARD_COLOR)
        tools_row.pack(fill="x", pady=(0, 10))
        
        self.edit_tool_buttons = {}
        tool_options = [
            ("pan", "✋ Move/Pan"),
            ("sign", "✍️ Add Signature"),
            ("redact", "🥷 Redact Page")
        ]
        for tool_id, label in tool_options:
            btn = tk.Button(
                tools_row, text=label, bd=0, relief="flat", padx=12, pady=6, cursor="hand2",
                font=(self.font_family, 9, "bold")
            )
            btn.config(command=lambda tid=tool_id: self.set_edit_tool(tid))
            btn.pack(side="left", padx=(0, 10), fill="x", expand=True)
            self.edit_tool_buttons[tool_id] = btn
            
        self.set_edit_tool("pan")

        # 3. Signature Drawing Pad Card
        self.sig_card = tk.Frame(self.edit_content_frame, bg=self.CARD_COLOR, padx=15, pady=15)
        self.sig_card.pack(fill="x", pady=(0, 15))
        
        sig_header_lbl = tk.Label(
            self.sig_card, text="✍️ Signature Pad", fg=self.TEXT_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 11, "bold")
        )
        sig_header_lbl.pack(anchor="w", pady=(0, 4))
        
        sig_sub_lbl = tk.Label(
            self.sig_card, text="Draw signature below using your mouse.", fg=self.MUTED_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 9)
        )
        sig_sub_lbl.pack(anchor="w", pady=(0, 8))
        
        sig_canvas_frame = tk.Frame(self.sig_card, bg=self.INPUT_BG, highlightthickness=1, highlightbackground=self.INPUT_BORDER)
        sig_canvas_frame.pack(fill="x", pady=(0, 8))
        
        self.sig_canvas = tk.Canvas(
            sig_canvas_frame, bg=self.INPUT_BG, height=100, highlightthickness=0, cursor="pencil"
        )
        self.sig_canvas.pack(fill="both", expand=True)
        
        # Bind signature pad drawings
        self.sig_canvas.bind("<ButtonPress-1>", self.on_sig_start)
        self.sig_canvas.bind("<B1-Motion>", self.on_sig_draw)
        
        sig_buttons_row = tk.Frame(self.sig_card, bg=self.CARD_COLOR)
        sig_buttons_row.pack(fill="x")
        
        self.clear_sig_btn = self.create_button(
            sig_buttons_row, text="🧹 Clear Pad", command=self.clear_sig_pad,
            bg_color=self.CARD_COLOR, fg_color=self.TEXT_COLOR, hover_color=self.INPUT_BORDER
        )
        self.clear_sig_btn.pack(side="left")

        # 4. Save Location Card Frame
        save_card = tk.Frame(self.edit_content_frame, bg=self.CARD_COLOR, padx=15, pady=15)
        save_card.pack(fill="x")
        
        save_header_lbl = tk.Label(
            save_card, text="💾 Save Destination", fg=self.TEXT_COLOR, bg=self.CARD_COLOR, font=(self.font_family, 11, "bold")
        )
        save_header_lbl.pack(anchor="w", pady=(0, 8))
        
        save_row = tk.Frame(save_card, bg=self.CARD_COLOR)
        save_row.pack(fill="x")
        
        self.edit_save_entry = tk.Entry(
            save_row,
            textvariable=self.edit_save_path,
            state="readonly",
            readonlybackground=self.INPUT_BG,
            fg=self.TEXT_COLOR,
            bd=0,
            highlightthickness=1,
            highlightbackground=self.INPUT_BORDER,
            highlightcolor=self.ACCENT_COLOR,
            font=(self.font_family, 10),
            width=40
        )
        self.edit_save_entry.pack(side="left", fill="x", expand=True, padx=(0, 10), ipady=5)
        
        self.edit_save_browse_btn = self.create_button(
            save_row, text="Browse...", command=self.browse_edit_save_file,
            bg_color=self.CARD_COLOR, fg_color=self.TEXT_COLOR, hover_color=self.INPUT_BORDER
        )
        self.edit_save_browse_btn.pack(side="right")

    def browse_edit_input_file(self):
        """Trigger native dialog to select a source PDF for editing/signing/redacting."""
        file_path = filedialog.askopenfilename(
            title="Select PDF Document to Edit", filetypes=[("PDF Files", "*.pdf")]
        )
        if file_path:
            self.edit_input_path.set(file_path)
            
            try:
                reader = PdfReader(file_path)
                self.edit_pdf_pages = len(reader.pages)
                file_size = os.path.getsize(file_path)
                self.edit_pdf_size_str = format_file_size(file_size)
                
                self.edit_file_details_lbl.config(
                    text=f"Pages: {self.edit_pdf_pages}  |  File Size: {self.edit_pdf_size_str}", fg=self.SUCCESS_COLOR
                )
                
                dir_name, file_name = os.path.split(file_path)
                name_wo_ext, ext = os.path.splitext(file_name)
                suggested_out = os.path.join(dir_name, f"{name_wo_ext}_signed{ext}")
                self.edit_save_path.set(suggested_out)
                
                # Load document for preview
                self.load_preview_document(file_path)
                self.placed_items = []  # Clear previous items
                self.active_edit_item = None
                
            except Exception as e:
                self.edit_file_details_lbl.config(
                    text=f"Failed to load file: {str(e)}", fg=self.ERROR_COLOR
                )
                self.clear_preview_document()
            self.update_edit_ui_state()

    def browse_edit_save_file(self):
        """Trigger native dialog to specify save path for edited PDF."""
        file_path = filedialog.asksaveasfilename(
            title="Save Edited PDF As",
            defaultextension=".pdf",
            filetypes=[("PDF Files", "*.pdf")],
            initialfile=os.path.basename(self.edit_save_path.get()) if self.edit_save_path.get() else ""
        )
        if file_path:
            self.edit_save_path.set(file_path)
            self.update_edit_ui_state()

    def update_edit_ui_state(self):
        """Validate edit configuration and update action button state."""
        if self.current_tab == "edit":
            if self.edit_input_path.get():
                if self.edit_save_path.get():
                    self.action_btn.config(state="normal")
                    self.status_message.set("Ready to save edited PDF. Click 'SAVE EDITS' to burn in signatures & redactions.")
                    self.status_lbl.config(fg=self.TEXT_COLOR)
                else:
                    self.action_btn.config(state="disabled")
                    self.status_message.set("Specify a save destination.")
                    self.status_lbl.config(fg=self.MUTED_COLOR)
            else:
                self.action_btn.config(state="disabled")
                self.status_message.set("Select a PDF to begin.")
                self.status_lbl.config(fg=self.MUTED_COLOR)

    def set_edit_tool(self, tool_id, clear_selection=True):
        """Set active edit tool and update button styles."""
        self.edit_active_tool = tool_id
        
        # Reset all button styles
        for tid, btn in self.edit_tool_buttons.items():
            if tid == tool_id:
                btn.config(bg=self.ACCENT_COLOR, fg="#11111b")
                btn.bind("<Enter>", lambda e, b=btn: self.on_button_hover(b, self.ACCENT_COLOR))
                btn.bind("<Leave>", lambda e, b=btn: self.on_button_hover(b, self.ACCENT_COLOR))
            else:
                btn.config(bg=self.INPUT_BG, fg=self.TEXT_COLOR)
                btn.bind("<Enter>", lambda e, b=btn: self.on_button_hover(b, self.INPUT_BORDER))
                btn.bind("<Leave>", lambda e, b=btn: self.on_button_hover(b, self.INPUT_BG))
                
        # Clear active selection when switching tools
        if clear_selection:
            self.active_edit_item = None
        self.update_canvas_overlays()

    def on_sig_start(self, event):
        """Start drawing a new stroke on the signature pad."""
        if self.is_processing:
            return
        self.signature_strokes.append([(event.x, event.y)])
        self.sig_canvas.create_line(
            event.x, event.y, event.x, event.y, 
            fill=self.TEXT_COLOR, width=2.5, capstyle="round", joinstyle="round", tags="sig_stroke"
        )

    def on_sig_draw(self, event):
        """Track mouse movement to draw lines on the signature pad."""
        if self.is_processing:
            return
        if self.signature_strokes:
            pad_w = self.sig_canvas.winfo_width()
            pad_h = self.sig_canvas.winfo_height()
            x = max(0, min(pad_w, event.x))
            y = max(0, min(pad_h, event.y))
            
            self.signature_strokes[-1].append((x, y))
            pts = self.signature_strokes[-1]
            if len(pts) >= 2:
                self.sig_canvas.create_line(
                    pts[-2][0], pts[-2][1], pts[-1][0], pts[-1][1],
                    fill=self.TEXT_COLOR, width=2.5, capstyle="round", joinstyle="round", tags="sig_stroke"
                )

    def clear_sig_pad(self):
        """Clear all signature strokes and the pad canvas."""
        self.signature_strokes = []
        self.sig_canvas.delete("all")

    def find_item_at_coords(self, cx, cy):
        """Find if a signature or redaction bounding box is at the canvas coordinate (cx, cy)."""
        for item in reversed(self.placed_items):
            if item.get("page") == self.preview_page_idx:
                cx1, cy1 = self.pdf_to_canvas(item["rect"].x0, item["rect"].y0)
                cx2, cy2 = self.pdf_to_canvas(item["rect"].x1, item["rect"].y1)
                
                # Check resize handle first (for both signature and redaction items)
                if (cx2 - 8 <= cx <= cx2 + 8) and (cy2 - 8 <= cy <= cy2 + 8):
                    return item, "resize"
                        
                # Check bounding box
                rx0, rx1 = min(cx1, cx2), max(cx1, cx2)
                ry0, ry1 = min(cy1, cy2), max(cy1, cy2)
                if (rx0 <= cx <= rx1) and (ry0 <= cy <= ry1):
                    return item, "move"
                    
        return None, None

    def on_canvas_click(self, event):
        """Handle preview canvas click in edit mode."""
        if not self.preview_doc:
            return
            
        cx = self.preview_canvas.canvasx(event.x)
        cy = self.preview_canvas.canvasy(event.y)
        
        pdf_x, pdf_y = self.canvas_to_pdf(cx, cy)
        pw, ph = self.get_current_page_dimensions()
        
        if not (0 <= pdf_x <= pw and 0 <= pdf_y <= ph):
            self.active_edit_item = None
            self.update_canvas_overlays()
            return
            
        # First, check if clicked on an existing placed item (to select, move, or resize)
        # Permissive Selection: Allow clicking existing items in any mode, auto-switching to pan mode.
        item, action = self.find_item_at_coords(cx, cy)
        if item:
            if self.edit_active_tool != "pan":
                self.set_edit_tool("pan", clear_selection=False)
            self.active_edit_item = item
            self.drag_start = (cx, cy)
            if action == "resize":
                self.is_resizing = True
            else:
                self.is_resizing = False
                self.drag_offset_x = pdf_x - item["rect"].x0
                self.drag_offset_y = pdf_y - item["rect"].y0
            self.update_canvas_overlays()
            return
            
        # If clicked empty space on the page:
        if self.edit_active_tool == "sign":
            if not self.signature_strokes:
                self.status_message.set("⚠️ Draw a signature on the pad first, then click the page to place.")
                self.status_lbl.config(fg=self.ERROR_COLOR)
                return
                
            all_x = [pt[0] for stroke in self.signature_strokes for pt in stroke]
            all_y = [pt[1] for stroke in self.signature_strokes for pt in stroke]
            if not all_x or not all_y:
                return
            min_x, max_x = min(all_x), max(all_x)
            min_y, max_y = min(all_y), max(all_y)
            w = max(1.0, max_x - min_x)
            h = max(1.0, max_y - min_y)
            
            padding = 2
            w_png = w + 2 * padding
            h_png = h + 2 * padding
            
            h_pdf = 48.0
            w_pdf = h_pdf * (w_png / h_png)
            w_pdf = max(24.0, min(300.0, w_pdf))
            
            x0 = max(0, min(pw - w_pdf, pdf_x - w_pdf / 2))
            y0 = max(0, min(ph - h_pdf, pdf_y - h_pdf / 2))
            
            rect = fitz.Rect(x0, y0, x0 + w_pdf, y0 + h_pdf)
            new_item = {
                "type": "signature",
                "page": self.preview_page_idx,
                "rect": rect,
                "strokes": list(self.signature_strokes)
            }
            self.placed_items.append(new_item)
            
            # Auto-switch to Move/Pan mode and select the placed signature
            self.set_edit_tool("pan", clear_selection=False)
            self.active_edit_item = new_item
            self.update_canvas_overlays()
            
        elif self.edit_active_tool == "redact":
            self.redact_start = (pdf_x, pdf_y)
            self.redact_current = (pdf_x, pdf_y)
            self.active_edit_item = None
            self.update_canvas_overlays()
            
        elif self.edit_active_tool == "pan":
            # Clicked empty space in pan mode: clear selection
            self.active_edit_item = None
            self.update_canvas_overlays()

    def on_canvas_drag(self, event):
        """Handle mouse dragging in edit mode."""
        if not self.preview_doc:
            return
            
        cx = self.preview_canvas.canvasx(event.x)
        cy = self.preview_canvas.canvasy(event.y)
        
        pdf_x, pdf_y = self.canvas_to_pdf(cx, cy)
        pw, ph = self.get_current_page_dimensions()
        
        # If we have an active selected item, we move/resize it when in pan mode
        if self.edit_active_tool == "pan" and self.active_edit_item:
            if self.is_resizing:
                pdf_x = max(self.active_edit_item["rect"].x0 + 10, min(pw, pdf_x))
                pdf_y = max(self.active_edit_item["rect"].y0 + 10, min(ph, pdf_y))
                self.active_edit_item["rect"].x1 = pdf_x
                self.active_edit_item["rect"].y1 = pdf_y
            else:
                rx0 = max(0, min(pw - (self.active_edit_item["rect"].x1 - self.active_edit_item["rect"].x0), pdf_x - self.drag_offset_x))
                ry0 = max(0, min(ph - (self.active_edit_item["rect"].y1 - self.active_edit_item["rect"].y0), pdf_y - self.drag_offset_y))
                w = self.active_edit_item["rect"].x1 - self.active_edit_item["rect"].x0
                h = self.active_edit_item["rect"].y1 - self.active_edit_item["rect"].y0
                self.active_edit_item["rect"] = fitz.Rect(rx0, ry0, rx0 + w, ry0 + h)
            self.update_canvas_overlays()
            
        elif self.edit_active_tool == "redact":
            if self.redact_start:
                pdf_x, pdf_y = self.clamp_pdf_coordinates(pdf_x, pdf_y)
                self.redact_current = (pdf_x, pdf_y)
                self.update_canvas_overlays()

    def on_canvas_release(self, event):
        """Handle mouse button release in edit mode."""
        self.is_resizing = False
        
        if self.edit_active_tool == "redact" and self.redact_start and getattr(self, "redact_current", None):
            x0, y0 = self.redact_start
            x1, y1 = self.redact_current
            rx0, rx1 = min(x0, x1), max(x0, x1)
            ry0, ry1 = min(y0, y1), max(y0, y1)
            
            if (rx1 - rx0 > 2) and (ry1 - ry0 > 2):
                rect = fitz.Rect(rx0, ry0, rx1, ry1)
                new_redact = {
                    "type": "redaction",
                    "page": self.preview_page_idx,
                    "rect": rect
                }
                self.placed_items.append(new_redact)
                self.active_edit_item = new_redact
                
            self.redact_start = None
            self.redact_current = None
            self.update_canvas_overlays()

    def canvas_to_pdf(self, cx, cy):
        """Map Tkinter canvas coordinate (cx, cy) to PDF document space coordinate (pdf_x, pdf_y)."""
        pdf_x = (cx - self.preview_x) / self.preview_scale
        pdf_y = (cy - self.preview_y) / self.preview_scale
        return pdf_x, pdf_y

    def pdf_to_canvas(self, pdf_x, pdf_y):
        """Map PDF document space coordinate (pdf_x, pdf_y) to Tkinter canvas coordinate (cx, cy)."""
        cx = pdf_x * self.preview_scale + self.preview_x
        cy = pdf_y * self.preview_scale + self.preview_y
        return cx, cy

    def get_current_page_dimensions(self):
        """Get the current preview page size in points."""
        if self.preview_doc:
            page = self.preview_doc.load_page(self.preview_page_idx)
            return page.rect.width, page.rect.height
        return 0, 0

    def clamp_pdf_coordinates(self, x, y):
        """Clamp PDF coordinates to the boundaries of the current page."""
        w, h = self.get_current_page_dimensions()
        return max(0, min(w, x)), max(0, min(h, y))

    def draw_item_overlay(self, item):
        """Render a single placed item overlay on the preview canvas."""
        cx1, cy1 = self.pdf_to_canvas(item["rect"].x0, item["rect"].y0)
        cx2, cy2 = self.pdf_to_canvas(item["rect"].x1, item["rect"].y1)
        
        if item["type"] == "signature":
            # Draw signature strokes inside mapped canvas box coordinates (cropped to bounds with matching padding)
            strokes = item["strokes"]
            all_x = [pt[0] for stroke in strokes for pt in stroke]
            all_y = [pt[1] for stroke in strokes for pt in stroke]
            if all_x and all_y:
                min_x, max_x = min(all_x), max(all_x)
                min_y, max_y = min(all_y), max(all_y)
                w = max(1.0, max_x - min_x)
                h = max(1.0, max_y - min_y)
                
                padding = 2
                w_total = w + 2 * padding
                h_total = h + 2 * padding
                
                for stroke in strokes:
                    canvas_coords = []
                    for px, py in stroke:
                        nx = (px - min_x + padding) / w_total
                        ny = (py - min_y + padding) / h_total
                        cx = cx1 + nx * (cx2 - cx1)
                        cy = cy1 + ny * (cy2 - cy1)
                        canvas_coords.extend([cx, cy])
                    if len(canvas_coords) >= 4:
                        self.preview_canvas.create_line(
                            canvas_coords, fill=self.ACCENT_COLOR, width=2, smooth=True, tags="edit_overlay"
                        )
        elif item["type"] == "redaction":
            # Solid opaque black rectangle
            self.preview_canvas.create_rectangle(
                cx1, cy1, cx2, cy2, 
                fill="black", outline="black", tags="edit_overlay"
            )

    def draw_selection_overlay(self, item):
        """Render active selection dashed border and resize handle."""
        cx1, cy1 = self.pdf_to_canvas(item["rect"].x0, item["rect"].y0)
        cx2, cy2 = self.pdf_to_canvas(item["rect"].x1, item["rect"].y1)
        
        # Dash selection outline
        self.preview_canvas.create_rectangle(
            cx1, cy1, cx2, cy2, 
            outline=self.ACCENT_COLOR, dash=(4, 4), width=1.5, tags="edit_overlay"
        )
        
        # Render resize handle at bottom-right for selected items
        hx1, hy1, hx2, hy2 = cx2 - 4, cy2 - 4, cx2 + 4, cy2 + 4
        self.preview_canvas.create_rectangle(
            hx1, hy1, hx2, hy2, fill=self.ACCENT_COLOR, outline="#11111b", tags="edit_overlay"
        )

    def draw_temp_redaction_overlay(self):
        """Draw temporary dashed rectangle during active redaction click-and-drag."""
        x0, y0 = self.redact_start
        x1, y1 = self.redact_current
        rx0, rx1 = min(x0, x1), max(x0, x1)
        ry0, ry1 = min(y0, y1), max(y0, y1)
        
        cx1, cy1 = self.pdf_to_canvas(rx0, ry0)
        cx2, cy2 = self.pdf_to_canvas(rx1, ry1)
        
        self.preview_canvas.create_rectangle(
            cx1, cy1, cx2, cy2, 
            fill="", outline=self.ERROR_COLOR, dash=(3, 3), width=1.5, tags="edit_overlay"
        )

    def update_canvas_overlays(self):
        """Redraw all visual edits and temporary shapes on the canvas overlay layer."""
        if not hasattr(self, "preview_canvas"):
            return
            
        self.preview_canvas.delete("edit_overlay")
        
        if self.current_tab != "edit":
            return
            
        # Draw all placed signatures and redaction zones on the active page
        for item in self.placed_items:
            if item.get("page") == self.preview_page_idx:
                self.draw_item_overlay(item)
                
        # Draw active selection border
        if self.active_edit_item and self.active_edit_item.get("page") == self.preview_page_idx:
            self.draw_selection_overlay(self.active_edit_item)
            
        # Draw temporary shape during dragging
        if self.edit_active_tool == "redact" and self.redact_start and getattr(self, "redact_current", None):
            self.draw_temp_redaction_overlay()

    def on_delete_key(self, event=None):
        """Handle the Delete key press event to remove the selected overlay item."""
        if self.current_tab == "edit" and self.active_edit_item:
            if self.active_edit_item in self.placed_items:
                self.placed_items.remove(self.active_edit_item)
                self.active_edit_item = None
                self.update_canvas_overlays()

    def start_editing(self):
        """Start the background thread worker process to save PDF edits."""
        input_path = self.edit_input_path.get()
        if not input_path:
            return
            
        save_path = self.edit_save_path.get()
        if not save_path:
            messagebox.showwarning("Save Destination", "Please specify a save destination.")
            return
            
        if not self.placed_items:
            messagebox.showwarning("No Edits", "Please place at least one signature or redaction first.")
            return
            
        self.is_processing = True
        self.set_ui_state("disabled")
        self.open_folder_btn.pack_forget()
        self.status_message.set("Applying edits and saving PDF...")
        self.status_lbl.config(fg=self.ACCENT_COLOR)
        self.progress_bar.set_progress(0.0)
        
        threading.Thread(
            target=run_pdf_edit,
            args=(
                input_path,
                save_path,
                self.placed_items,
                self.msg_queue
            ),
            daemon=True
        ).start()
        
        self.root.after(100, self.check_progress)


if __name__ == "__main__":
    root = tk.Tk()
    app = PDFFlipperApp(root)
    root.mainloop()
