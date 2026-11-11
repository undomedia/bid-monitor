"""제안요청서 AI 분석: 첨부서류를 읽고 배점·용역기간·참가자격 등을 뽑는다.

ANTHROPIC_API_KEY 환경변수가 있어야 동작한다. (https://console.anthropic.com)
입찰공고·사전규격 중 첨부서류가 있는 공고만, 한 번 분석한 공고는 다시 분석하지 않는다.
"""
from __future__ import annotations

import base64
import json
import os
import re

from .collectors.base import session
from .docs_text import Doc, to_docs

# 제안요청서·과업지시서·평가기준이 담긴 파일을 먼저 본다
PRIORITY = ["제안요청", "RFP", "과업지시", "과업내용", "평가", "입찰공고", "공고문", "공고서", "규격", "내역"]
SKIP = ["서식", "양식", "청렴", "보안서약", "각서", "위임장", "확약서", "사용인감"]

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "budget", "period", "contract_method", "tech_price_ratio",
                 "eligibility", "track_record", "quantitative", "qualitative",
                 "key_dates", "deliverables", "notes", "not_found"],
    "properties": {
        "summary": {"type": "string", "description": "사업 내용 2~3문장 요약"},
        "budget": {"type": "string", "description": "사업예산·추정가격 (부가세 포함 여부 명시)"},
        "period": {"type": "string", "description": "용역(계약)기간. 예: 계약일로부터 120일 / 2027.12.31.까지"},
        "contract_method": {"type": "string", "description": "계약방법·낙찰자 결정방법. 예: 협상에 의한 계약(제한경쟁)"},
        "tech_price_ratio": {"type": "string", "description": "기술:가격 평가 비율. 예: 90:10"},
        "eligibility": {"type": "array", "items": {"type": "string"},
                        "description": "입찰 참가자격 (업종 등록, 지역 제한, 중소기업 여부, 공동수급 등)"},
        "track_record": {"type": "array", "items": {"type": "string"},
                         "description": "실적 관련 평가 기준 전부: 배점, 인정 기간, 인정 범위(유사용역 정의), 금액·건수 구간별 점수"},
        "quantitative": {"type": "array", "description": "정량평가 항목",
                         "items": {"$ref": "#/$defs/score"}},
        "qualitative": {"type": "array", "description": "정성평가(제안서·발표) 항목",
                        "items": {"$ref": "#/$defs/score"}},
        "key_dates": {"type": "array", "description": "제안서 제출, 발표평가, 설명회 등 일정",
                      "items": {"type": "object", "additionalProperties": False,
                                "required": ["label", "date"],
                                "properties": {"label": {"type": "string"}, "date": {"type": "string"}}}},
        "deliverables": {"type": "array", "items": {"type": "string"},
                         "description": "주요 과업·납품물 (영상 편수, 러닝타임, 형식, 촬영 범위 등)"},
        "notes": {"type": "array", "items": {"type": "string"},
                  "description": "제안 준비 시 주의할 점 (발표 필수, 시안 제출, 저작권 귀속, 하도급 제한 등)"},
        "not_found": {"type": "array", "items": {"type": "string"},
                      "description": "서류에서 찾지 못한 항목 이름"},
    },
    "$defs": {
        "score": {"type": "object", "additionalProperties": False,
                  "required": ["item", "points", "detail"],
                  "properties": {"item": {"type": "string"}, "points": {"type": "string"},
                                 "detail": {"type": "string", "description": "세부 평가기준 요약"}}},
    },
}

SYSTEM = """당신은 공공 영상제작 용역 입찰을 준비하는 영상제작사의 입찰 담당자를 돕는 분석가입니다.
첨부된 입찰공고서·제안요청서·과업지시서를 읽고, 제안 준비에 필요한 핵심 정보를 정해진 JSON 형식으로 정리하세요.

- 서류에 적힌 내용만 쓰고, 추측하지 마세요. 찾지 못한 항목은 빈 값으로 두고 not_found 에 이름을 적으세요.
- 금액·점수·기간·날짜는 서류의 표기 그대로 옮기세요.
- 실적(용역수행실적, 유사실적) 평가는 특히 중요합니다. 배점, 인정 기간, 유사용역 인정 범위, 금액·건수 구간별 점수를 빠짐없이 track_record 에 적으세요.
- 정량·정성 배점표는 항목별로 나누어 적고, 세부 기준이 있으면 detail 에 짧게 요약하세요.
- 모든 내용은 한국어로 짧고 명확하게 쓰세요."""


