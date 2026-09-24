"""Small decodable bytes for tests which exercise the real publication path."""

from io import BytesIO
from PIL import Image


def png_bytes():
    output = BytesIO()
    Image.new("RGB", (16, 16), "#247a62").save(output, format="PNG")
    return output.getvalue()
