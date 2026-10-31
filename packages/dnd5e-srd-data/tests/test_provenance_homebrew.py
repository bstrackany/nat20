"""A consumer's own content says where it came from.

The corpus is Foundry-ingested SRD, CC-BY-4.0; an entry a table loads from its
own books says so instead of claiming to be SRD.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from dnd5e_srd_data import Provenance


def test_homebrew_names_its_licence_while_the_corpus_defaults_are_unchanged() -> None:
    own = Provenance(
        source="homebrew",
        source_url="homebrew://phb-2024.yaml#searing-smite",
        ingest_date=date(2026, 10, 30),
        ingest_version="table",
        license_tag="proprietary",
    )
    assert own.model_dump(mode="json")["srd_version"] == []  # may name no SRD version
    srd = Provenance(
        source="foundry",
        source_url="x",
        ingest_date=date(2026, 1, 1),
        ingest_version="v",
        srd_version={"5.2"},
    )
    assert srd.license_tag == "CC-BY-4.0"
    with pytest.raises(ValidationError):
        Provenance(source="wiki", source_url="x", ingest_date=date(2026, 1, 1), ingest_version="v")
