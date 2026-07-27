"""환경 변수 기반 설정.

모델 ID를 코드에 박지 않고 .env 로 빼는 이유: Gemini 모델은 세대 교체가 잦고
API 키마다 접근 가능한 모델이 다르다. 앱에서 list_available_models() 로
실제 목록을 확인한 뒤 .env 만 고치면 되도록 한다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / ".cache"
ENV_PATH = ROOT / ".env"


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    api_key: str
    research_model: str
    analysis_model: str
    writer_model: str
    max_image_edge: int
    cache_enabled: bool


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def load_settings() -> Settings:
    # override=True 가 핵심이다. 기본값(False)이면 프로세스가 이미 들고 있는
    # 환경변수가 이깁니다. 그러면 .env 를 고쳐도 앱을 완전히 재시작하기 전까지
    # 옛 모델 이름이 계속 쓰여서, 화면과 실제 동작이 어긋난다.
    load_dotenv(ENV_PATH, override=True)

    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key or api_key.startswith("여기에"):
        raise ConfigError(
            "GEMINI_API_KEY 가 설정되지 않았습니다. "
            "env.example.txt 를 .env 로 복사한 뒤 키를 채워주세요."
        )

    try:
        max_edge = int(os.getenv("MAX_IMAGE_EDGE", "1280"))
    except ValueError as exc:
        raise ConfigError("MAX_IMAGE_EDGE 는 정수여야 합니다.") from exc

    return Settings(
        api_key=api_key,
        # 기본값은 preview 가 아닌 안정화 모델만 쓴다. preview 는 이름이 바뀌거나
        # 조용히 사라져서 404 를 만든다.
        research_model=os.getenv("RESEARCH_MODEL", "gemini-3.5-flash").strip(),
        analysis_model=os.getenv("ANALYSIS_MODEL", "gemini-3.6-flash").strip(),
        writer_model=os.getenv("WRITER_MODEL", "gemini-3.6-flash").strip(),
        max_image_edge=max_edge,
        cache_enabled=_flag("CACHE_ENABLED", True),
    )
