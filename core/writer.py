"""2단계: 선택된 장단점 + 별점 -> 리뷰 변형들."""

from __future__ import annotations

import math

from . import prompts
from .config import Settings
from .gemini import generate_structured
from .schemas import ProductAnalysis, ReviewAngle, ReviewBundle


def suggest_rating(pros: list[ReviewAngle], cons: list[ReviewAngle]) -> int:
    """선택한 장단점 비율에서 별점을 제안한다.

    별점을 먼저 받고 그에 맞춰 장단점을 고르게 하면 순서가 거꾸로다.
    사용자는 실제 경험(무엇이 좋았고 아쉬웠는지)을 먼저 고르고,
    별점은 거기서 파생되는 게 자연스럽다. 물론 UI 에서 덮어쓸 수 있다.

    분모에 +1 을 두는 이유: 체크 하나만으로 양 극단(1점/5점)에 도달하면
    제안값이 너무 예민해진다. 선택이 적을수록 중립 쪽에 머무르게 한다.
    """
    p, c = len(pros), len(cons)
    if p == 0 and c == 0:
        return 4
    score = 3 + 2 * (p - c) / (p + c + 1)
    # round() 는 짝수로 반올림(banker's rounding)해서 3.5 -> 4, 4.5 -> 4 로 갈린다.
    # 제안값은 항상 위로 올림되는 편이 예측 가능하다.
    return max(1, min(5, math.floor(score + 0.5)))


def write_reviews(
    settings: Settings,
    *,
    analysis: ProductAnalysis,
    pros: list[ReviewAngle],
    cons: list[ReviewAngle],
    rating: int,
    purchase_reason: str = "",
    personal_note: str = "",
    tone: str = "담백하게",
    length: str = "길게",
    review_format: str = "구조형",
    variant_count: int = 3,
) -> ReviewBundle:
    if not pros and not cons and not personal_note.strip():
        raise ValueError("장점이나 단점을 하나 이상 고르거나, 직접 경험을 적어주세요.")

    prompt = prompts.writer_user_prompt(
        product_name=analysis.product_name,
        category=analysis.category,
        rating=rating,
        pros=[f"{a.headline} — {a.detail}" for a in pros],
        cons=[f"{a.headline} — {a.detail}" for a in cons],
        purchase_reason=purchase_reason,
        personal_note=personal_note,
        tone=tone,
        length=length,
        review_format=review_format,
        variant_count=variant_count,
    )

    # 문장 다양성이 목적이라 분석 단계보다 온도를 높인다.
    #
    # max_output_tokens 는 일부러 지정하지 않는다. Gemini 3.x 는 사고(thinking)
    # 토큰도 이 상한에 포함시키기 때문에, 본문 길이만 보고 계산한 값을 넣으면
    # 오히려 기본값보다 작아져서 응답이 JSON 중간에 잘린다.
    return generate_structured(
        settings,
        model=settings.writer_model,
        system=prompts.WRITER_SYSTEM,
        prompt=prompt,
        schema=ReviewBundle,
        temperature=1.0,
    )
