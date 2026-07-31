"""API 키 없이 돌릴 수 있는 자체 점검.

    .venv/bin/python tests/check.py

API 호출이 필요한 부분은 가짜 데이터를 세션 상태에 밀어넣어 검증한다.
실제 Gemini 응답 품질은 여기서 검증할 수 없다. 그건 앱을 띄워서 눈으로 봐야 한다.
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("GEMINI_API_KEY", "dummy-key-for-tests")

from PIL import Image
from streamlit.testing.v1 import AppTest

from core import auth, cache, prompts
from core.analyzer import AnalysisOutcome
from core.config import ConfigError, load_settings
from core.media import batch_digest, prepare_image
from core.schemas import (
    ProductAnalysis,
    ReviewAngle,
    ReviewBundle,
    ReviewVariant,
    SpecFact,
)
from core.writer import suggest_rating, write_reviews

results: list[tuple[str, str]] = []


def check(name: str, fn) -> None:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - 점검 스크립트
        results.append((name, f"실패: {type(exc).__name__}: {exc}"))
    else:
        results.append((name, "통과"))


# --------------------------------------------------------------------------
# 고정 픽스처
# --------------------------------------------------------------------------

FAKE_ANALYSIS = ProductAnalysis(
    product_name="테스트 텀블러 1L",
    category="보온 텀블러",
    summary="스테인리스 재질의 대용량 텀블러로 보입니다. 손잡이가 달려 있습니다.",
    observed=["무광 베이지 색상", "측면 손잡이", "빨대 포함"],
    specs=[
        SpecFact(label="재질", value="스테인리스", basis="image"),
        SpecFact(label="용량", value="1L", basis="web"),
    ],
    pros=[
        ReviewAngle(headline="대용량", detail="하루치 물을 한 번에 담습니다.", basis="web"),
        ReviewAngle(headline="손잡이", detail="한 손으로 들기 편합니다.", basis="image"),
        ReviewAngle(headline="무광 마감", detail="지문이 잘 안 보입니다.", basis="image"),
    ],
    cons=[
        ReviewAngle(headline="무게", detail="가득 채우면 묵직합니다.", basis="common"),
        ReviewAngle(headline="세척", detail="빨대는 따로 닦아야 합니다.", basis="common"),
    ],
    keywords=["대용량", "보온", "손잡이", "스테인리스"],
)

FAKE_BUNDLE = ReviewBundle(
    keywords=["대용량", "손잡이"],
    variants=[
        ReviewVariant(
            style_label="담백한 후기",
            title="하루치 물을 한 번에, 대신 묵직합니다",
            text="물 많이 마시는 편이라 골랐는데 하루 한 번만 채우면 됩니다. "
            "손잡이가 있어서 들고 다니기도 편하고요. 다만 가득 채우면 확실히 묵직합니다.",
        ),
        ReviewVariant(
            style_label="짧은 요약",
            title="용량 하나는 확실한 텀블러",
            text="용량 하나는 확실합니다. 무게는 감수해야 해요.",
        ),
    ],
)


def _angles(n: int) -> list[ReviewAngle]:
    return [ReviewAngle(headline="h", detail="d", basis="image") for _ in range(n)]


def _app(*, logged_in: bool = True) -> AppTest:
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
    if logged_in:
        at.session_state[auth.SESSION_KEY] = True
    at.run()
    return at


# --------------------------------------------------------------------------
# core 단위 점검
# --------------------------------------------------------------------------


def t_settings() -> None:
    s = load_settings()
    assert s.max_image_edge > 0
    assert s.analysis_model and s.writer_model and s.research_model


def t_media() -> None:
    buf = io.BytesIO()
    Image.new("RGB", (4000, 2000), (200, 30, 30)).save(buf, format="PNG")
    prepared = prepare_image(buf.getvalue(), 1280)
    assert max(prepared.width, prepared.height) == 1280
    assert prepared.mime_type == "image/jpeg"
    assert batch_digest([prepared], salt="a") == batch_digest([prepared], salt="a")
    assert batch_digest([prepared], salt="a") != batch_digest([prepared], salt="b")


def t_rating() -> None:
    cases = [
        (0, 0, 4),  # 아무것도 안 고름 -> 중립적 기본값
        (1, 0, 4),  # 장점 하나로 만점이 되면 안 됨
        (3, 0, 5),
        (5, 0, 5),
        (2, 2, 3),  # 균형 -> 보통
        (1, 1, 3),
        (3, 1, 4),
        (1, 2, 3),  # 표본이 적으면 중립에 머묾
        (0, 2, 2),
        (0, 5, 1),
    ]
    for p, c, expected in cases:
        got = suggest_rating(_angles(p), _angles(c))
        assert got == expected, f"pros={p} cons={c} -> {got}, 기대={expected}"

    # 단조성: 단점이 늘어나는데 별점이 오르면 안 된다
    for p in range(5):
        scores = [suggest_rating(_angles(p), _angles(c)) for c in range(6)]
        assert scores == sorted(scores, reverse=True), (p, scores)


def t_prompts() -> None:
    text = prompts.writer_user_prompt(
        product_name="테스트 텀블러",
        category="텀블러",
        rating=4,
        pros=["가벼운 무게 — 한 손에 잡힘"],
        cons=[],
        purchase_reason="쓰던 게 자꾸 새서",
        personal_note="한 달 써보니 뚜껑에서 냄새가 남",
        tone="담백하게",
        length="길게",
        review_format="구조형",
        variant_count=3,
    )
    assert "가벼운 무게" in text
    assert "뚜껑에서 냄새" in text
    assert "쓰던 게 자꾸 새서" in text
    assert "(없음)" in text
    assert "4점" in text
    # 형식 지시가 실제로 프롬프트에 실려야 한다
    assert "구매동기" in text and "총평" in text, "구조형 형식 지시가 누락됐습니다"
    assert "900자" in text, "분량 지시가 누락됐습니다"

    checklist = prompts.writer_user_prompt(
        product_name="X", category="Y", rating=5,
        pros=["a — b"], cons=[], purchase_reason="", personal_note="",
        tone="담백하게", length="아주 길게", review_format="요약+상세형",
        variant_count=1,
    )
    assert "✔" in checklist, "요약+상세형 지시가 누락됐습니다"
    assert "1400자" in checklist

    assert prompts.research_user_prompt("아무거나")
    # 제품명이 없어도 프롬프트가 만들어져야 한다
    assert prompts.analysis_user_prompt("", "")

    # UI 선택지와 프롬프트 가이드가 어긋나지 않아야 한다
    assert set(prompts.FORMAT_GUIDE) == {"문단형", "구조형", "요약+상세형"}


def t_no_output_cap() -> None:
    """출력 토큰 상한을 직접 걸지 않아야 한다.

    Gemini 3.x 는 사고 토큰도 max_output_tokens 에 포함시킨다. 본문 길이만 보고
    계산한 값을 넣으면 기본값보다 작아져서 긴 리뷰가 JSON 중간에 잘린다.
    """
    import inspect

    from core import writer

    # 인자로 넘기는 경우만 잡는다. 등호를 함께 찾으므로, 이유를 적어둔 주석의
    # 언급에는 걸리지 않는다.
    src = inspect.getsource(writer.write_reviews)
    assert "max_output_tokens=" not in src, (
        "write_reviews 가 다시 출력 토큰 상한을 걸고 있습니다"
    )


def t_truncation_detection() -> None:
    """잘린 응답을 알아보고 안내 문구를 주는지."""
    from core.gemini import _finish_reason

    class _Enum:
        name = "MAX_TOKENS"

    class _Cand:
        finish_reason = _Enum()

    class _Resp:
        candidates = [_Cand()]

    assert _finish_reason(_Resp()) == "MAX_TOKENS"

    class _StrCand:
        finish_reason = "stop"

    class _StrResp:
        candidates = [_StrCand()]

    assert _finish_reason(_StrResp()) == "STOP"

    class _Empty:
        candidates = []

    assert _finish_reason(_Empty()) == ""


def t_cache() -> None:
    cache.save("__check_key__", FAKE_ANALYSIS)
    loaded = cache.load("__check_key__", ProductAnalysis)
    assert loaded is not None and loaded.product_name == FAKE_ANALYSIS.product_name
    assert cache.load("__없는_키__", ProductAnalysis) is None
    cache.clear()
    assert cache.load("__check_key__", ProductAnalysis) is None


def t_schema_conversion() -> None:
    """스키마가 Gemini SDK 변환기를 통과하는지. 여기서 막히면 런타임에 터진다."""
    from google.genai import _transformers as tr
    from google.genai import types

    converted = tr.t_schema(None, ProductAnalysis)
    assert converted is not None
    body = str(converted.model_dump(exclude_none=True))
    for field in ("product_name", "pros", "cons", "keywords", "basis"):
        assert field in body, f"{field} 가 변환된 스키마에 없음"

    assert tr.t_schema(None, ReviewBundle) is not None
    types.GenerateContentConfig(
        response_mime_type="application/json", response_schema=ProductAnalysis
    )
    types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())])


def t_non_ascii_api_key() -> None:
    """한글이 섞인 API 키를 호출 전에 잡아내는지.

    실제로 겪은 사고다. Secrets 에 자리표시자가 남아 있으면 요청 헤더를 만들 때
    "'ascii' codec can't encode characters" 라는, 키와 무관해 보이는 오류가 난다.
    """
    from core import config

    # load_settings 는 .env 를 override=True 로 다시 읽는다. 그대로 두면 여기서
    # 넣은 값이 실제 .env 값으로 덮여써져 검증 자체가 무의미해진다.
    saved = os.environ.get("GEMINI_API_KEY")
    saved_path = config.ENV_PATH
    config.ENV_PATH = ROOT / ".env.__없는파일__"
    try:
        for bad_key in ("여기에_API_키", "매일AIzaSyTest", "“AIzaSyTest”"):
            os.environ["GEMINI_API_KEY"] = bad_key
            try:
                load_settings()
            except ConfigError as exc:
                assert "GEMINI_API_KEY" in str(exc), str(exc)
            else:
                raise AssertionError(f"비ASCII 키를 통과시켰습니다: {bad_key!r}")

        # 정상 형태의 키는 통과해야 한다
        os.environ["GEMINI_API_KEY"] = "AIzaSyTestKey_123-abc"
        assert load_settings().api_key == "AIzaSyTestKey_123-abc"
    finally:
        config.ENV_PATH = saved_path
        if saved is not None:
            os.environ["GEMINI_API_KEY"] = saved

    # API 단계까지 새어나간 경우에도 원인을 알려줘야 한다
    from core.gemini import _wrap

    err = _wrap(
        UnicodeEncodeError("ascii", "여기에", 0, 1, "ordinal not in range(128)"),
        "gemini-3.5-flash",
        "웹검색 단계",
    )
    assert "API 키" in str(err), str(err)


def t_env_override() -> None:
    """.env 를 고치면 재시작 없이 반영되어야 한다.

    load_dotenv 의 기본값(override=False)이면 프로세스가 이미 들고 있는
    환경변수가 이겨서, 화면에 옛 모델 이름이 계속 남는 사고가 난다.
    """
    os.environ["WRITER_MODEL"] = "낡은-값-이-남으면-안-됨"
    reloaded = load_settings()
    assert reloaded.writer_model != "낡은-값-이-남으면-안-됨", (
        ".env 값이 기존 환경변수를 덮어쓰지 못합니다 (override=True 누락)"
    )


def t_model_validation() -> None:
    """존재하지 않는 모델 이름을 호출 전에 잡아내는지."""
    from dataclasses import replace

    from core.gemini import validate_models

    available = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-2.5-pro"]
    good = replace(
        load_settings(),
        research_model="gemini-3.5-flash",
        analysis_model="gemini-3.6-flash",
        writer_model="gemini-3.6-flash",
    )
    assert validate_models(good, available) == {}, "정상 이름을 오류로 판정합니다"

    bad = replace(good, research_model="gemini-3-flash")  # 실제로 없던 이름
    problems = validate_models(bad, available)
    assert "RESEARCH_MODEL" in problems, "없는 모델을 잡지 못합니다"
    assert problems["RESEARCH_MODEL"], "대안을 제시하지 못합니다"

    # 목록 조회에 실패했을 때는 검증을 건너뛰어야 한다 (앱을 막으면 안 됨)
    assert validate_models(bad, []) == {}


def t_empty_selection_guard() -> None:
    try:
        write_reviews(load_settings(), analysis=FAKE_ANALYSIS, pros=[], cons=[], rating=4)
    except ValueError as exc:
        assert "하나 이상" in str(exc)
    else:
        raise AssertionError("빈 선택인데 오류가 안 났습니다")


# --------------------------------------------------------------------------
# UI 점검 (실제로 app.py 를 실행한다)
# --------------------------------------------------------------------------


def t_initial_render() -> None:
    at = _app()
    assert not at.exception, [str(e) for e in at.exception]
    assert any("리뷰" in t.value for t in at.title)
    assert any("분석하기" in i.value for i in at.info)
    buttons = {b.label: b for b in at.button}
    assert buttons["분석하기"].disabled is True, "이미지 없이 분석 버튼이 눌립니다"


def t_analysis_screen() -> None:
    at = _app()
    at.session_state["outcome"] = AnalysisOutcome(
        analysis=FAKE_ANALYSIS,
        sources=["예시출처|https://example.com/a"],
        from_cache=True,
    )
    at.run()
    assert not at.exception, [str(e) for e in at.exception]

    body = " ".join(str(m.value) for m in at.markdown)
    for expected in ("만족한 점", "아쉬운 점", "대용량", "손잡이", "생성 옵션"):
        assert expected in body, f"'{expected}' 가 화면에 없음"

    assert len(at.checkbox) == 5, [c.label for c in at.checkbox]
    assert at.slider[0].value == 4


def t_rating_follows_selection() -> None:
    at = _app()
    at.session_state["outcome"] = AnalysisOutcome(
        analysis=FAKE_ANALYSIS, sources=[], from_cache=False
    )
    at.run()

    for cb in at.checkbox:
        if cb.key.startswith("pro_"):
            cb.check()
    at.run()
    assert not at.exception, [str(e) for e in at.exception]
    assert at.slider[0].value == 5, f"장점 전체 선택 -> {at.slider[0].value}점"

    for cb in at.checkbox:
        if cb.key.startswith("con_"):
            cb.check()
    at.run()
    assert at.slider[0].value == 3, f"장단점 모두 선택 -> {at.slider[0].value}점"


def t_password_gate() -> None:
    """로그인 전에는 본문이 절대 보이면 안 된다."""
    os.environ["APP_PASSWORD"] = "테스트비밀번호"
    at = _app(logged_in=False)
    assert not at.exception, [str(e) for e in at.exception]

    body = " ".join(str(m.value) for m in at.markdown)
    assert "1단계" not in body, "로그인도 안 했는데 본문이 노출됩니다"
    assert "제품명" not in body, "로그인도 안 했는데 입력칸이 노출됩니다"
    # 화면에 잠금 표시가 있고, 입력칸이 가려진 비밀번호 칸이어야 한다
    assert any("🔒" in t.value for t in at.title), [t.value for t in at.title]
    assert len(at.text_input) == 1, "로그인 화면에 입력칸이 하나만 있어야 합니다"
    # AppTest 의 .type 은 위젯 종류('text_input')를 준다. 입력이 가려지는지는
    # proto 쪽을 봐야 한다 (TextInput.Type: DEFAULT=0, PASSWORD=1).
    assert at.text_input[0].proto.type == 1, "비밀번호가 화면에 그대로 보입니다"

    # 틀린 비밀번호로는 통과하지 못한다
    at.text_input[0].set_value("틀린값")
    at.button[0].click().run()
    assert auth.SESSION_KEY not in at.session_state, "틀린 비밀번호로 통과했습니다"

    # 맞는 비밀번호면 통과한다
    at2 = _app(logged_in=False)
    at2.text_input[0].set_value("테스트비밀번호")
    at2.button[0].click().run()
    assert at2.session_state[auth.SESSION_KEY], "맞는 비밀번호인데 못 들어갑니다"


def t_missing_password_blocks() -> None:
    """비밀번호가 설정되지 않았으면 앱을 열어주면 안 된다."""
    saved = os.environ.pop("APP_PASSWORD", None)
    try:
        at = _app(logged_in=False)
        body = " ".join(str(m.value) for m in at.markdown)
        errors = " ".join(str(e.value) for e in at.error)
        assert "1단계" not in body, "비밀번호 미설정인데 앱이 열립니다"
        assert "APP_PASSWORD" in errors, errors
    finally:
        if saved is not None:
            os.environ["APP_PASSWORD"] = saved


def t_reset_clears_everything() -> None:
    """'새 리뷰 시작' 이 결과와 선택을 모두 지우고 로그인은 유지하는지."""
    os.environ["APP_PASSWORD"] = "테스트비밀번호"
    at = _app()
    at.session_state["outcome"] = AnalysisOutcome(
        analysis=FAKE_ANALYSIS, sources=[], from_cache=False
    )
    at.session_state["bundle"] = FAKE_BUNDLE
    at.run()

    before = (
        at.session_state["uploader_round"] if "uploader_round" in at.session_state else 0
    )
    reset_buttons = [b for b in at.button if "새 리뷰 시작" in b.label]
    assert reset_buttons, [b.label for b in at.button]
    reset_buttons[0].click().run()

    assert not at.exception, [str(e) for e in at.exception]
    assert "outcome" not in at.session_state, "분석 결과가 안 지워졌습니다"
    assert "bundle" not in at.session_state, "리뷰 결과가 안 지워졌습니다"
    assert at.session_state[auth.SESSION_KEY], "리셋하면서 로그아웃돼버렸습니다"
    # 업로더 key 가 바뀌어야 사진이 실제로 비워진다
    assert at.session_state["uploader_round"] == before + 1


def t_result_screen() -> None:
    at = _app()
    at.session_state["outcome"] = AnalysisOutcome(
        analysis=FAKE_ANALYSIS, sources=[], from_cache=False
    )
    at.session_state["bundle"] = FAKE_BUNDLE
    at.run()
    assert not at.exception, [str(e) for e in at.exception]
    texts = [ta.value for ta in at.text_area]
    assert any("물 많이 마시는" in t for t in texts)
    assert any("용량 하나는" in t for t in texts)


CHECKS = [
    ("설정 로딩", t_settings),
    ("이미지 정규화·해시", t_media),
    ("별점 제안 로직", t_rating),
    ("프롬프트 조립", t_prompts),
    ("출력 상한 미지정", t_no_output_cap),
    ("응답 잘림 감지", t_truncation_detection),
    ("캐시 저장·로딩·삭제", t_cache),
    ("Gemini 스키마 변환", t_schema_conversion),
    ("비ASCII API 키 차단", t_non_ascii_api_key),
    (".env 재로딩(override)", t_env_override),
    ("모델 이름 사전 검증", t_model_validation),
    ("빈 선택 방어", t_empty_selection_guard),
    ("UI · 비밀번호 게이트", t_password_gate),
    ("UI · 비밀번호 미설정 차단", t_missing_password_blocks),
    ("UI · 새 리뷰 시작 리셋", t_reset_clears_everything),
    ("UI · 초기 렌더링", t_initial_render),
    ("UI · 분석 결과 화면", t_analysis_screen),
    ("UI · 선택에 따른 별점 연동", t_rating_follows_selection),
    ("UI · 생성 결과 화면", t_result_screen),
]


def main() -> int:
    for name, fn in CHECKS:
        check(name, fn)

    width = max(len(n) for n, _ in results)
    failed = sum(1 for _, r in results if r != "통과")
    for name, result in results:
        mark = "OK  " if result == "통과" else "FAIL"
        print(f"[{mark}] {name.ljust(width)}  {result}")
    print(f"\n{len(results) - failed}/{len(results)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
