"""Deterministic BACB registry checker backed by a human-authenticated browser."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict
from uuid import uuid4

from playwright.async_api import Browser, Page, TimeoutError as PlaywrightTimeoutError, async_playwright
from pydantic import BaseModel, ConfigDict, Field, field_validator

from bacb_registry_check.schemas import BacbCheckRequest, BacbCheckResponse, BacbRegistryRecord, CredentialType

BACB_REGISTRY_URL = "https://services.bacb.com/o.php?page=101135"

_CREDENTIAL_VALUES: dict[CredentialType, str] = {
    "RBT": "RBT",
    "BCaBA": "BCaBA",
    "BCBA": "BCBA",
    "BCBA-D": "BCBAD",
}
_CERT_NUMBER_TYPE_LABELS: dict[CredentialType, str] = {
    "RBT": "RBT",
    "BCaBA": "0 (BCaBA)",
    "BCBA": "1 (BCBA)",
    "BCBA-D": "1 (BCBA)",
}
_US_STATE_LABELS = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "DC": "District of Columbia",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
}


class BacbCheckerConfig(BaseModel):
    """Runtime configuration for the BACB registry checker."""

    model_config = ConfigDict(extra="forbid")

    cdp_url: str = Field(default="http://192.168.65.254:9222/")
    screenshot_dir: Path = Field(default=Path("/data/bacb-registry"))
    public_base_url: str = Field(default="http://127.0.0.1:8765")
    request_timeout_ms: int = Field(default=30_000, ge=1_000)

    @field_validator("cdp_url", "public_base_url")
    @classmethod
    def _trim_string(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("value must not be blank")
        return trimmed


class HumanVerificationRequired(RuntimeError):
    """Raised when BACB still requires manual Cloudflare verification."""


class CertNumberParts(TypedDict):
    """Parsed certification-number fields used by the BACB form."""

    credential: CredentialType
    year: str
    sequence: str


class BrowserRecord(TypedDict):
    """Raw record data returned from browser-side table extraction."""

    row_index: int
    expanded: bool
    summary: str
    cells: list[str]
    details: str
    supervisor_links: list[str]


@dataclass(frozen=True)
class SelectedRecord:
    """Selected browser record plus its index in the result table."""

    row_index: int
    record: BacbRegistryRecord


class BacbRegistryChecker:
    """Search BACB through a persistent Chrome session the user verifies manually."""

    def __init__(self, config: BacbCheckerConfig) -> None:
        self._config = config

    async def check(self, request: BacbCheckRequest) -> BacbCheckResponse:
        """Submit a BACB registry search and return parsed records plus a screenshot."""

        async with async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp(self._config.cdp_url)
            try:
                page = await self._registry_page(browser)
                await self._open_search_form(page)
                await self._ensure_human_verified(page)
                if request.search_mode == "certification_number":
                    await self._submit_certification_number_search(page, request)
                else:
                    await self._submit_state_name_search(page, request)

                browser_records = await self._extract_browser_records(page)
                selected = await self._select_and_expand_record(page, request, browser_records)
                records = await self._records_after_expansion(page)
                screenshot_path = await self._save_screenshot(page)
                screenshot_url = self._screenshot_url(screenshot_path)
                return self._response_from_records(request, records, selected, screenshot_path, screenshot_url)
            except HumanVerificationRequired as exc:
                screenshot_path = await self._save_screenshot_if_possible(page if "page" in locals() else None)
                return BacbCheckResponse(
                    search_mode=request.search_mode,
                    submitted_rbt_number=request.rbt_number,
                    submitted_state=request.state,
                    submitted_name=request.name,
                    credential=request.credential,
                    status="needs_human_verification",
                    matched=None,
                    screenshot_path=str(screenshot_path) if screenshot_path else None,
                    screenshot_url=self._screenshot_url(screenshot_path) if screenshot_path else None,
                    result_summary=str(exc),
                )
            finally:
                await browser.close()

    async def _registry_page(self, browser: Browser) -> Page:
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        for page in context.pages:
            if "services.bacb.com/o.php" in page.url:
                await page.bring_to_front()
                return page
        page = await context.new_page()
        await page.goto(BACB_REGISTRY_URL, wait_until="domcontentloaded", timeout=self._config.request_timeout_ms)
        return page

    async def _open_search_form(self, page: Page) -> None:
        await page.wait_for_load_state("domcontentloaded", timeout=self._config.request_timeout_ms)
        if await self._is_visible(page, "#btnSubmit"):
            return
        if await self._is_visible(page, "#reviseSearch"):
            await page.locator("#reviseSearch").click()
            try:
                await page.wait_for_selector("#btnSubmit", timeout=5_000)
                return
            except PlaywrightTimeoutError:
                pass
        await page.goto(BACB_REGISTRY_URL, wait_until="domcontentloaded", timeout=self._config.request_timeout_ms)
        try:
            await page.wait_for_selector("#btnSubmit", timeout=10_000)
        except PlaywrightTimeoutError as exc:
            if await self._is_human_verification_page(page):
                raise HumanVerificationRequired(
                    "BACB Cloudflare verification is pending. Complete it in the Chrome tab, then rerun the check."
                ) from exc
            raise HumanVerificationRequired(
                "BACB registry form is not available yet. Open the Chrome BACB tab and complete the human verification."
            ) from exc

    async def _ensure_human_verified(self, page: Page) -> None:
        if await self._is_human_verification_page(page):
            raise HumanVerificationRequired(
                "BACB Cloudflare verification is pending. Complete it in the Chrome tab, then rerun the check."
            )
        token = ""
        token_locator = page.locator('input[name="cf-turnstile-response"]')
        if await token_locator.count() > 0:
            token = await token_locator.input_value(timeout=2_000)
        if token:
            return
        raise HumanVerificationRequired(
            "BACB Cloudflare verification is still pending. Complete it once in the Chrome tab, then rerun the check."
        )

    async def _submit_state_name_search(self, page: Page, request: BacbCheckRequest) -> None:
        first_name, last_name = split_name(request.name or "")
        await page.locator("#radioGeneral").check()
        await page.locator(f'input[name="certType[]"][value="{_CREDENTIAL_VALUES[request.credential]}"]').check()
        await page.locator("#id-country").select_option(label="United States")
        await page.locator("#id-state").select_option(label=state_label(request.state or ""))
        await page.locator("#id-first_name").fill(first_name)
        await page.locator("#id-last_name").fill(last_name)
        await self._accept_terms(page)
        await self._submit_search(page)

    async def _submit_certification_number_search(self, page: Page, request: BacbCheckRequest) -> None:
        if request.rbt_number is None:
            raise ValueError("rbt_number is required for certification-number search")
        parts = parse_certification_number(request.rbt_number, request.credential)
        await page.locator("#radioCertification").check()
        await page.locator("#id-cert_number_type").select_option(label=_CERT_NUMBER_TYPE_LABELS[parts["credential"]])
        await page.locator("#id-cert_number_year").fill(parts["year"])
        await page.locator("#id-cert_number_seq").fill(parts["sequence"])
        await self._accept_terms(page)
        await self._submit_search(page)

    async def _accept_terms(self, page: Page) -> None:
        ack = page.locator('input[name="ackStatus"]')
        if await ack.count() > 0:
            await ack.check()

    async def _submit_search(self, page: Page) -> None:
        await page.locator("#btnSubmit").click()
        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except PlaywrightTimeoutError:
            pass
        await page.wait_for_timeout(1_000)
        if await self._is_human_verification_page(page):
            raise HumanVerificationRequired(
                "BACB requested human verification after submitting the search. Complete it in the Chrome tab, then rerun the check."
            )

    async def _extract_browser_records(self, page: Page) -> list[BrowserRecord]:
        return await page.evaluate(
            r"""
            () => {
              const normalize = (text) => (text || '').replace(/\s+/g, ' ').trim();
              const table = document.querySelector('table.dataTable') || document.querySelector('table');
              if (!table || !table.tBodies.length) return [];
              const rows = Array.from(table.tBodies[0].rows);
              const mainRows = rows.filter((row) => {
                const cells = Array.from(row.cells).map((cell) => normalize(cell.textContent));
                return !row.classList.contains('child') && cells.length >= 5 && cells[0].includes(',');
              });
              return mainRows.map((row, index) => {
                const cells = Array.from(row.cells).map((cell) => normalize(cell.textContent));
                const next = row.nextElementSibling;
                const nextText = next ? normalize(next.textContent) : '';
                const hiddenDetails = cells.find((cell) => cell.includes('Certification Number:')) || '';
                const details = nextText.includes('Certification Number:') ? nextText : hiddenDetails;
                const detailContainer = nextText.includes('Certification Number:') ? next : row;
                const supervisorLinks = details
                  ? Array.from(detailContainer.querySelectorAll('a')).map((link) => normalize(link.textContent)).filter(Boolean)
                  : [];
                return {
                  row_index: index,
                  expanded: row.classList.contains('parent'),
                  summary: cells.slice(0, 5).join(' '),
                  cells,
                  details,
                  supervisor_links: supervisorLinks,
                };
              });
            }
            """
        )

    async def _select_and_expand_record(
        self, page: Page, request: BacbCheckRequest, browser_records: list[BrowserRecord]
    ) -> SelectedRecord | None:
        if not browser_records:
            return None
        selected_index = select_record_index(browser_records, request)
        selected = browser_records[selected_index]
        if not selected["expanded"]:
            await page.evaluate(
                r"""
                (targetIndex) => {
                  const normalize = (text) => (text || '').replace(/\s+/g, ' ').trim();
                  const table = document.querySelector('table.dataTable') || document.querySelector('table');
                  if (!table || !table.tBodies.length) return;
                  const rows = Array.from(table.tBodies[0].rows).filter((row) => {
                    const cells = Array.from(row.cells).map((cell) => normalize(cell.textContent));
                    return !row.classList.contains('child') && cells.length >= 5 && cells[0].includes(',');
                  });
                  const row = rows[targetIndex];
                  if (!row || row.classList.contains('parent')) return;
                  const control = row.querySelector('td') || row;
                  control.click();
                }
                """,
                selected_index,
            )
            await page.wait_for_timeout(750)
        records = await self._records_after_expansion(page)
        if selected_index >= len(records):
            return None
        return SelectedRecord(row_index=selected_index, record=records[selected_index])

    async def _records_after_expansion(self, page: Page) -> list[BacbRegistryRecord]:
        browser_records = await self._extract_browser_records(page)
        return [record_from_browser(raw) for raw in browser_records]

    async def _save_screenshot(self, page: Page) -> Path:
        self._config.screenshot_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = self._config.screenshot_dir / f"bacb-registry-{timestamp}-{uuid4().hex[:10]}.png"
        await page.screenshot(path=str(path), full_page=True)
        return path

    async def _save_screenshot_if_possible(self, page: Page | None) -> Path | None:
        if page is None:
            return None
        try:
            return await self._save_screenshot(page)
        except Exception:
            return None

    def _screenshot_url(self, path: Path | None) -> str | None:
        if path is None:
            return None
        return f"{self._config.public_base_url.rstrip('/')}/screenshots/{path.name}"

    def _response_from_records(
        self,
        request: BacbCheckRequest,
        records: list[BacbRegistryRecord],
        selected: SelectedRecord | None,
        screenshot_path: Path,
        screenshot_url: str | None,
    ) -> BacbCheckResponse:
        if selected is None:
            return BacbCheckResponse(
                search_mode=request.search_mode,
                submitted_rbt_number=request.rbt_number,
                submitted_state=request.state,
                submitted_name=request.name,
                credential=request.credential,
                status="not_found",
                matched=False,
                records=records,
                screenshot_path=str(screenshot_path),
                screenshot_url=screenshot_url,
                result_summary="No BACB registry rows matched the submitted search.",
            )
        return BacbCheckResponse(
            search_mode=request.search_mode,
            submitted_rbt_number=request.rbt_number,
            submitted_state=request.state,
            submitted_name=request.name,
            credential=request.credential,
            status="completed",
            matched=True,
            selected_record=selected.record,
            records=records,
            screenshot_path=str(screenshot_path),
            screenshot_url=screenshot_url,
            result_summary="BACB registry search completed and the selected row was expanded for the screenshot.",
        )

    async def _is_visible(self, page: Page, selector: str) -> bool:
        locator = page.locator(selector)
        if await locator.count() == 0:
            return False
        return await locator.is_visible()

    async def _is_human_verification_page(self, page: Page) -> bool:
        body = page.locator("body")
        if await body.count() == 0:
            return False
        text = await body.inner_text(timeout=2_000)
        normalized = text.lower()
        return "let's confirm you are human" in normalized or "complete the security check" in normalized


def split_name(name: str) -> tuple[str, str]:
    """Split a full name into the BACB form's first and last name fields."""

    parts = name.split()
    if len(parts) <= 1:
        return name, ""
    return parts[0], parts[-1]


def state_label(state: str) -> str:
    """Return the BACB select label for a state abbreviation or name."""

    normalized = state.strip().upper()
    return _US_STATE_LABELS.get(normalized, state.strip())


def parse_certification_number(value: str, fallback_credential: CredentialType) -> CertNumberParts:
    """Parse a BACB certification number into the registry form's pieces."""

    normalized = value.strip().upper()
    rbt_match = re.fullmatch(r"RBT-(\d{2,4})-(\d+)", normalized)
    if rbt_match:
        return {"credential": "RBT", "year": rbt_match.group(1)[-2:], "sequence": rbt_match.group(2)}
    numeric_match = re.fullmatch(r"(?:[01]-)?(\d{2,4})-(\d+)", normalized)
    if numeric_match:
        return {
            "credential": fallback_credential,
            "year": numeric_match.group(1)[-2:],
            "sequence": numeric_match.group(2),
        }
    raise ValueError("certification number must look like RBT-25-445224 or 25-445224")


def select_record_index(records: list[BrowserRecord], request: BacbCheckRequest) -> int:
    """Choose the most relevant BACB row for expansion."""

    if request.rbt_number is not None:
        target = request.rbt_number.upper()
        for index, record in enumerate(records):
            haystack = f"{record['summary']} {record['details']}".upper()
            if target in haystack:
                return index
    if request.name is not None:
        target_tokens = set(normalize_name(request.name).split())
        for index, record in enumerate(records):
            haystack = normalize_name(f"{record['summary']} {record['details']}")
            if target_tokens and target_tokens.issubset(set(haystack.split())):
                return index
    return 0


