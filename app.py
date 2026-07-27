"""반자동 리뷰 작성 도구.

흐름: 사진 업로드 -> 분석 -> 사용자가 방향 선택 -> 리뷰 생성
"""

from __future__ import annotations

import os

import streamlit as st

from core import auth, cache, prompts
from core.analyzer import analyze_product
from core.config import ConfigError, Settings, load_settings
from core.gemini import GeminiError, list_available_models, validate_models
from core.media import MAX_IMAGES, prepare_images, supported_upload_types
from core.schemas import ProductAnalysis, ReviewAngle
from core.writer import suggest_rating, write_reviews

st.set_page_config(
    page_title="리뷰 작성 도우미",
    page_icon="📝",
    layout="wide",
    # 휴대폰에서는 사이드바가 펼쳐져 있으면 화면을 다 덮는다.
    initial_sidebar_state="collapsed",
)

# 클라우드 배포에는 .env 파일이 없다. Secrets 화면에 넣은 값을 환경변수로 옮겨
# core 쪽이 실행 위치를 몰라도 되게 한다.
_SECRET_KEYS = (
    "GEMINI_API_KEY",
    "APP_PASSWORD",
    "RESEARCH_MODEL",
    "ANALYSIS_MODEL",
    "WRITER_MODEL",
    "MAX_IMAGE_EDGE",
    "CACHE_ENABLED",
)


def hydrate_env_from_secrets() -> None:
    try:
        available = dict(st.secrets)
    except Exception:  # noqa: BLE001 - secrets 파일이 없으면 예외가 난다
        return
    for key in _SECRET_KEYS:
        value = available.get(key)
        if value is not None:
            os.environ[key] = str(value)


hydrate_env_from_secrets()
auth.require_password()

BASIS_BADGE = {
    "image": "사진 확인",
    "web": "검색 근거",
    "common": "일반 통념",
}


def get_settings() -> Settings | None:
    try:
        return load_settings()
    except ConfigError as exc:
        st.error(str(exc))
        st.stop()
    return None


@st.cache_data(ttl=1800, show_spinner=False)
def cached_model_list(api_key: str) -> list[str]:
    """모델 목록은 자주 안 바뀌므로 30분 캐싱한다. 실패해도 앱은 계속 뜬다."""
    try:
        return list_available_models(api_key)
    except Exception:  # noqa: BLE001 - 검증 실패가 앱을 막으면 안 된다
        return []


def render_model_status(settings: Settings) -> None:
    """설정된 모델 이름이 실제로 존재하는지 미리 확인해 보여준다."""
    st.caption(
        f"분석 모델: `{settings.analysis_model}`\n\n"
        f"작성 모델: `{settings.writer_model}`\n\n"
        f"검색 모델: `{settings.research_model}`"
    )

    available = cached_model_list(settings.api_key)
    if not available:
        return

    problems = validate_models(settings, available)
    if not problems:
        st.success("모델 이름 확인됨", icon="✅")
        return

    for key, suggestions in problems.items():
        hint = ""
        if suggestions:
            hint = "\n\n대신 쓸 수 있는 이름:\n" + "\n".join(
                f"- `{s}`" for s in suggestions
            )
        st.error(f"`.env` 의 **{key}** 값이 존재하지 않는 모델입니다.{hint}")
    st.caption("`.env` 를 고친 뒤 브라우저를 새로고침하면 반영됩니다.")


def render_sidebar(settings: Settings) -> bool:
    with st.sidebar:
        st.subheader("설정")
        render_model_status(settings)

        use_web = st.toggle(
            "웹검색으로 제품 정보 보강",
            value=True,
            help="상품명을 입력했을 때만 동작합니다. 끄면 사진 분석만으로 진행합니다.",
        )

        with st.expander("문제 해결"):
            if st.button("사용 가능한 모델 확인", use_container_width=True):
                cached_model_list.clear()
                try:
                    names = list_available_models(settings.api_key)
                except GeminiError as exc:
                    st.error(str(exc))
                except Exception as exc:  # noqa: BLE001 - 진단 목적
                    st.error(f"모델 목록 조회 실패: {exc}")
                else:
                    st.write("이 중에서 골라 `.env` 에 넣으세요.")
                    st.code("\n".join(names) or "(없음)")

            if st.button("분석 캐시 비우기", use_container_width=True):
                count = cache.clear()
                st.success(f"{count}개 삭제했습니다.")

        st.divider()
        if st.button("로그아웃", use_container_width=True):
            auth.logout()
            st.rerun()

    return use_web


def reset_selection_state() -> None:
    for key in list(st.session_state.keys()):
        if key.startswith(("pro_", "con_")):
            del st.session_state[key]
    st.session_state.pop("bundle", None)


