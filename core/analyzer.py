"""1단계: 사진 + 상품명 -> 장단점 후보.

호출을 두 번으로 나눈다.
  (1) 상품명이 있으면 웹검색으로 사실 정보를 모으고
  (2) 그 텍스트와 사진을 함께 넣어 구조화된 분석을 받는다.
상품명이 비어 있으면 (1)은 건너뛴다. 비용도 아끼고, 근거 없는 검색으로
엉뚱한 제품 정보가 섞이는 것도 막는다.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import cache, prompts
from .config import Settings
from .gemini import generate_grounded, generate_structured
from .media import PreparedImage, batch_digest
from .schemas import ProductAnalysis


@dataclass
class AnalysisOutcome:
    analysis: ProductAnalysis
    sources: list[str]
    from_cache: bool


class _CachedAnalysis(ProductAnalysis):
    """캐시 파일에 출처까지 같이 담기 위한 확장. LLM 스키마로는 쓰지 않는다."""

    sources: list[str] = []


def analyze_product(
    settings: Settings,
    *,
    images: list[PreparedImage],
    product_name: str,
    use_web_search: bool = True,
) -> AnalysisOutcome:
    if not images:
        raise ValueError("분석하려면 이미지가 최소 한 장 필요합니다.")

    key = batch_digest(images, salt=f"{product_name}|{use_web_search}|v1")

    if settings.cache_enabled:
        hit = cache.load(key, _CachedAnalysis)
        if hit is not None:
            payload = hit.model_dump()
            sources = payload.pop("sources", [])
            return AnalysisOutcome(
                analysis=ProductAnalysis.model_validate(payload),
                sources=sources,
                from_cache=True,
            )

    research_text = ""
    sources: list[str] = []
    if use_web_search and product_name.strip():
        research_text, sources = generate_grounded(
            settings,
            system=prompts.RESEARCH_SYSTEM,
            prompt=prompts.research_user_prompt(product_name.strip()),
        )

    analysis = generate_structured(
        settings,
        model=settings.analysis_model,
        system=prompts.ANALYSIS_SYSTEM,
        prompt=prompts.analysis_user_prompt(product_name, research_text),
        schema=ProductAnalysis,
        images=[(img.data, img.mime_type) for img in images],
        temperature=0.4,
    )

    if settings.cache_enabled:
        cache.save(key, _CachedAnalysis(**analysis.model_dump(), sources=sources))

    return AnalysisOutcome(analysis=analysis, sources=sources, from_cache=False)
