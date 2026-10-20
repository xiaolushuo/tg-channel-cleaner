# -*- coding: utf-8 -*-
"""导出 CSV / Excel（移植自 Python 版；xlsx 手写 OOXML，零第三方依赖）。"""
import csv
import io
import zipfile

XL_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
XL_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
XL_CELL_LIMIT = 32767
XL_SHEET_NAME_LIMIT = 31
XL_SHEET_BAD_CHARS = '[]:*?/\\'

XS_TEXT, XS_HEAD, XS_INT, XS_BOLD, XS_TITLE, XS_WRAP = 0, 1, 2, 3, 4, 5

STYLES_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<styleSheet xmlns="{XL_NS}">'
    '<fonts count="3">'
    '<font><sz val="11"/><color theme="1"/><name val="Calibri"/></font>'
    '<font><b/><sz val="11"/><color theme="1"/><name val="Calibri"/></font>'
    '<font><b/><sz val="14"/><color theme="1"/><name val="Calibri"/></font>'
    '</fonts>'
    '<fills count="3">'
    '<fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill>'
    '<fill><patternFill patternType="solid">'
    '<fgColor rgb="FFDDEBF7"/><bgColor indexed="64"/></patternFill></fill>'
    '</fills>'
    '<borders count="2">'
    '<border><left/><right/><top/><bottom/><diagonal/></border>'
    '<border><left/><right/><top/>'
    '<bottom style="thin"><color rgb="FFB0B7C3"/></bottom><diagonal/></border>'
    '</borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="6">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0"'
    ' applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1">'
    '<alignment vertical="center"/></xf>'
    '<xf numFmtId="3" fontId="0" fillId="0" borderId="0" xfId="0"'
    ' applyNumberFormat="1"/>'
    '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
    '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1">'
    '<alignment vertical="top" wrapText="1"/></xf>'
    '</cellXfs>'
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    '</styleSheet>'
)

XL_CELL_STYLES = {"s": XS_TEXT, "h": XS_HEAD, "i": XS_INT, "b": XS_BOLD, "t": XS_TITLE, "w": XS_WRAP}
XL_FORMULA_TRIGGERS = ("=", "+", "@", "\t", "\r")


def xml_escape(value):
    """XML 文本转义 + 丢控制字符（0x00-0x08/0x0B/0x0C/0x0E-0x1F）。"""
    out = []
    for ch in str(value):
        code = ord(ch)
        if code < 0x20 and ch not in "\t\n\r":
            continue
        if ch == "&":
            out.append("&amp;")
        elif ch == "<":
            out.append("&lt;")
        elif ch == ">":
            out.append("&gt;")
        elif ch == '"':
            out.append("&quot;")
        else:
            out.append(ch)
    return "".join(out)


def csv_guard(value):
    """CSV 单元格防公式注入：以 = + @ 或制表/回车开头的文本加单引号。"""
    text = "" if value is None else str(value)
    return ("'" + text) if text.startswith(XL_FORMULA_TRIGGERS) else text


def xl_col_letter(n):
    letters = ""
    n = int(n)
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters or "A"


def xl_sheet_name(name, used=None):
    raw = str(name or "").strip() or "Sheet"
    for ch in XL_SHEET_BAD_CHARS:
        raw = raw.replace(ch, "-")
    raw = raw[:XL_SHEET_NAME_LIMIT]
    if used is None:
        return raw
    base, out, i = raw, raw, 2
    while out in used:
        tail = f"({i})"
        out = base[:XL_SHEET_NAME_LIMIT - len(tail)] + tail
        i += 1
    used.add(out)
    return out


def xl_cell_token(value, style=None):
    """Python 值 -> (样式索引, 类型, 值)：s=文本 / i=数字 / e=空。"""
    if isinstance(value, tuple) and len(value) == 2 and value[0] in XL_CELL_STYLES:
        style = XL_CELL_STYLES[value[0]]
        value = value[1]
    if value is None or value == "":
        return (XS_TEXT if style is None else style), "e", ""
    if isinstance(value, bool):
        return (XS_TEXT if style is None else style), "s", str(value)
    if isinstance(value, (int, float)):
        return (XS_INT if style is None else style), "i", value
    return (XS_TEXT if style is None else style), "s", str(value)[:XL_CELL_LIMIT]


def xl_cell_xml(ref, token):
    style, kind, value = token
    s_attr = f' s="{style}"' if style else ""
    if kind == "e":
        return f'<c r="{ref}"{s_attr}/>'
    if kind == "i":
        return f'<c r="{ref}"{s_attr}><v>{value}</v></c>'
    return (f'<c r="{ref}"{s_attr} t="inlineStr"><is>'
            f'<t xml:space="preserve">{xml_escape(value)}</t></is></c>')