def _pick_files(files: list[dict], limit: int) -> list[dict]:
    def score(f):
        n = f.get("name", "")
        for i, w in enumerate(PRIORITY):
            if w.lower() in n.lower():
                return i
        return len(PRIORITY)
    usable = [f for f in files if not any(w in f.get("name", "") for w in SKIP)]
    return sorted(usable, key=score)[:limit]


def load_docs(notice: dict, max_files: int = 6, max_bytes: int = 30_000_000) -> tuple[list[Doc], list[str]]:
    docs, errors = [], []
    for f in _pick_files(notice.get("files", []), max_files):
        try:
            r = session().get(f["url"], timeout=60)
            r.raise_for_status()
            if len(r.content) > max_bytes:
                errors.append(f"{f['name']}: 파일이 너무 큼")
                continue
            name = f["name"]
            cd = r.headers.get("Content-Disposition", "")
            m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", cd)
            if m and "." not in name:
                name = m.group(1)
            got = to_docs(name, r.content)
            if not got:
                errors.append(f"{name}: 읽을 수 없는 형식")
            docs.extend(got)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{f.get('name')}: {type(e).__name__} {e}"[:200])
    return docs, errors


def build_content(notice: dict, docs: list[Doc]) -> list[dict]:
    content: list[dict] = []
    for d in docs:
        if d.kind == "pdf":
            content.append({"type": "document", "title": d.name[:200],
                            "source": {"type": "base64", "media_type": "application/pdf",
                                       "data": base64.standard_b64encode(d.data).decode()}})
        elif d.text.strip():
            content.append({"type": "document", "title": d.name[:200],
                            "source": {"type": "text", "media_type": "text/plain", "data": d.text}})
    info = "\n".join(f"- {k}: {v}" for k, v in (notice.get("info") or {}).items())
    content.append({"type": "text", "text":
        f"공고명: {notice['title']}\n기관: {notice.get('org', '')}\n단계: {notice['category']}\n"
        f"나라장터 기본정보:\n{info or '- 없음'}\n\n위 첨부서류를 분석해 주세요."})
    return content


def analyze_notice(notice: dict, cfg: dict, client=None) -> dict:
    import anthropic

    docs, errors = load_docs(notice, int(cfg.get("max_files", 6)))
    if not docs:
        raise RuntimeError("분석할 수 있는 첨부서류가 없습니다: " + "; ".join(errors))

    client = client or anthropic.Anthropic()
    resp = client.beta.messages.create(
        model=cfg.get("model", "claude-opus-5-5"),
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content": build_content(notice, docs)}],
        output_config={"effort": cfg.get("effort", "medium"),
                       "format": {"type": "json_schema", "schema": SCHEMA}},
        # 안전 필터가 드물게 거절할 때 다른 모델로 자동 재시도
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("AI가 분석을 거절했습니다")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("분석 결과가 너무 길어 잘렸습니다")
    text = next(b.text for b in resp.content if b.type == "text")
    result = json.loads(text)
    result["files_read"] = [d.name for d in docs]
    result["file_errors"] = errors
    result["model"] = resp.model
    return result


def _redact(text: str) -> str:
    from .__main__ import redact
    return redact(text)


def run(store, settings: dict, now: str) -> None:
    cfg = settings.get("analysis", {}) or {}
    if not cfg.get("enabled", True):
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("[분석] ANTHROPIC_API_KEY 가 없어 제안요청서 분석을 건너뜁니다")
        return
    done = store.analyzed_ids()
    stages = cfg.get("stages", ["입찰공고", "사전규격"])
    todo = [n for n in store.recent(int(cfg.get("lookback_days", 14)))
            if n["category"] in stages and n.get("files") and n["id"] not in done]
    # 마감 임박한 것부터
    todo.sort(key=lambda n: n.get("deadline") or "9999")
    limit = int(cfg.get("max_per_run", 10))
    for n in todo[:limit]:
        try:
            result = analyze_notice(n, cfg)
            store.set_analysis(n["id"], {"ok": True, "at": now, **result})
            print(f"[분석] ✔ {n['title'][:40]}")
        except Exception as e:  # noqa: BLE001
            # 실패도 기록해 같은 공고를 매번 다시 시도하지 않음 (3회까지 재시도)
            prev = store.analyses().get(n["id"], {})
            tries = prev.get("tries", 0) + 1
            store.set_analysis(n["id"], {"ok": False, "at": now, "tries": tries,
                                         "error": _redact(f"{type(e).__name__}: {e}")[:300]},
                               final=tries >= 3)
            print(f"[분석] ✘ {n['title'][:40]} — {e}")
    if len(todo) > limit:
        print(f"[분석] 남은 {len(todo) - limit}건은 다음 실행 때 분석합니다")
