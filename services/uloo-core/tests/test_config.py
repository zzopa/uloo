"""Configuration safety tests."""

import pytest
from pydantic import ValidationError

from uloo.config import Settings


def test_database_schema_accepts_simple_identifier() -> None:
    configured = Settings(_env_file=None, db_schema="uloo_test_schema")

    assert configured.db_schema == "uloo_test_schema"


@pytest.mark.parametrize("schema", ["", "1schema", "bad-schema", "public; DROP SCHEMA public"])
def test_database_schema_rejects_unsafe_identifier(schema: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, db_schema=schema)
