"""Referência de operação (título de página, serviço.operação). A convenção de nomes dos perfis fica em
test_profile_impls.py (sintética) e em tests/profiles/senior/ (a do senior)."""

from __future__ import annotations

import pytest

from code_index.domain.api_impl import _title_ref, parse_operation_ref


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
