"""Operação da API (SDL) → handler, por convenção de nome."""

from __future__ import annotations

import pytest

from code_index.domain.api_impl import _title_ref, handler_names, match_handlers, parse_operation_ref


def test_handler_names() -> None:
    assert handler_names("listBlacklistedEmails") == ["listblacklistedemailshandler", "listblacklistedemailshandlerimpl"]
    assert handler_names("login__9")[0] == "loginv9handler"


def test_match_handlers_prefere_codigo_a_teste() -> None:
    sdl = [{"domain": "platform", "service": "email", "queries": ["listBlacklistedEmails", "semHandler"],
            "actions": ["sendEmail", "login__9"], "signals": []}]
    classes = [
        ("ListBlacklistedEmailsHandler", "src/test/ListBlacklistedEmailsHandler.java", 3),
        ("ListBlacklistedEmailsHandler", "src/main/handlers/ListBlacklistedEmailsHandler.java", 10),
        ("SendEmailHandlerImpl", "src/main/SendEmailHandlerImpl.java", 5),
        ("LoginV9Handler", "src/main/LoginV9Handler.java", 7),
        ("LoginHandler", "src/main/LoginHandler.java", 7),
    ]
    out = {r["operation"]: (r["kind"], r["path"], r["line"]) for r in match_handlers(sdl, classes)}
    assert out == {
        "listBlacklistedEmails": ("query", "src/main/handlers/ListBlacklistedEmailsHandler.java", 10),
        "sendEmail": ("action", "src/main/SendEmailHandlerImpl.java", 5),
        "login__9": ("action", "src/main/LoginV9Handler.java", 7),
    }


def test_title_ref() -> None:
    assert _title_ref("POST /email/queries/listBlacklistedEmails") == ("email", "query", "listBlacklistedEmails")
    assert _title_ref("GET /scheduler/entities/job/{id}") is None


@pytest.mark.parametrize(("ref", "expected"), [
    ("listBlacklistedEmails", (None, "listBlacklistedEmails")),
    ("email.listBlacklistedEmails", ("email", "listBlacklistedEmails")),
    ("email/queries/listBlacklistedEmails", ("email", "listBlacklistedEmails")),
    ("POST /email/actions/sendEmail", ("email", "sendEmail")),
])
def test_parse_operation_ref(ref: str, expected: tuple) -> None:
    assert parse_operation_ref(ref) == expected
    with pytest.raises(ValueError):
        parse_operation_ref(" / ")
