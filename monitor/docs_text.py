"""첨부서류(HWP / HWPX / PDF / DOCX / ZIP)에서 내용을 꺼낸다.

PDF 는 표 모양을 살리기 위해 원본 그대로 AI 에 전달하고,
HWP·HWPX·DOCX 는 여기서 텍스트로 변환한다 (표는 셀을 ' | ' 로 구분).
"""
from __future__ import annotations

import io
import re
import struct
import zipfile
import zlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass


@dataclass
class Doc:
    name: str
    kind: str          # "pdf" | "text"
    data: bytes = b""  # pdf 원본
    text: str = ""


def sniff(name: str, data: bytes) -> str:
    n = name.lower()
    if data[:4] == b"%PDF" or n.endswith(".pdf"):
        return "pdf"
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "hwp"           # OLE (HWP 5.x, 구형 doc 도 같은 시그니처)
    if data[:2] == b"PK":
        try:
            names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        except zipfile.BadZipFile:
            return "unknown"
        if any(x.startswith("Contents/section") for x in names):
            return "hwpx"
        if "word/document.xml" in names:
            return "docx"
        return "zip"
    if n.endswith((".txt", ".csv")):
        return "txt"
    return "unknown"


# ---------- HWP 5.x ----------

def hwp_text(data: bytes) -> str:
    import olefile

    ole = olefile.OleFileIO(io.BytesIO(data))
    header = ole.openstream("FileHeader").read()
    compressed = bool(header[36] & 1)
    if header[36] & 4:
        raise ValueError("배포용(암호화) 문서라 읽을 수 없습니다")
    sections = sorted(
        (e for e in ole.listdir() if e[0] == "BodyText"),
        key=lambda e: int(e[1].replace("Section", "") or 0),
    )
    out = []
    for sec in sections:
        raw = ole.openstream(sec).read()
        if compressed:
            raw = zlib.decompress(raw, -15)
        out.append(_hwp_records(raw))
    return "\n".join(out)


def _hwp_records(raw: bytes) -> str:
    PARA_TEXT = 67
    lines, i = [], 0
    while i + 4 <= len(raw):
        h = struct.unpack_from("<I", raw, i)[0]
        i += 4
        tag, size = h & 0x3FF, (h >> 20) & 0xFFF
        if size == 0xFFF:
            size = struct.unpack_from("<I", raw, i)[0]
            i += 4
        if tag == PARA_TEXT:
            lines.append(_hwp_para(raw[i:i + size]))
        i += size
    return "\n".join(l for l in lines if l.strip())


def _hwp_para(b: bytes) -> str:
    chars = b.decode("utf-16-le", errors="ignore")
    out, i = [], 0
    while i < len(chars):
        c = ord(chars[i])
        if c >= 32:
            out.append(chars[i]); i += 1
        elif c in (10, 13):
            out.append("\n"); i += 1
        elif c == 9:
            out.append("\t"); i += 8
        elif c == 0 or 24 <= c <= 31:
            i += 1                        # 1글자짜리 제어문자
        else:
            i += 8                        # 표·그림 등 8글자짜리 제어문자
    return "".join(out)


# ---------- HWPX / DOCX (XML) ----------

def _xml_text(xml: bytes, para: str, cell: str, text: str) -> str:
    """표 한 행은 한 줄로: 셀 사이는 ' | ', 행 끝은 줄바꿈, 셀 안 문단은 공백으로."""
    out, in_cell = [], 0
    for ev, el in ET.iterparse(io.BytesIO(xml), events=("start", "end")):
        tag = el.tag.rsplit("}", 1)[-1]
        if ev == "start":
            if tag == cell:
                in_cell += 1
            continue
        if tag == text and el.text:
            out.append(el.text)
        elif tag == para:
            out.append(" " if in_cell else "\n")
        elif tag == cell:
            in_cell -= 1
            out.append(" | ")
        elif tag == "tr" and not in_cell:
            out.append("\n")
    return re.sub(r" +\| ", " | ", "".join(out))


def hwpx_text(data: bytes) -> str:
    z = zipfile.ZipFile(io.BytesIO(data))
    secs = sorted((n for n in z.namelist() if n.startswith("Contents/section") and n.endswith(".xml")),
                  key=lambda n: int("".join(ch for ch in n if ch.isdigit()) or 0))
    return "\n".join(_xml_text(z.read(s), "p", "tc", "t") for s in secs)


def docx_text(data: bytes) -> str:
    z = zipfile.ZipFile(io.BytesIO(data))
    return _xml_text(z.read("word/document.xml"), "p", "tc", "t")


# ---------- 진입점 ----------

def to_docs(name: str, data: bytes, depth: int = 0) -> list[Doc]:
    kind = sniff(name, data)
    if kind == "pdf":
        return [Doc(name, "pdf", data=data)]
    if kind == "hwp":
        return [Doc(name, "text", text=hwp_text(data))]
    if kind == "hwpx":
        return [Doc(name, "text", text=hwpx_text(data))]
    if kind == "docx":
        return [Doc(name, "text", text=docx_text(data))]
    if kind == "txt":
        return [Doc(name, "text", text=data.decode("utf-8", errors="ignore"))]
    if kind == "zip" and depth == 0:
        out = []
        z = zipfile.ZipFile(io.BytesIO(data))
        for info in z.infolist():
            if info.is_dir() or info.file_size > 50_000_000:
                continue
            fname = _zip_name(info)
            try:
                out.extend(to_docs(fname, z.read(info), depth + 1))
            except Exception:  # noqa: BLE001 — 일부 파일이 깨져도 나머지는 사용
                continue
        return out
    return []


def _zip_name(info: zipfile.ZipInfo) -> str:
    """한국 zip 은 파일명이 CP949 인 경우가 많다."""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("cp949")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename
