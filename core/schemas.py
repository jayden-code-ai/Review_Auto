"""Gemini 구조화 출력(response_schema)에 그대로 넘기는 Pydantic 모델.

Gemini 의 스키마 변환기는 Optional/Union 을 잘 다루지 못하므로 모든 필드를
필수로 두고, 값이 없을 때는 빈 문자열이나 빈 리스트를 쓰도록 프롬프트에서 지시한다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# 근거의 출처. 리뷰 생성 시 image > web > common 순으로 신뢰한다.
Basis = Literal["image", "web", "common"]


class SpecFact(BaseModel):
    """제품 사양 한 줄."""

    label: str = Field(description="사양 항목명. 예: 용량, 무게, 소재")
    value: str = Field(description="사양 값. 예: 500ml, 320g, 스테인리스")
    basis: Basis = Field(description="이 정보의 출처")


class ReviewAngle(BaseModel):
    """리뷰에 쓸 수 있는 장점 또는 단점 후보 하나."""

    headline: str = Field(description="8자 내외의 짧은 라벨. 예: 가벼운 무게")
    detail: str = Field(description="왜 장점/단점인지 한 문장 설명")
    basis: Basis = Field(description="이 판단의 근거 출처")


class ProductAnalysis(BaseModel):
    """1단계 분석 결과. 사용자가 이걸 보고 방향을 고른다."""

    product_name: str = Field(description="정규화된 제품명")
    category: str = Field(description="제품 카테고리. 예: 텀블러, 무선이어폰")
    summary: str = Field(description="이미지와 검색 결과를 종합한 2~3문장 요약")
    observed: list[str] = Field(
        description="이미지에서 눈으로 직접 확인되는 사실만. 추측 금지."
    )
    specs: list[SpecFact] = Field(description="확인된 사양 목록")
    pros: list[ReviewAngle] = Field(description="장점 후보 6~8개")
    cons: list[ReviewAngle] = Field(description="단점/아쉬운점 후보 4~6개")
    keywords: list[str] = Field(description="리뷰용 핵심 키워드 8~12개")


class ReviewVariant(BaseModel):
    """생성된 리뷰 한 편."""

    style_label: str = Field(description="이 변형의 성격을 나타내는 짧은 이름")
    title: str = Field(description="리뷰 목록에 노출될 한 줄 제목")
    text: str = Field(description="리뷰 본문")


class ReviewBundle(BaseModel):
    """2단계 생성 결과."""

    keywords: list[str] = Field(description="이번 리뷰에 실제로 반영된 키워드")
    variants: list[ReviewVariant] = Field(description="서로 다른 리뷰 변형들")


class ResearchResult(BaseModel):
    """웹검색(그라운딩) 단계 산출물. LLM 스키마가 아니라 내부 전달용."""

    text: str
    sources: list[str]
