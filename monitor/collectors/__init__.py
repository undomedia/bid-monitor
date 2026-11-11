from . import board, g2b, s2b, search

COLLECTORS = {
    "g2b": g2b.collect,          # 나라장터 발주계획 / 사전규격 / 입찰공고
    "s2b": s2b.collect,          # 학교장터 견적요청/소액수의공고
    "naver": search.collect_naver,   # 네이버 카페·블로그·뉴스 검색
    "gnews": search.collect_gnews,   # 구글 뉴스 RSS
    "board": board.collect,      # 임의 게시판 (URL 지정)
}