def xl_sheet_xml(sheet):
    freeze = bool(sheet.get("freeze", True))
    do_filter = bool(sheet.get("filter", True))
    widths = list(sheet.get("cols") or [])
    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             f'<worksheet xmlns="{XL_NS}">']
    if freeze:
        parts.append('<sheetViews><sheetView workbookViewId="0">'
                     '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft"'
                     ' state="frozen"/></sheetView></sheetViews>')
    else:
        parts.append('<sheetViews><sheetView workbookViewId="0"/></sheetViews>')
    parts.append('<sheetFormatPr defaultRowHeight="15"/>')
    if widths:
        parts.append("<cols>" + "".join(
            f'<col min="{i + 1}" max="{i + 1}" width="{max(4, w)}" customWidth="1"/>'
            for i, w in enumerate(widths)) + "</cols>")
    parts.append("<sheetData>")
    for idx, row in enumerate(sheet.get("rows") or [], 1):
        cells = "".join(xl_cell_xml(f"{xl_col_letter(i + 1)}{idx}", xl_cell_token(v))
                        for i, v in enumerate(row))
        parts.append(f'<row r="{idx}">{cells}</row>')
    parts.append("</sheetData>")
    if do_filter and widths:
        parts.append(f'<autoFilter ref="A1:{xl_col_letter(len(widths))}1"/>')
    parts.append("</worksheet>")
    return "".join(parts)


def build_xlsx(sheets):
    """若干张表 -> xlsx 字节流。sheets=[{name,cols,rows,freeze,filter}]。"""
    used_names = set()
    norm = []
    for sh in (sheets or []):
        if not isinstance(sh, dict):
            continue
        norm.append({
            "name": xl_sheet_name(sh.get("name"), used_names),
            "cols": list(sh.get("cols") or []),
            "rows": [list(r) for r in (sh.get("rows") or [])],
            "freeze": bool(sh.get("freeze", True)),
            "filter": bool(sh.get("filter", True)),
        })
    if not norm:
        norm = [{"name": "Sheet1", "cols": [], "rows": [], "freeze": False, "filter": False}]

    sheet_tags = "".join(
        f'<sheet name="{xml_escape(sh["name"])}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
        for i, sh in enumerate(norm))
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<workbook xmlns="{XL_NS}" xmlns:r="{XL_R_NS}">'
                f'<sheets>{sheet_tags}</sheets></workbook>')

    styles_rid = len(norm) + 1
    wb_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               f'<Relationships xmlns="{PKG_NS}">'
               + "".join(f'<Relationship Id="rId{i + 1}" Type="{XL_R_NS}/worksheet"'
                         f' Target="worksheets/sheet{i + 1}.xml"/>'
                         for i in range(len(norm)))
               + f'<Relationship Id="rId{styles_rid}" Type="{XL_R_NS}/styles"'
                 ' Target="styles.xml"/>'
               '</Relationships>')

    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     f'<Types xmlns="{CT_NS}">'
                     '<Default Extension="rels" ContentType="application/vnd.openxml'
                     'formats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.'
                     'openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                     + "".join(
                         f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml"'
                         ' ContentType="application/vnd.openxmlformats-officedocument.'
                         'spreadsheetml.worksheet+xml"/>' for i in range(len(norm)))
                     + '<Override PartName="/xl/styles.xml" ContentType="application/vnd.'
                       'openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                       '</Types>')

    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 f'<Relationships xmlns="{PKG_NS}">'
                 f'<Relationship Id="rId1" Type="{XL_R_NS}/officeDocument"'
                 ' Target="xl/workbook.xml"/></Relationships>')

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/styles.xml", STYLES_XML)
        for i, sh in enumerate(norm):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", xl_sheet_xml(sh))
    return buf.getvalue()


def build_csv(rows):
    """rows = [[cell, ...], ...] -> utf-8-sig 字节流。

    表头单元格可能是 ("h", 文本) 样式标记元组（xlsx 用），CSV 里拆出纯文本
    （v3.19.0 修复：此前 CSV 表头会写出 "('h', '名称')" 这样的元组串）。"""
    def cell(v):
        if isinstance(v, tuple) and len(v) == 2 and v[0] == "h":
            v = v[1]
        return csv_guard(v)

    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    for row in rows:
        w.writerow([cell(v) for v in row])
    return buf.getvalue().encode("utf-8-sig")


def write_export(path, sheets, fmt="xlsx"):
    """写导出文件，返回字节数。fmt=xlsx|csv。"""
    if fmt == "csv":
        # CSV 仅导出主表
        rows = sheets[0]["rows"]
        data = build_csv(rows)
    else:
        data = build_xlsx(sheets)
    with open(path, "wb") as f:
        f.write(data)
    return len(data)
