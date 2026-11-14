"""Small OOXML writer for portable database exports; no formulas or external runtime."""

import re
from xml.etree.ElementTree import Element, SubElement, tostring
from zipfile import ZIP_DEFLATED, ZipFile

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
MAX_ROWS = 1_048_576


def xml_bytes(element):
    # XML parsers normalize literal CR/CRLF; an entity preserves the source text.
    return tostring(element, encoding="utf-8", xml_declaration=True).replace(b"\r", b"&#13;")


def column_name(index):
    result = ""
    while index:
        index, rest = divmod(index - 1, 26)
        result = chr(65 + rest) + result
    return result


def needs_sidecar(value):
    return (
        len(value.encode("utf-16-le")) // 2 > 32767
        or value.count("\n") > 253
        or bool(re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]|_x[0-9a-fA-F]{4}_", value))
    )


def write_workbook(path, sheets, sidecar):
    """sheets = (name, headers, rows). All user strings are explicit inlineStr."""
    workbook = Element("workbook", xmlns=NS, attrib={"xmlns:r": REL})
    definitions = SubElement(workbook, "sheets")
    relationships = Element("Relationships", xmlns=PKG)
    types = Element("Types", xmlns="http://schemas.openxmlformats.org/package/2006/content-types")
    for ext, kind in (
        ("rels", "application/vnd.openxmlformats-package.relationships+xml"),
        ("xml", "application/xml"),
    ):
        SubElement(types, "Default", Extension=ext, ContentType=kind)
    for part, kind in (("/xl/workbook.xml", "sheet.main"), ("/xl/styles.xml", "styles")):
        SubElement(
            types,
            "Override",
            PartName=part,
            ContentType=f"application/vnd.openxmlformats-officedocument.spreadsheetml.{kind}+xml",
        )
    with ZipFile(path, "x", ZIP_DEFLATED) as archive:
        sheet_no = 0
        for name, headers, rows in sheets:
            chunks = [rows[i : i + MAX_ROWS - 1] for i in range(0, len(rows), MAX_ROWS - 1)] or [[]]
            for part_no, chunk in enumerate(chunks, 1):
                sheet_no += 1
                title = name if len(chunks) == 1 else f"{name[:25]} {part_no}"
                SubElement(
                    definitions,
                    "sheet",
                    name=title,
                    sheetId=str(sheet_no),
                    attrib={"r:id": f"rId{sheet_no}"},
                )
                SubElement(
                    relationships,
                    "Relationship",
                    Id=f"rId{sheet_no}",
                    Type=f"{REL}/worksheet",
                    Target=f"worksheets/sheet{sheet_no}.xml",
                )
                SubElement(
                    types,
                    "Override",
                    PartName=f"/xl/worksheets/sheet{sheet_no}.xml",
                    ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml",
                )
                sheet = Element("worksheet", xmlns=NS, attrib={"xmlns:r": REL})
                views = SubElement(sheet, "sheetViews")
                view = SubElement(views, "sheetView", workbookViewId="0")
                SubElement(
                    view,
                    "pane",
                    ySplit="1",
                    topLeftCell="A2",
                    activePane="bottomLeft",
                    state="frozen",
                )
                cols = SubElement(sheet, "cols")
                for col, header in enumerate(headers, 1):
                    width = (
                        48
                        if any(key in header for key in ("текст", "Описание", "payload", "note"))
                        else 24
                    )
                    SubElement(
                        cols, "col", min=str(col), max=str(col), width=str(width), customWidth="1"
                    )
                data = SubElement(sheet, "sheetData")
                links = []
                for row_no, values in enumerate([headers, *chunk], 1):
                    row = SubElement(data, "row", r=str(row_no))
                    for col, value in enumerate(values, 1):
                        if value is None:
                            continue
                        ref = f"{column_name(col)}{row_no}"
                        attrs = {"r": ref, "s": "1" if row_no == 1 else "0"}
                        if isinstance(value, int) and abs(value) < 10**15:
                            cell = SubElement(row, "c", attrs)
                            SubElement(cell, "v").text = str(value)
                        else:
                            text = str(value)
                            if needs_sidecar(text):
                                relative = sidecar(f"sheet{sheet_no}-{ref}", text)
                                text = f"Полный текст: {relative} (ограничение Excel)"
                                links.append((ref, relative))
                            attrs["t"] = "inlineStr"
                            cell = SubElement(row, "c", attrs)
                            inline = SubElement(cell, "is")
                            SubElement(inline, "t", {"xml:space": "preserve"}).text = text
                SubElement(
                    sheet, "autoFilter", ref=f"A1:{column_name(len(headers))}{len(chunk) + 1}"
                )
                if links:
                    hyperlinks = SubElement(sheet, "hyperlinks")
                    rels = Element("Relationships", xmlns=PKG)
                    for i, (ref, target) in enumerate(links, 1):
                        SubElement(hyperlinks, "hyperlink", ref=ref, attrib={"r:id": f"link{i}"})
                        SubElement(
                            rels,
                            "Relationship",
                            Id=f"link{i}",
                            Type=f"{REL}/hyperlink",
                            Target=target,
                            TargetMode="External",
                        )
                    archive.writestr(
                        f"xl/worksheets/_rels/sheet{sheet_no}.xml.rels", xml_bytes(rels)
                    )
                archive.writestr(f"xl/worksheets/sheet{sheet_no}.xml", xml_bytes(sheet))
        SubElement(
            relationships, "Relationship", Id="styles", Type=f"{REL}/styles", Target="styles.xml"
        )
        archive.writestr("xl/workbook.xml", xml_bytes(workbook))
        archive.writestr("xl/_rels/workbook.xml.rels", xml_bytes(relationships))
        archive.writestr("[Content_Types].xml", xml_bytes(types))
        root_rels = Element("Relationships", xmlns=PKG)
        SubElement(
            root_rels,
            "Relationship",
            Id="rId1",
            Type=f"{REL}/officeDocument",
            Target="xl/workbook.xml",
        )
        archive.writestr("_rels/.rels", xml_bytes(root_rels))
        archive.writestr("xl/styles.xml", STYLES)


STYLES = f'''<?xml version="1.0" encoding="UTF-8"?>
<styleSheet xmlns="{NS}">
 <fonts count="2">
 <font>
 <sz val="11"/>
 <name val="Calibri"/>
 </font>
 <font>
 <b/>
 <color rgb="FFFFFFFF"/>
 <sz val="11"/>
 <name val="Calibri"/>
 </font>
 </fonts>
 <fills count="3">
 <fill>
 <patternFill patternType="none"/>
 </fill>
 <fill>
 <patternFill patternType="gray125"/>
 </fill>
 <fill>
 <patternFill patternType="solid">
 <fgColor rgb="FF175CD3"/>
 <bgColor indexed="64"/>
 </patternFill>
 </fill>
 </fills>
 <borders count="1"><border/></borders>
 <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
 <cellXfs count="2">
 <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1">
 <alignment vertical="top" wrapText="1"/>
 </xf>
 <xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyAlignment="1">
 <alignment vertical="top" wrapText="1"/>
 </xf>
 </cellXfs>
 <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''
