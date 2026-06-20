import zipfile

from zotero_arxiv_daily.venues import clean_venue_name, load_venues_from_excel, venue_search_names


def test_clean_venue_name_removes_added_marker():
    assert clean_venue_name("International Conference on Learning Representations (ICLR) - *新增*") == (
        "International Conference on Learning Representations (ICLR)"
    )


def test_venue_search_names_includes_without_suffix_and_acronym():
    names = venue_search_names("IEEE Transactions on Software Engineering (TSE)")
    assert names == [
        "IEEE Transactions on Software Engineering (TSE)",
        "IEEE Transactions on Software Engineering",
        "TSE",
    ]


def test_load_venues_from_excel(tmp_path):
    path = tmp_path / "venues.xlsx"
    _write_minimal_venue_xlsx(path)

    venues = load_venues_from_excel(path)

    assert len(venues) == 2
    assert venues[0].kind == "journal"
    assert venues[0].name == "IEEE Transactions on Software Engineering (TSE)"
    assert venues[0].ccf == "CCF A"
    assert venues[1].kind == "conference"
    assert venues[1].name == "International Conference on Software Engineering (ICSE)"


def _write_minimal_venue_xlsx(path):
    workbook_xml = """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="Journals" sheetId="1" r:id="rId1"/>
    <sheet name="Conferences" sheetId="2" r:id="rId2"/>
  </sheets>
</workbook>
"""
    rels_xml = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>
</Relationships>
"""
    sheet1 = _sheet_xml(
        [
            ["Field", "Journal Name", "Rank", "CCF", "URL"],
            ["Software Engineering", "IEEE Transactions on Software Engineering (TSE)", "Q1", "CCF A", "https://example.com/tse"],
        ]
    )
    sheet2 = _sheet_xml(
        [
            ["Field", "Conference Name", "CCF", "Indexing", "URL"],
            ["Software Engineering", "International Conference on Software Engineering (ICSE)", "CCF A", "Top", "https://example.com/icse"],
        ]
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", rels_xml)
        archive.writestr("xl/worksheets/sheet1.xml", sheet1)
        archive.writestr("xl/worksheets/sheet2.xml", sheet2)


def _sheet_xml(rows):
    row_xml = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for column_index, value in enumerate(row):
            cell_ref = f"{chr(ord('A') + column_index)}{row_index}"
            cells.append(f'<c r="{cell_ref}" t="inlineStr"><is><t>{value}</t></is></c>')
        row_xml.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(row_xml)}</sheetData>'
        "</worksheet>"
    )
