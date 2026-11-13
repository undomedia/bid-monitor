"""공고 데이터 모델."""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field


@dataclass
class Notice:
    source_id: str          # 수집 출처 ID (sources.yaml 의 id)
    source_name: str        # 출처 표시 이름 (예: "나라장터", "EBS 외주제작 공모")
    category: str           # 분류 (예: "나라장터", "방송사 입찰", "외주제작·공모")
    title: str
    url: str
    uid: str = ""           # 출처 내 고유값 (공고번호 또는 URL). 비우면 title+posted 로 생성
    org: str = ""           # 공고기관 / 수요기관
    posted: str = ""        # 게시일 YYYY-MM-DD
    deadline: str = ""      # 마감일시 YYYY-MM-DD HH:MM (없을 수 있음)
    amount: int | None = None   # 추정가격/예산 (원)
    kind: str = ""          # 세부 구분 (용역/물품/공사 등)
    files: list[dict] = field(default_factory=list)    # 첨부서류 [{"name":..., "url":...}]
    info: dict = field(default_factory=dict)           # 한눈에 보기 항목 {"계약방법": "...", ...}
    matched: list[str] = field(default_factory=list)   # 매칭된 관심 키워드
    exclude_reason: str = ""  # 이 사유가 있으면 대시보드에 올리지 않음 (예: 학교장터 공급업체 지정)
    first_seen: str = ""    # 처음 수집된 시각

    @property
    def id(self) -> str:
        key = self.uid or f"{self.title}|{self.posted}"
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        return f"{self.source_id}:{digest}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["id"] = self.id
        d.pop("exclude_reason", None)
        return d
