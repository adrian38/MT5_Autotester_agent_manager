/* Minimal OOXML workbook writer for browser downloads. */

const excelEncoder = new TextEncoder();

function excelXml(value) {
  return String(value ?? '')
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, '')
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&apos;');
}

function excelColumn(index) {
  let name = '';
  for (let value = index + 1; value; value = Math.floor((value - 1) / 26)) {
    name = String.fromCharCode(65 + ((value - 1) % 26)) + name;
  }
  return name;
}

function excelSheet(rows) {
  const body = rows.map((row, rowIndex) => {
    const cells = row.map((value, columnIndex) => {
      const reference = `${excelColumn(columnIndex)}${rowIndex + 1}`;
      const style = rowIndex === 0 ? ' s="1"' : ' s="2"';
      return `<c r="${reference}" t="inlineStr"${style}><is><t xml:space="preserve">${excelXml(value)}</t></is></c>`;
    }).join('');
    return `<row r="${rowIndex + 1}">${cells}</row>`;
  }).join('');
  const lastColumn = excelColumn(Math.max(0, (rows[0] || []).length - 1));
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <dimension ref="A1:${lastColumn}${Math.max(1, rows.length)}"/>
  <sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>
  <cols><col min="1" max="2" width="18" customWidth="1"/><col min="3" max="11" width="36" customWidth="1"/></cols>
  <sheetData>${body}</sheetData><autoFilter ref="A1:${lastColumn}${Math.max(1, rows.length)}"/>
</worksheet>`;
}

function excelParts(rows, sheetName) {
  return {
    '[Content_Types].xml': '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>',
    '_rels/.rels': '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
    'xl/workbook.xml': `<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="${excelXml(sheetName)}" sheetId="1" r:id="rId1"/></sheets></workbook>`,
    'xl/_rels/workbook.xml.rels': '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
    'xl/styles.xml': '<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF315B7D"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment wrapText="1" vertical="top"/></xf><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment wrapText="1" vertical="top"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>',
    'xl/worksheets/sheet1.xml': excelSheet(rows),
  };
}

function excelCrc32(bytes) {
  let crc = 0xFFFFFFFF;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ (0xEDB88320 & -(crc & 1));
  }
  return (crc ^ 0xFFFFFFFF) >>> 0;
}

function excelRecord(size) {
  const bytes = new Uint8Array(size);
  return {bytes, view: new DataView(bytes.buffer)};
}

function excelConcat(parts) {
  const size = parts.reduce((total, part) => total + part.length, 0);
  const output = new Uint8Array(size);
  let offset = 0;
  for (const part of parts) { output.set(part, offset); offset += part.length; }
  return output;
}

function excelZip(parts) {
  const localParts = [];
  const centralParts = [];
  let offset = 0;
  for (const [path, content] of Object.entries(parts)) {
    const name = excelEncoder.encode(path);
    const data = excelEncoder.encode(content);
    const crc = excelCrc32(data);
    const local = excelRecord(30);
    local.view.setUint32(0, 0x04034B50, true); local.view.setUint16(4, 20, true);
    local.view.setUint16(6, 0x0800, true); local.view.setUint32(14, crc, true);
    local.view.setUint32(18, data.length, true); local.view.setUint32(22, data.length, true);
    local.view.setUint16(26, name.length, true);
    localParts.push(local.bytes, name, data);
    const central = excelRecord(46);
    central.view.setUint32(0, 0x02014B50, true); central.view.setUint16(4, 20, true);
    central.view.setUint16(6, 20, true); central.view.setUint16(8, 0x0800, true);
    central.view.setUint32(16, crc, true); central.view.setUint32(20, data.length, true);
    central.view.setUint32(24, data.length, true); central.view.setUint16(28, name.length, true);
    central.view.setUint32(42, offset, true);
    centralParts.push(central.bytes, name);
    offset += local.bytes.length + name.length + data.length;
  }
  const central = excelConcat(centralParts);
  const end = excelRecord(22);
  end.view.setUint32(0, 0x06054B50, true); end.view.setUint16(8, Object.keys(parts).length, true);
  end.view.setUint16(10, Object.keys(parts).length, true); end.view.setUint32(12, central.length, true);
  end.view.setUint32(16, offset, true);
  return excelConcat([...localParts, central, end.bytes]);
}

function buildExcelWorkbook(rows, sheetName = 'Auditoría') {
  const bytes = excelZip(excelParts(rows, sheetName));
  return new Blob([bytes], {type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'});
}
