"""Uploaded images reach only the decoders of the formats LectureSift accepts."""
from io import BytesIO

from PIL import Image
import pytest

from lecturesift import documents
from lecturesift.errors import LectureSiftError


def _encoded(format_name: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (32, 24), "white").save(output, format=format_name)
    return output.getvalue()


@pytest.mark.parametrize("name,format_name", [
    ("scan.png", "PNG"), ("scan.jpg", "JPEG"), ("scan.webp", "WEBP"), ("scan.tiff", "TIFF"),
])
def test_accepted_image_formats_are_read(tmp_path, name, format_name):
    path = tmp_path / name
    path.write_bytes(_encoded(format_name))
    text, details = documents._extract_image(path, enable_ocr=False)
    assert text == "" and details["pages"] == 1 and details["ocr_required"] is True


@pytest.mark.parametrize("format_name", ["GIF", "BMP", "PCX", "TGA", "ICO"])
def test_a_renamed_image_of_another_format_is_refused_before_decoding(tmp_path, format_name):
    path = tmp_path / "scan.png"
    path.write_bytes(_encoded(format_name))
    with pytest.raises(LectureSiftError) as refused:
        documents._extract_image(path, enable_ocr=False)
    assert refused.value.code == "LS-OCR-04"


def test_presentation_pictures_accept_only_raster_formats_powerpoint_stores():
    for format_name in ("PNG", "JPEG", "GIF", "BMP", "TIFF"):
        with documents._load_pptx_picture_image(_encoded(format_name)) as image:
            assert image.size == (32, 24)
    for format_name in ("PCX", "TGA", "ICO"):
        with pytest.raises(LectureSiftError) as refused:
            documents._load_pptx_picture_image(_encoded(format_name))
        assert refused.value.code == "LS-OCR-04"
