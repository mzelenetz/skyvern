from __future__ import annotations

import pytest

from bacb_registry_check.checker import (
    BrowserRecord,
    parse_certification_number,
    record_from_browser,
    select_record_index,
    split_name,
    state_label,
)
from bacb_registry_check.schemas import BacbCheckRequest


def test_split_name_uses_first_and_last_token() -> None:
    assert split_name("Jennelle Marie Otero") == ("Jennelle", "Otero")


def test_state_label_maps_abbreviation() -> None:
    assert state_label("NM") == "New Mexico"


def test_parse_rbt_certification_number() -> None:
    assert parse_certification_number("RBT-25-445224", "RBT") == {
        "credential": "RBT",
        "year": "25",
        "sequence": "445224",
    }


def test_parse_numeric_certification_number_uses_fallback_credential() -> None:
    assert parse_certification_number("1-2025-1234", "BCBA") == {
        "credential": "BCBA",
        "year": "25",
        "sequence": "1234",
    }


def test_parse_certification_number_rejects_unknown_shape() -> None:
    with pytest.raises(ValueError):
        parse_certification_number("not-a-cert", "RBT")


def test_record_from_browser_extracts_expanded_details() -> None:
    raw = BrowserRecord(
        row_index=1,
        expanded=True,
        summary="- OTERO, JENNELLE Los Lunas, NM United States RBT Active",
        cells=["- OTERO, JENNELLE", "Los Lunas, NM", "United States", "RBT", "Active"],
        details=(
            "JENNELLE OTERO Location: Los Lunas, NM United States "
            "Certification Level: Registered Behavior Technician "
            "Certification Number: RBT-25-445224 Status: Active "
            "Original Certification Date: 06/14/2025 Expiration Date: 06/13/2028 "
            "Supervision RBT Supervisor(s): Lauren Duncan Gabriela Manzano Vizcarra"
        ),
        supervisor_links=["Lauren Duncan", "Gabriela Manzano Vizcarra"],
    )

    record = record_from_browser(raw)

    assert record.name == "JENNELLE OTERO"
    assert record.location == "Los Lunas, NM"
    assert record.country == "United States"
    assert record.certification_number == "RBT-25-445224"
    assert record.certification_level == "Registered Behavior Technician"
    assert record.status == "Active"
    assert record.original_certification_date == "06/14/2025"
    assert record.expiration_date == "06/13/2028"
    assert record.supervisors == ["Lauren Duncan", "Gabriela Manzano Vizcarra"]


def test_select_record_prefers_name_match() -> None:
    records: list[BrowserRecord] = [
        BrowserRecord(
            row_index=0,
            expanded=False,
            summary="+ OTERO, ALBERT Las Cruces, NM United States RBT Active",
            cells=[],
            details="",
            supervisor_links=[],
        ),
        BrowserRecord(
            row_index=1,
            expanded=False,
            summary="+ OTERO, JENNELLE Los Lunas, NM United States RBT Active",
            cells=[],
            details="JENNELLE OTERO Certification Number: RBT-25-445224",
            supervisor_links=[],
        ),
    ]
    request = BacbCheckRequest.model_validate({"state": "NM", "name": "Jennelle Otero"})

    assert select_record_index(records, request) == 1
