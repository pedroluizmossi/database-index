"""Perfil senior: operação do SDL → classe `<operação>Handler` (convenção do SDK) e as rotas das operações."""

from __future__ import annotations

from code_index.domain.api_impl import match_handlers
from code_index.profiles.senior.api_impl import CONTRACT_ROUTES, handler_names
from code_index.sources.openapi_s3 import _operation_attrs


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
    out = {r["operation"]: (r["kind"], r["path"], r["line"]) for r in match_handlers(sdl, classes, handler_names)}
    assert out == {
        "listBlacklistedEmails": ("query", "src/main/handlers/ListBlacklistedEmailsHandler.java", 10),
        "sendEmail": ("action", "src/main/SendEmailHandlerImpl.java", 5),
        "login__9": ("action", "src/main/LoginV9Handler.java", 7),
    }



def test_rotas_das_operacoes_como_antes() -> None:
    attrs = _operation_attrs("POST /email/queries/listBlacklistedEmails", "email", CONTRACT_ROUTES)
    assert attrs == {"method": "POST", "http_path": "/email/queries/listBlacklistedEmails",
                     "kind": "query", "operation": "listBlacklistedEmails"}
    assert _operation_attrs("POST /email/signals/changed", "email", CONTRACT_ROUTES)["kind"] == "signal"
    assert _operation_attrs("POST /email/actions/x", "Email", CONTRACT_ROUTES)["kind"] == "action"
    assert "operation" not in _operation_attrs("POST /outro/actions/x", "email", CONTRACT_ROUTES)
    assert "operation" not in _operation_attrs("GET /email/entities/log/{id}", "email", CONTRACT_ROUTES)
