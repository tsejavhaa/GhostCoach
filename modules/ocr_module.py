"""
OCR Module
Extracts text from screen captures using Tesseract.
Preprocessing improves accuracy on code/IDE screenshots.
"""
from PIL import Image, ImageEnhance, ImageFilter


class OCRModule:
    def __init__(self):
        # PSM 6: Assume uniform block of text (best for code/problem statements)
        self.config = "--oem 3 --psm 6"

    def extract_text(self, image: Image.Image) -> str:
        """Extract all text from a PIL Image."""
        try:
            import pytesseract
            processed = self._preprocess(image)
            return pytesseract.image_to_string(processed, config=self.config).strip()
        except Exception as e:
            return f"OCR Error: {e}"

    def extract_text_from_region(
        self, image: Image.Image, x: int, y: int, width: int, height: int
    ) -> str:
        """Crop to region then OCR."""
        region = image.crop((x, y, x + width, y + height))
        return self.extract_text(region)

    # ── Preprocessing ──────────────────────────────────────────────
    def _preprocess(self, image: Image.Image) -> Image.Image:
        """Upscale + enhance contrast for better Tesseract accuracy."""
        # Convert to grayscale
        img = image.convert("L")

        # Scale up 2x — Tesseract works best at ~300 DPI
        w, h = img.size
        img = img.resize((w * 2, h * 2), Image.LANCZOS)

        # Boost contrast
        img = ImageEnhance.Contrast(img).enhance(2.0)

        # Light sharpening
        img = img.filter(ImageFilter.SHARPEN)

        return img
