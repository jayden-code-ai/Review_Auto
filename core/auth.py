"""비밀번호 게이트.

배포하면 URL 을 아는 사람은 누구나 들어올 수 있으므로 앞단을 막는다.

비밀번호는 코드에 넣지 않는다. 저장소에 커밋되는 순간 공개되기 때문이다.
로컬은 .env, Streamlit Cloud 는 Secrets 화면에서 주입한다. 둘 다 저장소 밖이다.
"""

from __future__ import annotations

import hmac
import os
import time

import streamlit as st

SESSION_KEY = "_authenticated"
_FAIL_KEY = "_auth_failures"
_MAX_FAILURES = 5


def _expected_password() -> str:
    """st.secrets(클라우드) 를 먼저 보고, 없으면 환경변수(.env) 를 본다."""
    value = ""
    try:
        value = str(st.secrets.get("APP_PASSWORD", "") or "")
    except Exception:  # noqa: BLE001 - secrets 파일이 아예 없으면 예외가 난다
        value = ""
    if not value:
        value = os.getenv("APP_PASSWORD", "")
    return value.strip()


def _login_form(expected: str) -> None:
    st.title("🔒 리뷰 작성 도우미")
    st.caption("비밀번호를 입력하세요.")

    failures = st.session_state.get(_FAIL_KEY, 0)
    if failures >= _MAX_FAILURES:
        st.error(
            "비밀번호를 너무 여러 번 틀렸습니다. "
            "브라우저 탭을 닫았다가 다시 열어주세요."
        )
        return

    with st.form("login"):
        entered = st.text_input("비밀번호", type="password")
        submitted = st.form_submit_button("들어가기", use_container_width=True)

    if not submitted:
        return

    # compare_digest 를 쓰는 이유: == 는 앞에서부터 비교하다 다르면 즉시 멈춰서,
    # 응답 시간 차이로 비밀번호를 한 글자씩 알아낼 수 있다.
    #
    # 바이트로 바꿔서 넘기는 이유: compare_digest 는 비ASCII 문자열을 받으면
    # TypeError 를 던진다. 한글 비밀번호를 쓰면 앱이 죽는다.
    if hmac.compare_digest(entered.encode("utf-8"), expected.encode("utf-8")):
        st.session_state[SESSION_KEY] = True
        st.session_state.pop(_FAIL_KEY, None)
        st.rerun()
    else:
        st.session_state[_FAIL_KEY] = failures + 1
        time.sleep(1)  # 자동 대입 공격 속도를 늦춘다
        left = _MAX_FAILURES - st.session_state[_FAIL_KEY]
        st.error(
            f"비밀번호가 틀렸습니다. (남은 시도 {left}회)"
            if left > 0
            else "비밀번호를 너무 여러 번 틀렸습니다."
        )


def require_password() -> None:
    """인증되지 않았으면 로그인 화면을 그리고 이후 실행을 멈춘다."""
    expected = _expected_password()

    if not expected:
        st.error(
            "**APP_PASSWORD 가 설정되지 않았습니다.**\n\n"
            "비밀번호 없이 열어두면 URL 을 아는 누구나 사용할 수 있고 "
            "API 요금이 청구됩니다.\n\n"
            "- 로컬: `.env` 에 `APP_PASSWORD=...` 추가\n"
            "- Streamlit Cloud: Settings → Secrets 에 `APP_PASSWORD = \"...\"` 추가"
        )
        st.stop()

    if st.session_state.get(SESSION_KEY):
        return

    _login_form(expected)
    st.stop()


def logout() -> None:
    st.session_state.pop(SESSION_KEY, None)
