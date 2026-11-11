import io
import json
import struct
import zipfile
import zlib
from types import SimpleNamespace

from monitor import analyze
from monitor.db import Store
from monitor.docs_text import _hwp_records, sniff, to_docs


def _hwpx(paragraphs):
    xml = ('<sec xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
           + "".join(f"<hp:p><hp:run><hp:t>{p}</hp:t></hp:run></hp:p>" for p in paragraphs)
           + '<hp:tbl><hp:tr><hp:tc><hp:p><hp:t>수행실적</hp:t></hp:p></hp:tc>'
             '<hp:tc><hp:p><hp:t>10점</hp:t></hp:p></hp:tc></hp:tr></hp:tbl></sec>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("Contents/section0.xml", xml)
    return buf.getvalue()


def test_hwpx_and_zip():
    data = _hwpx(["용역기간: 계약일로부터 90일", "기술:가격 = 90:10"])
    assert sniff("a.hwpx", data) == "hwpx"
    text = to_docs("제안요청서.hwpx", data)[0].text
    assert "계약일로부터 90일" in text and "수행실적 | " in text and "10점" in text
    # 한글 파일명(CP949)이 든 zip 안의 hwpx + pdf
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("XXXXXXXXXX.hwpx", data)          # 10바이트 자리표시자
        z.writestr("b.pdf", b"%PDF-1.4 fake")
    raw = buf.getvalue().replace(b"XXXXXXXXXX", "제안요청서".encode("cp949"))  # CP949 파일명(플래그 없음)
    docs = to_docs("첨부.zip", raw)
    assert [d.name for d in docs] == ["제안요청서.hwpx", "b.pdf"]
    assert [d.kind for d in docs] == ["text", "pdf"]


def test_hwp_records():
    def rec(tag, payload):
        return struct.pack("<I", tag | (len(payload) << 20)) + payload
    para = "배점 ".encode("utf-16-le") + struct.pack("<H", 11) + b"\x00" * 14 + "정량 30점".encode("utf-16-le")
    raw = rec(66, b"\x00" * 8) + rec(67, para) + rec(67, "실적 15점".encode("utf-16-le"))
    assert _hwp_records(raw) == "배점 정량 30점\n실적 15점"


def test_pick_files_priority():
    files = [{"name": "청렴계약 이행서약서.hwp"}, {"name": "입찰공고서.pdf"},
             {"name": "기타.hwp"}, {"name": "제안요청서.hwp"}]
    assert [f["name"] for f in analyze._pick_files(files, 3)] == ["제안요청서.hwp", "입찰공고서.pdf", "기타.hwp"]


RESULT = {
    "summary": "시정 홍보영상 3편 제작", "budget": "45,000,000원(부가세 포함)",
    "period": "계약일로부터 120일", "contract_method": "협상에 의한 계약", "tech_price_ratio": "90:10",
    "eligibility": ["영상제작업 신고 업체"], "track_record": ["최근 3년 유사용역 실적 10점: 5건 이상 10점"],
    "quantitative": [{"item": "수행실적", "points": "10", "detail": "최근 3년"}],
    "qualitative": [{"item": "기획력", "points": "30", "detail": "콘셉트"}],
    "key_dates": [{"label": "제안서 제출", "date": "2026-11-20 18:00"}],
    "deliverables": ["홍보영상 3편(각 3분)"], "notes": ["발표평가 필수"], "not_found": [],
}


class FakeClient:
    def __init__(self):
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(stop_reason="end_turn", model=kw["model"],
                               content=[SimpleNamespace(type="text", text=json.dumps(RESULT, ensure_ascii=False))])


def test_analyze_notice(monkeypatch):
    hwpx = _hwpx(["용역기간: 계약일로부터 120일"])
    resp = SimpleNamespace(content=hwpx, headers={}, raise_for_status=lambda: None)
    monkeypatch.setattr("monitor.analyze.session", lambda: SimpleNamespace(get=lambda url, timeout: resp))
    client = FakeClient()
    notice = {"title": "홍보영상 제작", "org": "서울시", "category": "입찰공고",
              "info": {"추정가격": "45,000,000원"},
              "files": [{"name": "제안요청서.hwpx", "url": "https://x/1"}]}
    out = analyze.analyze_notice(notice, {}, client=client)
    assert out["period"] == "계약일로부터 120일" and out["files_read"] == ["제안요청서.hwpx"]
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
    doc = call["messages"][0]["content"][0]
    assert doc["type"] == "document" and "120일" in doc["source"]["data"]


def test_run_skips_without_key_and_records(tmp_path, monkeypatch):
    store = Store(tmp_path / "d.db")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    analyze.run(store, {}, "2026-11-11 10:00")   # 키 없으면 조용히 건너뜀
    assert store.analyses() == {}
