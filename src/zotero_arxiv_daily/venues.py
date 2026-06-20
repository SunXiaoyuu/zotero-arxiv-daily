from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS = {"main": MAIN_NS}


@dataclass(frozen=True)
class Venue:
    kind: str
    name: str
    field: str | None = None
    rank: str | None = None
    ccf: str | None = None
    url: str | None = None
    openalex_source_id: str | None = None


def resolve_project_path(path: str | Path) -> Path:
    resolved = Path(path)
    if resolved.is_absolute():
        return resolved

    cwd_path = Path.cwd() / resolved
    if cwd_path.exists():
        return cwd_path

    project_root = Path(__file__).resolve().parents[2]
    return project_root / resolved


def clean_venue_name(name: str) -> str:
    name = re.sub(r"\s+-\s+\*?new\*?\s*$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+-\s+\*?新增\*?\s*$", "", name)
    name = name.replace("*", "")
    return re.sub(r"\s+", " ", name).strip()


def venue_search_names(name: str) -> list[str]:
    cleaned = clean_venue_name(name)
    names = [cleaned]

    without_suffix = re.sub(r"\s*\([^)]*\)\s*$", "", cleaned).strip()
    if without_suffix and without_suffix != cleaned:
        names.append(without_suffix)

    match = re.search(r"\(([^)]+)\)\s*$", cleaned)
    if match:
        acronym = match.group(1).strip()
        if acronym and " " not in acronym and len(acronym) <= 12:
            names.append(acronym)

    deduped = []
    seen = set()
    for item in names:
        key = item.lower()
        if item and key not in seen:
            deduped.append(item)
            seen.add(key)
    return deduped


def load_venues_from_excel(path: str | Path) -> list[Venue]:
    excel_path = resolve_project_path(path)
    if not excel_path.exists():
        raise FileNotFoundError(f"Venue Excel file not found: {excel_path}")

    venues: list[Venue] = []
    seen_names = set()
    for sheet_name, rows in _read_xlsx_sheets(excel_path):
        if not rows:
            continue

        header = [str(cell or "") for cell in rows[0]]
        kind = _infer_sheet_kind(sheet_name, header)
        if kind is None:
            continue

        for row in rows[1:]:
            if len(row) < 2:
                continue

            name = clean_venue_name(str(row[1] or ""))
            if not name:
                continue

            key = name.lower()
            if key in seen_names:
                continue
            seen_names.add(key)

            if kind == "journal":
                rank = _get_cell(row, 2)
                ccf = _get_cell(row, 3)
            else:
                ccf = _get_cell(row, 2)
                rank = _get_cell(row, 3)

            venues.append(
                Venue(
                    kind=kind,
                    name=name,
                    field=_get_cell(row, 0),
                    rank=rank,
                    ccf=ccf,
                    url=_get_cell(row, 4),
                    openalex_source_id=_get_cell(row, _column_for_header(header, "openalex_source_id", 5)),
                )
            )

    return venues


def _infer_sheet_kind(sheet_name: str, header: list[str]) -> str | None:
    header_text = " ".join(header).lower()
    sheet_text = sheet_name.lower()
    if "journal" in header_text or "journal" in sheet_text:
        return "journal"
    if "conference" in header_text or "conference" in sheet_text:
        return "conference"
    return None


def _get_cell(row: list[str], index: int) -> str | None:
    if index >= len(row):
        return None
    value = str(row[index] or "").strip()
    return value or None


def _column_for_header(header: list[str], name: str, default: int) -> int:
    for index, value in enumerate(header):
        if value.strip().lower() == name.lower():
            return index
    return default


def _read_xlsx_sheets(path: Path) -> list[tuple[str, list[list[str]]]]:
    with zipfile.ZipFile(path) as archive:
        shared_strings = _read_shared_strings(archive)
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        relmap = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels}

        sheets_node = workbook.find("main:sheets", NS)
        if sheets_node is None:
            return []

        sheets = []
        for sheet in sheets_node:
            name = sheet.attrib["name"]
            rid = sheet.attrib[f"{{{REL_NS}}}id"]
            target = relmap[rid].lstrip("/")
            sheet_path = target if target.startswith("xl/") else f"xl/{target}"
            root = ET.fromstring(archive.read(sheet_path))
            rows = [_read_row(row, shared_strings) for row in root.findall(".//main:sheetData/main:row", NS)]
            sheets.append((name, rows))
        return sheets


def _read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []

    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return [
        "".join(text.text or "" for text in item.findall(".//main:t", NS))
        for item in root.findall("main:si", NS)
    ]


def _read_row(row: ET.Element, shared_strings: list[str]) -> list[str]:
    values: list[str] = []
    for cell in row.findall("main:c", NS):
        cell_ref = cell.attrib.get("r", "")
        column_index = _column_index(cell_ref)
        while len(values) < column_index:
            values.append("")
        values.append(_read_cell(cell, shared_strings))
    return values


def _read_cell(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "s":
        value = cell.find("main:v", NS)
        if value is None or value.text is None:
            return ""
        return shared_strings[int(value.text)]

    if cell_type == "inlineStr":
        return "".join(text.text or "" for text in cell.findall(".//main:t", NS))

    value = cell.find("main:v", NS)
    return value.text if value is not None and value.text is not None else ""


def _column_index(cell_ref: str) -> int:
    letters = "".join(ch for ch in cell_ref if ch.isalpha())
    if not letters:
        return 0

    index = 0
    for char in letters.upper():
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1