def start_new_review() -> None:
    """모든 입력과 결과를 비우고 처음 화면으로 되돌린다.

    file_uploader 는 값을 코드로 지울 수 없다. key 를 바꾸면 스트림릿이 다른
    위젯으로 취급해 빈 상태로 새로 그리므로, 회차 번호를 올려 key 를 갈아끼운다.
    """
    keep = {auth.SESSION_KEY, "uploader_round"}
    for key in list(st.session_state.keys()):
        if key not in keep:
            del st.session_state[key]
    st.session_state["uploader_round"] = st.session_state.get("uploader_round", 0) + 1


def render_angle_picker(
    angles: list[ReviewAngle], prefix: str, empty_message: str
) -> list[ReviewAngle]:
    if not angles:
        st.caption(empty_message)
        return []

    picked: list[ReviewAngle] = []
    for i, angle in enumerate(angles):
        checked = st.checkbox(
            f"**{angle.headline}**",
            key=f"{prefix}_{i}",
        )
        st.caption(f"{angle.detail}  ·  {BASIS_BADGE.get(angle.basis, angle.basis)}")
        if checked:
            picked.append(angle)
    return picked


def render_analysis(analysis: ProductAnalysis, sources: list[str]) -> None:
    st.subheader(f"{analysis.product_name}")
    st.caption(analysis.category)
    st.write(analysis.summary)

    col_a, col_b = st.columns(2)
    with col_a:
        if analysis.specs:
            st.markdown("**확인된 사양**")
            for spec in analysis.specs:
                st.markdown(
                    f"- {spec.label}: {spec.value} "
                    f"<span style='opacity:.5;font-size:.85em'>"
                    f"({BASIS_BADGE.get(spec.basis, spec.basis)})</span>",
                    unsafe_allow_html=True,
                )
    with col_b:
        if analysis.observed:
            with st.expander("사진에서 확인된 것"):
                for item in analysis.observed:
                    st.markdown(f"- {item}")

    if analysis.keywords:
        st.markdown("**추출 키워드**")
        st.write(" ".join(f"`{k}`" for k in analysis.keywords))

    if sources:
        with st.expander(f"검색 출처 {len(sources)}건"):
            for entry in sources:
                title, _, uri = entry.partition("|")
                st.markdown(f"- [{title}]({uri})")


