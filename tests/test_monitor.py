import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml

from monitor import __main__ as cli
from monitor import filters
from monitor.collectors import board, g2b, search
from monitor.db import Store
from monitor.notify import build_messages

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))


def _future(days):
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def api(items, total=None):
    return {"response": {"header": {"resultCode": "00", "resultMsg": "정상"},
                         "body": {"items": items, "totalCount": total if total is not None else len(items)}}}


BID_ITEMS = [
    {"bidNtceNo": "R26BK0001", "bidNtceOrd": "000", "bidNtceNm": "2026 시정 홍보영상 제작 용역",
     "ntceInsttNm": "서울특별시", "dminsttNm": "서울특별시 홍보기획관", "bidNtceDt": "2026-11-10 10:00:00",
     "bidClseDt": _future(5), "presmptPrce": "45000000", "ntceKindNm": "등록공고",
     "bidNtceDtlUrl": "https://www.g2b.go.kr/x?1", "cntrctCnclsMthdNm": "제한경쟁",
     "sucsfbidMthdNm": "협상에의한계약", "opengDt": "2026-11-20 11:00:00", "bfSpecRgstNo": "1234567",
     "stdNtceDocUrl": "https://g2b.example/std.pdf",
     "ntceSpecDocUrl1": "https://g2b.example/f1", "ntceSpecFileNm1": "제안요청서.hwp",
     "ntceSpecDocUrl2": "https://g2b.example/f2", "ntceSpecFileNm2": "과업지시서.pdf",
     "ntceSpecDocUrl3": "", "ntceSpecFileNm3": "", "ntceInsttOfclTelNo": "02-120"},
    {"bidNtceNo": "R26BK0002", "bidNtceOrd": "000", "bidNtceNm": "청사 CCTV 영상감시 시스템 유지보수",
     "ntceInsttNm": "OO구청", "bidNtceDt": "2026-11-10 11:00:00", "bidClseDt": _future(3)},
    {"bidNtceNo": "R26BK0003", "bidNtceOrd": "000", "bidNtceNm": "강당 영상장비 구매 설치",
     "ntceInsttNm": "OO대학교", "bidNtceDt": "2026-11-10 12:00:00", "bidClseDt": _future(3)},
    {"bidNtceNo": "R26BK0004", "bidNtceOrd": "000", "bidNtceNm": "청사 청소 용역",
     "ntceInsttNm": "OO공단", "bidNtceDt": "2026-11-10 12:00:00", "bidClseDt": _future(3)},
    {"bidNtceNo": "R26BK0005", "bidNtceOrd": "001", "bidNtceNm": "관광 유튜브 숏폼 콘텐츠 제작",
     "ntceInsttNm": "한국관광공사", "bidNtceDt": "2026-11-09 09:00:00", "bidClseDt": "2026-11-01 10:00:00",
     "asignBdgtAmt": "120000000", "ntceKindNm": "변경공고"},
]
PRESPEC_ITEMS = [
    {"bfSpecRgstNo": "1234567", "prdctClsfcNoNm": "2027 기관 홍보 동영상 제작", "orderInsttNm": "국민연금공단",
     "rlDminsttNm": "국민연금공단", "rcptDt": "2026-11-10 09:00:00", "opninRgstClseDt": _future(4),
     "asignBdgtAmt": "80000000",
     "specDocFileUrl1": "https://g2b.example/s1", "specDocFileNm1": "사전규격 제안요청서(안).hwpx"},
]
PLAN_ITEMS = [
    {"orderPlanUntyNo": "P2026-77", "bizNm": "다큐멘터리 촬영 및 제작", "orderInsttNm": "국립중앙박물관",
     "orderYm": "202612", "sumOrderAmt": "300000000", "nticeDt": "2026-11-08"},
]


