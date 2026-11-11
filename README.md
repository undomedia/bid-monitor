# 🎬 영상제작 공고 모니터

영상제작 관련 **나라장터 발주계획·사전규격·입찰공고**, **학교장터 견적요청**, 입찰 밖에서 올라오는 **영상 제작 의뢰·공모 글**을
하루 3번 자동으로 모아 웹 대시보드에 보여주고, 새 공고는 텔레그램이나 이메일로 알려줍니다.

- 서버가 필요 없습니다. GitHub Actions가 자동으로 수집하고, 결과는 GitHub Pages로 무료 공개됩니다.
- 나라장터는 조달청 **공식 Open API**를 사용합니다. 크제비 같은 사설 사이트와 같은 원천 데이터입니다.
- 나라장터는 **용역**만 수집합니다. 그 위에 키워드 필터를 한 번 더 걸어 영상 장비 구매·설치, CCTV 같은 공고는 제외합니다.
- **마감이 지난 공고는 기본으로 숨깁니다.**

## 화면 구성

| 탭 | 내용 |
|---|---|
| 입찰공고 | 지금 입찰 가능한 나라장터 공고 (마감 지난 것은 숨김) |
| 사전규격 | 곧 입찰로 나올 사업. 제안요청서(안)로 미리 준비 |
| 발주계획 | 기관이 발주할 예정인 사업과 예산, 발주 시기 |
| 학교장터 | 학교·교육청 소액 견적요청 (학교 홍보영상, 행사 촬영 등). 키 필요 없음 |
| 제작 의뢰·공모 | 네이버 카페·블로그·뉴스, 구글 뉴스에서 찾은 영상 제작 의뢰·업체 모집·공모 글 |
| ★ 관심 | 별표 표시한 공고만 모아 보기 |

공고를 펼치면 **주요내용**(금액, 계약방법, 일정, 담당자), **첨부서류 다운로드**, **AI 제안요청서 분석**
(배점표 정량·정성, 실적 평가기준, 용역기간, 참가자격, 일정, 과업내용)이 나옵니다.

## 설치 (처음 한 번, 약 20분)

### 1. 나라장터 API 키 받기 (필수, 무료)
1. [공공데이터포털](https://www.data.go.kr)에 회원가입합니다.
2. 아래 3개 API에서 각각 **활용신청**을 누릅니다. 보통 바로 자동 승인됩니다.
   - 조달청_나라장터 입찰공고정보서비스
   - 조달청_나라장터 사전규격정보서비스
   - 조달청_나라장터 발주계획현황서비스
3. 마이페이지에서 **일반 인증키(Decoding)**를 복사합니다. 세 API 모두 같은 키를 씁니다.

### 2. 네이버 검색 API 키 (선택: 제작 의뢰 탭)
[네이버 개발자센터](https://developers.naver.com/apps)에서 애플리케이션을 등록하고 **검색** API를 선택한 뒤
Client ID와 Secret을 복사합니다. 키가 없으면 구글 뉴스만 수집합니다.

### 3. 제안요청서 AI 분석 키 (선택이지만 추천)
[Anthropic Console](https://console.anthropic.com)에 가입하고 결제수단을 등록한 뒤 **API Keys**에서 키를 만듭니다.
공고 1건을 분석하는 데 보통 수백 원이 들고, 서류 양에 따라 달라집니다. 하루 분석 건수 상한은
`config/settings.yaml`의 `analysis.max_per_run`으로 정합니다. 처음 정한 값은 한 번 실행에 10건입니다.

### 3-1. 텔레그램 알림 (선택)
1. 텔레그램에서 `@BotFather`에게 `/newbot`을 보내 봇을 만들고 **토큰**을 받습니다.
2. 만든 봇에게 아무 메시지나 보냅니다.
3. 브라우저에서 `https://api.telegram.org/bot<토큰>/getUpdates`를 열고 `"chat":{"id": 숫자`에 있는 숫자를 복사합니다. 이 숫자가 **CHAT_ID**입니다.

### 4. GitHub에 올리기
1. GitHub에서 새 저장소를 만들고 이 폴더를 올립니다.
2. 저장소 **Settings → Secrets and variables → Actions → New repository secret**에 아래 값을 등록합니다.

   | 이름 | 값 |
   |---|---|
   | `G2B_SERVICE_KEY` | 공공데이터포털 인증키 |
   | `ANTHROPIC_API_KEY` | (선택) 제안요청서 AI 분석 |
   | `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` | (선택) 네이버 |
   | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | (선택) 텔레그램 |
   | `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_TO` | (선택) 이메일. Gmail은 `smtp.gmail.com` + 앱 비밀번호 |

3. **Settings → Pages**에서 Source를 `Deploy from a branch`, Branch를 `main`의 `/docs`로 지정합니다.
4. **Actions** 탭 → `공고 수집` → **Run workflow**를 누르고, days에 `60`을 입력해 최근 두 달 치를 채웁니다.
5. 몇 분 뒤 `https://<아이디>.github.io/<저장소명>/`에서 대시보드를 볼 수 있습니다.

이후에는 매일 07:10, 12:10, 17:40(한국시간)에 자동으로 수집하고 새 공고를 알려줍니다.
처음 수집할 때는 기존 공고가 한꺼번에 알림으로 쏟아지지 않도록 알림을 생략합니다.

> 무료 GitHub Pages는 **공개 페이지**입니다. 공개 공고만 담기 때문에 문제는 없지만, 별표나 숨김 같은 개인 표시는 각자 브라우저에만 저장됩니다.

## 내 PC에서 실행하기

```bash
pip install -r requirements.txt
export G2B_SERVICE_KEY=발급받은키        # Windows: set G2B_SERVICE_KEY=...
python -m monitor run --days 30
```
결과는 `docs/index.html`에 생깁니다. 브라우저에서 바로 열면 data.json을 읽지 못할 수 있으니
`python -m http.server -d docs` 실행 후 http://localhost:8000 으로 여세요.

## 키워드 조정

`config/settings.yaml`
- `keywords`: 제목에 이 중 하나가 있으면 수집합니다 (영상, 촬영, 다큐, 유튜브, 숏폼 …).
- `exclude_keywords`: 제목에 있으면 제외합니다 (CCTV, 장비, 구매, 설치, 유지보수 …).

`config/sources.yaml`
- 네이버/뉴스 검색어(`queries`)와 업체 홍보글 제외어(`exclude`)
- 특정 게시판 추가: 먼저 `python -m monitor probe <게시판 URL>`로 글이 제대로 인식되는지 확인한 뒤 `type: board`로 등록합니다.

## 문제 해결
- 대시보드 위에 **⚠ 수집 실패**가 보이면 그 출처의 오류 내용을 확인하세요.
  - `SERVICE KEY IS NOT REGISTERED`: 활용신청 직후에는 키가 반영되기까지 1~2시간 걸릴 수 있습니다.
  - API 주소가 바뀐 경우: `sources.yaml`에서 해당 소스에 `base_url`과 `operation`을 지정합니다.
    (공공데이터포털 API 상세 페이지의 "요청주소"를 확인하세요.)

## 개발
```bash
pip install pytest && python -m pytest -q
```