def main() -> None:
    head_left, head_right = st.columns([3, 1])
    with head_left:
        st.title("📝 리뷰 작성 도우미")
    with head_right:
        # 결과를 본 뒤 새 제품으로 넘어가는 통로. 새로고침을 대신한다.
        if st.button("🔄 새 리뷰 시작", use_container_width=True):
            start_new_review()
            st.rerun()

    st.caption(
        "사진과 제품명을 넣으면 리뷰 소재를 뽑아드립니다. "
        "그중 **직접 겪은 것만 골라서** 리뷰 초안을 만드세요."
    )

    settings = get_settings()
    assert settings is not None
    use_web = render_sidebar(settings)

    st.divider()
    st.markdown("### 1단계 · 제품 정보 입력")

    product_name = st.text_input(
        "제품명",
        placeholder="예) 스탠리 퀜처 H2.0 트래블 텀블러 1.18L",
        help="정확할수록 검색 정보가 좋아집니다. 비워두면 사진만으로 분석합니다.",
    )
    uploads = st.file_uploader(
        f"제품 사진 (최대 {MAX_IMAGES}장)",
        type=supported_upload_types(),
        accept_multiple_files=True,
        key=f"uploads_{st.session_state.get('uploader_round', 0)}",
    )

    if uploads:
        # 휴대폰 화면에서 5칸으로 나누면 썸네일이 너무 작아진다. 3칸씩 줄바꿈.
        shown = uploads[:MAX_IMAGES]
        for start in range(0, len(shown), 3):
            row = shown[start : start + 3]
            for col, upload in zip(st.columns(3), row):
                col.image(upload, use_container_width=True)

    analyze_clicked = st.button(
        "분석하기", type="primary", disabled=not uploads, use_container_width=False
    )

    if analyze_clicked:
        with st.spinner("사진을 분석하는 중입니다..."):
            try:
                images = prepare_images(
                    [u.getvalue() for u in uploads], settings.max_image_edge
                )
                outcome = analyze_product(
                    settings,
                    images=images,
                    product_name=product_name,
                    use_web_search=use_web,
                )
            except (GeminiError, ValueError) as exc:
                st.error(str(exc))
            except Exception as exc:  # noqa: BLE001
                st.error(f"분석 중 예상치 못한 오류: {exc}")
            else:
                st.session_state["outcome"] = outcome
                reset_selection_state()
                if outcome.from_cache:
                    st.toast("캐시된 분석 결과를 불러왔습니다.")

    outcome = st.session_state.get("outcome")
    if outcome is None:
        st.info("사진을 올리고 '분석하기'를 누르면 여기에 결과가 나옵니다.")
        return

    analysis: ProductAnalysis = outcome.analysis

    st.divider()
    render_analysis(analysis, outcome.sources)

    st.divider()
    st.markdown("### 2단계 · 리뷰 방향 고르기")
    st.caption(
        "실제로 겪은 것만 고르세요. 고르지 않은 항목은 리뷰에 들어가지 않습니다."
    )

    pick_left, pick_right = st.columns(2)
    with pick_left:
        st.markdown("#### 만족한 점")
        picked_pros = render_angle_picker(
            analysis.pros, "pro", "장점 후보를 찾지 못했습니다."
        )
    with pick_right:
        st.markdown("#### 아쉬운 점")
        picked_cons = render_angle_picker(
            analysis.cons, "con", "단점 후보를 찾지 못했습니다."
        )

    note_left, note_right = st.columns(2)
    with note_left:
        purchase_reason = st.text_area(
            "왜 샀나요 (선택)",
            placeholder="예) 기존에 쓰던 게 자꾸 새서 대용량으로 바꾸려고 찾다가 골랐어요",
            height=90,
        )
    with note_right:
        personal_note = st.text_area(
            "직접 겪은 일 (선택, 리뷰 품질을 가장 크게 좌우합니다)",
            placeholder="예) 한 달째 매일 들고 다니는데 뚜껑 고무패킹에서 냄새가 나기 시작했어요",
            height=90,
        )

    st.divider()
    st.markdown("### 3단계 · 생성 옵션")

    suggested = suggest_rating(picked_pros, picked_cons)
    opt1, opt2 = st.columns([1, 2])
    with opt1:
        rating = st.slider("별점", 1, 5, suggested)
        if rating != suggested:
            st.caption(f"선택 항목 기준 제안값은 {suggested}점입니다.")
    with opt2:
        review_format = st.radio(
            "형식",
            list(prompts.FORMAT_GUIDE),
            index=1,
            horizontal=True,
            captions=[
                "소제목 없이 자연스러운 문단",
                "구매동기·장점·단점·사용후기·총평",
                "✔ 요약 + 상세 사용기 + 항목별 평가",
            ],
        )

    opt3, opt4, opt5 = st.columns(3)
    with opt3:
        tone = st.selectbox("톤", list(prompts.TONE_GUIDE))
    with opt4:
        length = st.selectbox("분량", list(prompts.LENGTH_GUIDE), index=2)
    with opt5:
        variant_count = st.selectbox("생성 개수", [1, 2, 3, 4], index=1)

    if st.button("리뷰 생성", type="primary"):
        with st.spinner("리뷰를 쓰는 중입니다..."):
            try:
                bundle = write_reviews(
                    settings,
                    analysis=analysis,
                    pros=picked_pros,
                    cons=picked_cons,
                    rating=rating,
                    purchase_reason=purchase_reason,
                    personal_note=personal_note,
                    tone=tone,
                    length=length,
                    review_format=review_format,
                    variant_count=variant_count,
                )
            except (GeminiError, ValueError) as exc:
                st.error(str(exc))
            except Exception as exc:  # noqa: BLE001
                st.error(f"생성 중 예상치 못한 오류: {exc}")
            else:
                st.session_state["bundle"] = bundle

    bundle = st.session_state.get("bundle")
    if bundle is None:
        return

    st.divider()
    st.markdown("### 결과")
    if bundle.keywords:
        st.write(" ".join(f"`{k}`" for k in bundle.keywords))

    for i, variant in enumerate(bundle.variants):
        st.markdown(f"#### {variant.title}")
        st.caption(f"{variant.style_label} · {len(variant.text)}자")
        # 본문 길이에 맞춰 편집창 높이를 조절한다. 900자짜리를 160px 창에서
        # 스크롤하며 읽는 건 고역이다.
        height = min(700, max(200, len(variant.text) // 2 + 120))
        st.text_area(
            f"리뷰 {i + 1}",
            value=variant.text,
            height=height,
            key=f"result_{i}",
            label_visibility="collapsed",
        )
        with st.expander("복사용 (오른쪽 위 아이콘)"):
            st.code(f"{variant.title}\n\n{variant.text}", language=None, wrap_lines=True)

    st.divider()
    done_left, done_right = st.columns(2)
    with done_left:
        if st.button("🔄 새 제품으로 다시 시작", use_container_width=True, type="primary"):
            start_new_review()
            st.rerun()
        st.caption("사진과 입력을 모두 비우고 처음으로 돌아갑니다.")
    with done_right:
        if st.button("↺ 같은 제품으로 다시 생성", use_container_width=True):
            # 분석 결과는 그대로 두고 결과만 지운다. 위 옵션을 바꿔 다시 뽑으면 된다.
            st.session_state.pop("bundle", None)
            st.rerun()
        st.caption("분석은 유지한 채 결과만 지웁니다. 옵션을 바꿔 다시 생성하세요.")


if __name__ == "__main__":
    main()