def test_parse_stages():
    src = {"id": "t", "name": "n", "category": "입찰공고"}
    n = g2b.parse_bid(BID_ITEMS[0], src)
    assert n.uid == "R26BK0001-000" and n.amount == 45_000_000
    assert n.org == "서울특별시 (수요: 서울특별시 홍보기획관)" and n.posted == "2026-11-10"
    assert g2b.parse_bid(BID_ITEMS[4], src).kind == "변경공고"
    assert [f["name"] for f in n.files] == ["입찰공고서(표준)", "제안요청서.hwp", "과업지시서.pdf"]
    assert n.info["추정가격"] == "45,000,000원" and n.info["낙찰방법"] == "협상에의한계약"
    assert n.info["개찰일시"] == "2026-11-20 11:00" and n.info["사전규격번호"] == "1234567"
    p = g2b.parse_prespec(PRESPEC_ITEMS[0], {"id": "p", "name": "n", "category": "사전규격"})
    assert p.title == "2027 기관 홍보 동영상 제작" and p.deadline
    assert p.files == [{"name": "사전규격 제안요청서(안).hwpx", "url": "https://g2b.example/s1"}]
    pl = g2b.parse_plan(PLAN_ITEMS[0], {"id": "pl", "name": "n", "category": "발주계획"})
    assert pl.kind == "발주예정 2026-12" and pl.amount == 300_000_000
    assert pl.info["발주금액"] == "300,000,000원"


def test_items_shapes():
    assert g2b._items(api({"item": PLAN_ITEMS[0]}))[0] == [PLAN_ITEMS[0]]
    assert g2b._items(api({"item": PLAN_ITEMS}))[0] == PLAN_ITEMS
    with pytest.raises(RuntimeError):
        g2b._items({"response": {"header": {"resultCode": "30", "resultMsg": "SERVICE KEY IS NOT REGISTERED"}}})


def test_filter_keeps_video_drops_equipment():
    src = {"id": "t", "name": "n", "category": "입찰공고"}
    kept = filters.apply([g2b.parse_bid(i, src) for i in BID_ITEMS], {}, SETTINGS)
    titles = [n.title for n in kept]
    assert titles == ["2026 시정 홍보영상 제작 용역", "관광 유튜브 숏폼 콘텐츠 제작"]


class FakeResp:
    def __init__(self, payload=None, content=b""):
        self._p, self.content, self.text = payload, content, json.dumps(payload or {})
    def raise_for_status(self): pass
    def json(self): return self._p


class FakeSession:
    def __init__(self): self.calls = []
    def get(self, url, params=None, **kw):
        self.calls.append((url, params))
        if "BidPublicInfoService" in url: return FakeResp(api(BID_ITEMS))
        if "HrcspSsstndrd" in url: return FakeResp(api(PRESPEC_ITEMS))
        if "OrderPlan" in url: return FakeResp(api(PLAN_ITEMS))
        if "news.google.com" in url: return FakeResp(content=RSS)
        raise AssertionError(url)


RSS = """<?xml version="1.0"?><rss><channel>
<item><title>OO시, 시민 참여 영상 공모전 개최 - 지역일보</title><link>https://news.example/1</link>
<pubDate>Mon, 10 Nov 2026 01:00:00 GMT</pubDate><source url="x">지역일보</source></item>
<item><title>OO기관 홍보영상 제작해드립니다 저렴 - 광고</title><link>https://news.example/2</link>
<pubDate>Mon, 10 Nov 2026 01:00:00 GMT</pubDate></item>
</channel></rss>""".encode()


def test_rss_parse():
    items = search.parse_rss(RSS, {"id": "g", "name": "뉴스"})
    assert items[0].title == "OO시, 시민 참여 영상 공모전 개최" and items[0].org == "지역일보"


def test_board_auto_detect():
    html = """<html><body><nav><a href="/a">공고 메뉴입니다</a></nav>
    <table><tr><td><table>
      <tr><th>번호</th><th>제목</th><th>등록일</th></tr>
      <tr><td>3</td><td><a href="view?id=3">2026 홍보영상 제작업체 견적 공고</a></td><td>2026.11.10</td></tr>
      <tr><td>2</td><td><a href="javascript:goView('2')">청사 청소 용역 공고</a></td><td>2026.11.09</td></tr>
      <tr><td>1</td><td><a href="view?id=1">영상 공모전 안내</a></td><td>2026-11-01 ~ 2026-11-30</td></tr>
    </table></td></tr></table></body></html>"""
    src = {"id": "b", "url": "https://org.example/board/list", "category": "제작 의뢰·공모",
           "link_regex": r"goView\('(\d+)'\)", "link_template": "https://org.example/board/view?id={0}"}
    items = board.extract(html, src)
    assert [n.title for n in items] == ["2026 홍보영상 제작업체 견적 공고", "청사 청소 용역 공고", "영상 공모전 안내"]
    assert items[0].url == "https://org.example/board/view?id=3"
    assert items[1].url == "https://org.example/board/view?id=2"
    assert items[2].posted == "2026-11-01" and items[2].deadline == "2026-11-30"


