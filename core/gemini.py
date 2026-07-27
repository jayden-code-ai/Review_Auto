"""google-genai 클라이언트 래퍼.

구조화 출력과 그라운딩을 한 번의 호출에 같이 요구하면 모델/버전에 따라
거부되거나 스키마가 깨진다. 그래서 이 프로젝트는 두 기능을 항상 별도 호출로 분리한다.
"""

from __future__ import annotations

import difflib
from functools import lru_cache
from typing import TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel

from .config import Settings

# PEP 695 제네릭은 Python 3.12+ 전용이라 배포 환경을 가린다. TypeVar 로 쓴다.
T = TypeVar("T", bound=BaseModel)


class GeminiError(RuntimeError):
    pass


def _wrap(exc: Exception, model: str, stage: str) -> GeminiError:
    """API 예외를 사용자가 다음 행동을 알 수 있는 메시지로 바꾼다.

    특히 모델 이름 오류는 구글이 모델을 교체할 때마다 재발하므로,
    원문 대신 어디를 고쳐야 하는지 알려준다.
    """
    raw = str(exc)

    if "NOT_FOUND" in raw or "is not found for API version" in raw:
        return GeminiError(
            f"{stage} 실패: '{model}' 이라는 모델이 없습니다.\n\n"
            "왼쪽 사이드바의 '문제 해결' → '사용 가능한 모델 확인' 을 눌러 "
            "지금 쓸 수 있는 이름을 확인한 뒤 `.env` 파일의 값을 바꾸고, "
            "브라우저를 새로고침하세요."
        )

    if "PERMISSION_DENIED" in raw or "API key not valid" in raw:
        return GeminiError(
            f"{stage} 실패: API 키가 거부되었습니다. "
            "`.env` 의 GEMINI_API_KEY 값을 확인하세요."
        )

    if "RESOURCE_EXHAUSTED" in raw or "429" in raw:
        return GeminiError(
            f"{stage} 실패: '{model}' 의 호출 한도를 넘었습니다.\n\n"
            "잠시 뒤 다시 시도하거나, `.env` 에서 더 가벼운 모델로 바꾸세요. "
            "preview 모델은 무료 한도가 특히 빡빡합니다."
        )

    return GeminiError(f"{stage} 실패 ({model}): {raw}")


@lru_cache(maxsize=4)
def get_client(api_key: str) -> genai.Client:
    return genai.Client(api_key=api_key)


def list_available_models(api_key: str) -> list[str]:
    """이 API 키로 generateContent 를 호출할 수 있는 모델 이름 목록."""
    client = get_client(api_key)
    names: list[str] = []
    for model in client.models.list():
        actions = getattr(model, "supported_actions", None) or []
        if actions and "generateContent" not in actions:
            continue
        name = (model.name or "").removeprefix("models/")
        if name:
            names.append(name)
    return sorted(names)


def validate_models(settings: Settings, available: list[str]) -> dict[str, list[str]]:
    """설정된 모델 중 실제로 없는 것을 찾아 대안과 함께 돌려준다.

    호출을 보내고 나서 404 를 받는 대신, 화면을 그리는 시점에 미리 잡는다.
    반환값: {설정이름: [비슷한 실제 모델 후보]}. 비어 있으면 모두 정상.
    """
    if not available:
        return {}

    configured = {
        "RESEARCH_MODEL": settings.research_model,
        "ANALYSIS_MODEL": settings.analysis_model,
        "WRITER_MODEL": settings.writer_model,
    }

    problems: dict[str, list[str]] = {}
    for key, name in configured.items():
        if name in available:
            continue
        close = difflib.get_close_matches(name, available, n=3, cutoff=0.5)
        # 이름 유사도로 못 찾으면 같은 계열(flash/pro)에서 아무거나 제안한다.
        if not close:
            family = "pro" if "pro" in name else "flash"
            close = [
                m for m in available if family in m and "preview" not in m
            ][:3]
        problems[key] = close

    return problems


def generate_grounded(
    settings: Settings, *, system: str, prompt: str
) -> tuple[str, list[str]]:
    """웹검색을 붙여 자유 텍스트를 받는다. (본문, 출처 URL 목록) 반환."""
    client = get_client(settings.api_key)
    try:
        response = client.models.generate_content(
            model=settings.research_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system,
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.2,
            ),
        )
    except Exception as exc:
        raise _wrap(exc, settings.research_model, "웹검색 단계") from exc

    return (response.text or "").strip(), _extract_sources(response)


def generate_structured(
    settings: Settings,
    *,
    model: str,
    system: str,
    prompt: str,
    schema: type[T],
    images: list[tuple[bytes, str]] | None = None,
    temperature: float = 0.7,
    max_output_tokens: int | None = None,
) -> T:
    """response_schema 로 파싱된 Pydantic 객체를 받는다."""
    client = get_client(settings.api_key)

    parts: list[types.Part | str] = []
    for data, mime_type in images or []:
        parts.append(types.Part.from_bytes(data=data, mime_type=mime_type))
    parts.append(prompt)

    try:
        response = client.models.generate_content(
            model=model,
            contents=parts,
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                response_schema=schema,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
            ),
        )
    except Exception as exc:
        raise _wrap(exc, model, "생성 단계") from exc

    parsed = response.parsed
    if isinstance(parsed, schema):
        return parsed

    truncated = _finish_reason(response) == "MAX_TOKENS"

    # response.parsed 가 비는 경우(안전 필터, 토큰 소진 등)를 위한 폴백.
    raw = (response.text or "").strip()
    if not raw:
        if truncated:
            raise GeminiError(_TRUNCATED_MESSAGE.format(model=model))
        raise GeminiError(
            f"{model} 이 빈 응답을 돌려줬습니다. "
            "안전 필터에 걸렸을 수 있습니다. 다시 시도해보세요."
        )
    try:
        return schema.model_validate_json(raw)
    except Exception as exc:
        if truncated:
            raise GeminiError(_TRUNCATED_MESSAGE.format(model=model)) from exc
        raise GeminiError(f"{model} 의 응답을 스키마로 해석하지 못했습니다: {exc}") from exc


_TRUNCATED_MESSAGE = (
    "{model} 의 응답이 길이 제한에 걸려 중간에 잘렸습니다.\n\n"
    "'분량' 을 한 단계 줄이거나 '생성 개수' 를 줄인 뒤 다시 시도하세요."
)


def _finish_reason(response: object) -> str:
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return ""
    reason = getattr(candidates[0], "finish_reason", None)
    if reason is None:
        return ""
    # SDK 버전에 따라 Enum 이거나 문자열이다.
    return str(getattr(reason, "name", reason)).upper()


def _extract_sources(response: object) -> list[str]:
    """그라운딩 메타데이터에서 출처 URL 을 모은다. 구조는 SDK 버전마다 조금씩 다르다."""
    urls: list[str] = []
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        metadata = getattr(candidate, "grounding_metadata", None)
        for chunk in getattr(metadata, "grounding_chunks", None) or []:
            web = getattr(chunk, "web", None)
            uri = getattr(web, "uri", None)
            title = getattr(web, "title", None)
            if uri:
                urls.append(f"{title or uri}|{uri}")
    # 순서를 유지한 채 중복 제거
    return list(dict.fromkeys(urls))
