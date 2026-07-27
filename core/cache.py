"""분석 결과 디스크 캐시.

같은 사진으로 리뷰를 여러 번 다시 뽑는 게 이 도구의 기본 사용 패턴이라,
비싼 1단계(비전 + 웹검색)를 캐싱하면 재생성이 사실상 공짜가 된다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .config import CACHE_DIR

# PEP 695 문법(def load[T: BaseModel])을 쓰지 않는 이유: 그건 Python 3.12 부터라
# 배포 환경의 파이썬이 낮으면 임포트 단계에서 바로 죽는다.
T = TypeVar("T", bound=BaseModel)


def _path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def load(key: str, model: type[T]) -> T | None:
    path = _path(key)
    if not path.exists():
        return None
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValidationError, json.JSONDecodeError, OSError):
        # 스키마가 바뀌었거나 파일이 깨진 경우. 캐시는 버리고 새로 만든다.
        path.unlink(missing_ok=True)
        return None


def save(key: str, value: BaseModel) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _path(key).write_text(
        value.model_dump_json(indent=2), encoding="utf-8"
    )


def clear() -> int:
    if not CACHE_DIR.exists():
        return 0
    files = list(CACHE_DIR.glob("*.json"))
    for f in files:
        f.unlink(missing_ok=True)
    return len(files)
