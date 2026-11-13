from . import board, datagokr, g2b, kepco, s2b, search

COLLECTORS = {
    "g2b": g2b.collect,          # 나라장터 발주계획 / 사전규격 / 입찰공고
    "datagokr": datagokr.collect,  # 공공데이터포털 기관별 입찰공고 API (마사회, 수자원공사 등)
    "kepco": kepco.collect,      # 한국전력·발전 자회사 전자입찰 (전력데이터 개방포털)
    "s2b": s2b.collect,          # 학교장터 견적요청/소액수의공고
    "naver": search.collect_naver,   # 네이버 카페·블로그·뉴스 검색
    "gnews": search.collect_gnews,   # 구글 뉴스 RSS
    "board": board.collect,      # 임의 게시판 (URL 지정)
}