def test_end_to_end(tmp_path, monkeypatch):
    fake = FakeSession()
    monkeypatch.setattr("monitor.collectors.base._session", fake)
    monkeypatch.setenv("G2B_SERVICE_KEY", "abc%2Bdef")
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "index.html").write_text((ROOT / "web" / "index.html").read_text(encoding="utf-8"))
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "settings.yaml").write_text((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    sources = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text(encoding="utf-8"))
    sources["sources"] = [s for s in sources["sources"] if s["type"] in ("g2b", "gnews")]
    (cfg / "sources.yaml").write_text(yaml.safe_dump(sources, allow_unicode=True))

    sent = []
    monkeypatch.setattr(cli.notify, "notify", lambda new, s: sent.append(new) or [])

    assert cli.main(["--config", str(cfg), "run", "--no-analyze"]) == 0
    assert sent == []                                   # 첫 수집은 알림 없음
    data = json.loads((tmp_path / "docs" / "data.json").read_text(encoding="utf-8"))
    cats = sorted({n["category"] for n in data["notices"]})
    assert cats == ["발주계획", "사전규격", "입찰공고", "제작 의뢰·공모"]
    assert all(s["ok"] for s in data["sources"])
    assert fake.calls[0][1]["serviceKey"] == "abc+def"   # Encoding 키 → Decoding
    # 광고성 뉴스 제목은 걸러짐(‘해드립니다’는 네이버 소스에만 적용되므로 키워드 기준만 확인)
    assert any("영상 공모전" in n["title"] for n in data["notices"])

    # 두 번째 실행: 새 공고 하나 추가 → 그것만 알림
    BID_ITEMS.append({"bidNtceNo": "R26BK0009", "bidNtceOrd": "000", "bidNtceNm": "축제 스케치 영상 촬영",
                      "ntceInsttNm": "OO문화재단", "bidNtceDt": "2026-11-11 09:00:00", "bidClseDt": _future(9)})
    try:
        assert cli.main(["--config", str(cfg), "run", "--no-analyze"]) == 0
    finally:
        BID_ITEMS.pop()
    assert [n.title for n in sent[0]] == ["축제 스케치 영상 촬영"]


def test_failure_isolated(tmp_path, monkeypatch):
    monkeypatch.delenv("G2B_SERVICE_KEY", raising=False)
    store = Store(tmp_path / "x.db")
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "index.html").write_text("x")
    args = type("A", (), {"only": None, "days": None, "no_notify": True, "no_analyze": True, "verbose": False})()
    cli.cmd_run(args, {"db_path": "x.db"}, [{"id": "g2b_bid", "type": "g2b", "stage": "bid"}])
    st = Store(tmp_path / "x.db").statuses()["g2b_bid"]
    assert st["ok"] is False and "G2B_SERVICE_KEY" in st["error"]


def test_message_split():
    src = {"id": "t", "name": "n", "category": "입찰공고"}
    many = [g2b.parse_bid({**BID_ITEMS[0], "bidNtceNo": str(i)}, src) for i in range(80)]
    msgs = build_messages(many, "https://dash.example")
    assert len(msgs) > 1 and all(len(m) <= 4000 for m in msgs)
    assert "새 공고 80건" in msgs[0]


def test_key_error_is_reported():
    with pytest.raises(RuntimeError, match="활용신청"):
        g2b._items({"OpenAPI_ServiceResponse": {"cmmMsgHeader": {
            "errMsg": "SERVICE_KEY_IS_NOT_REGISTERED_ERROR", "returnAuthMsg": "등록되지 않은 서비스키",
            "returnReasonCode": "30"}}})


def test_s2b_parse_real_page():
    from monitor.collectors import s2b
    html = (ROOT / "tests" / "fixtures" / "s2b_list.html").read_text(encoding="utf-8")
    items = s2b.parse(html, {"id": "s2b"})
    assert len(items) == 10
    n = next(i for i in items if "주례여고" in i.title)
    assert n.org == "주례여자고등학교" and n.posted == "2026-11-11" and n.deadline == "2026-11-13 11:00"
    assert "estimateCode=202611111655416" in n.url and n.category == "학교장터"
    kept = filters.apply(items, {}, SETTINGS)
    titles = " ".join(k.title for k in kept)
    assert "냉난방기" not in titles and "영상조명 임대" not in titles and "주례여고" in titles
