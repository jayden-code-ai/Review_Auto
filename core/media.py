"""업로드된 이미지를 API 전송용으로 정규화한다.

원본을 그대로 보내면 요즘 폰 사진은 한 장에 수백만 픽셀이라 토큰 낭비가 크다.
긴 변 기준으로 축소하고 JPEG 로 다시 인코딩해 전송량을 줄인다.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

from PIL import Image, ImageOps

# 아이폰에서 찍은 사진은 기본이 HEIC 다. Pillow 혼자서는 못 읽으므로 디코더를
# 등록해준다. 없는 환경에서도 앱은 떠야 하니 실패를 삼킨다 (HEIC 업로드만 막힘).
try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIC_SUPPORTED = True
except Exception:  # noqa: BLE001
    HEIC_SUPPORTED = False

MAX_IMAGES = 5


def supported_upload_types() -> list[str]:
    types = ["png", "jpg", "jpeg", "webp"]
    if HEIC_SUPPORTED:
        types += ["heic", "heif"]
    return types


@dataclass(frozen=True)
class PreparedImage:
    data: bytes
    mime_type: str
    digest: str
    width: int
    height: int


def prepare_image(raw: bytes, max_edge: int) -> PreparedImage:
    """EXIF 회전을 반영하고 축소한 뒤 JPEG 바이트로 되돌린다."""
    with Image.open(io.BytesIO(raw)) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        longest = max(img.size)
        if longest > max_edge:
            ratio = max_edge / longest
            new_size = (max(1, round(img.width * ratio)), max(1, round(img.height * ratio)))
            img = img.resize(new_size, Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85, optimize=True)
        data = buf.getvalue()
        return PreparedImage(
            data=data,
            mime_type="image/jpeg",
            digest=hashlib.sha256(data).hexdigest()[:16],
            width=img.width,
            height=img.height,
        )


def prepare_images(raws: list[bytes], max_edge: int) -> list[PreparedImage]:
    return [prepare_image(raw, max_edge) for raw in raws[:MAX_IMAGES]]


def batch_digest(images: list[PreparedImage], *, salt: str = "") -> str:
    """이미지 묶음 + 상품명 조합을 하나의 캐시 키로 압축한다."""
    joined = "|".join(img.digest for img in images) + "::" + salt.strip().lower()
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:24]