def record_from_browser(raw: BrowserRecord) -> BacbRegistryRecord:
    """Convert raw table text from the browser into a structured BACB record."""

    cells = raw["cells"]
    summary_name = cells[0].lstrip("+- ").strip() if cells else None
    location = cells[1] if len(cells) > 1 else None
    country = cells[2] if len(cells) > 2 else None
    certification = cells[3] if len(cells) > 3 else None
    status = cells[4] if len(cells) > 4 else None
    details = raw["details"]
    detail_name = extract_before(details, "Location:")
    detail_location = extract_between(details, "Location:", "Certification Level:")
    detail_location, detail_country = split_location_country(detail_location)
    return BacbRegistryRecord(
        name=detail_name or summary_name,
        location=detail_location or location,
        country=detail_country or country,
        certification=certification,
        status=extract_between(details, "Status:", "Original Certification Date:") or status,
        certification_level=extract_between(details, "Certification Level:", "Certification Number:"),
        certification_number=extract_between(details, "Certification Number:", "Status:"),
        original_certification_date=extract_between(details, "Original Certification Date:", "Expiration Date:"),
        expiration_date=extract_between(details, "Expiration Date:", "Supervision"),
        supervisors=clean_supervisors(raw["supervisor_links"]),
        raw_summary=raw["summary"],
        raw_details=details or None,
    )


def extract_before(text: str, marker: str) -> str | None:
    """Return normalized text before a marker."""

    if marker not in text:
        return None
    return clean_text(text.split(marker, 1)[0])


def extract_between(text: str, start: str, end: str) -> str | None:
    """Return normalized text between two labels."""

    if start not in text:
        return None
    after_start = text.split(start, 1)[1]
    if end in after_start:
        after_start = after_start.split(end, 1)[0]
    return clean_text(after_start)


def split_location_country(value: str | None) -> tuple[str | None, str | None]:
    """Split BACB's combined location/country text when possible."""

    if value is None:
        return None, None
    if value.endswith(" United States"):
        return clean_text(value[: -len(" United States")]), "United States"
    return value, None


def clean_supervisors(values: list[str]) -> list[str]:
    """Remove icon text and duplicates from supervisor link labels."""

    cleaned: list[str] = []
    for value in values:
        text = clean_text(value.replace("\uf08e", ""))
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def clean_text(value: str | None) -> str | None:
    """Normalize whitespace and empty strings."""

    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", value).strip(" :-")
    return cleaned or None


def normalize_name(value: str) -> str:
    """Normalize names for token matching."""

    return re.sub(r"[^A-Z ]+", " ", value.upper()).strip()
