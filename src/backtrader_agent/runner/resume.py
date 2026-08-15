"""Session begin/resume and completion transitions for controlled runs."""

from typing import Any, Dict

from ..errors import AgentError


def _begin_or_resume_session(
    sessions,
    *,
    session_id: str,
    subject: str,
    effect_id: str,
    idempotency_key: str,
    run_token: Dict[str, Any],
) -> None:
    session = sessions.load(session_id)
    state = session["state"]
    if state in {"RUNNING", "PAUSED"}:
        if (
            session.get("artifacts", {}).get("run_subject_hash") != subject
            or session.get("artifacts", {}).get("run_effect_id") != effect_id
            or session.get("approvals", {}).get("execute") != run_token["token_id"]
        ):
            raise AgentError(
                "BTAG-RUN-SESSION",
                "interrupted session belongs to another approved effect",
            )
        if state == "RUNNING":
            return
        action_type = "controlled-run-resume"
    elif state == "RUN_APPROVED":
        action_type = "controlled-run-start"
    else:
        raise AgentError(
            "BTAG-RUN-SESSION",
            "session is not ready to start or resume this approved run",
        )
    sessions.transition(
        session_id,
        "RUNNING",
        action_type,
        {"run_subject": subject},
        idempotency_key=idempotency_key,
        approval_token_id=run_token["token_id"],
        effect_references={
            "run_subject_hash": subject,
            "run_effect_id": effect_id,
        },
    )


def _finish_successful_session(
    runner,
    sessions,
    *,
    session_id: str,
    subject: str,
    effect_id: str,
    idempotency_key: str,
    run_token: Dict[str, Any],
    result: Dict[str, Any],
    report_hash: str,
) -> None:
    session = sessions.load(session_id)
    if session["state"] in {"RUN_APPROVED", "PAUSED"}:
        runner._begin_or_resume_session(
            sessions,
            session_id=session_id,
            subject=subject,
            effect_id=effect_id,
            idempotency_key=idempotency_key,
            run_token=run_token,
        )
        session = sessions.load(session_id)
    if session["state"] == "RUNNING":
        sessions.transition(
            session_id,
            "PASSED",
            "controlled-run-passed",
            {"run_result": result["result_hash"]},
            idempotency_key=idempotency_key,
            approval_token_id=run_token["token_id"],
            effect_references={"run_result_hash": result["result_hash"]},
        )
        session = sessions.load(session_id)
    if session["state"] == "PASSED":
        sessions.transition(
            session_id,
            "REPORTED",
            "report-render",
            {"report": report_hash},
            idempotency_key=idempotency_key,
            effect_references={"report_hash": report_hash},
        )
        session = sessions.load(session_id)
    if session["state"] == "REPORTED":
        sessions.transition(
            session_id,
            "COMPLETED",
            "session-complete",
            {"run_result": result["result_hash"]},
            idempotency_key=idempotency_key,
        )
        session = sessions.load(session_id)
    if session["state"] != "COMPLETED":
        raise AgentError(
            "BTAG-RUN-SESSION",
            "successful run could not complete its legal session transitions",
        )
