# binance-futures-bot

기술적 분석 기반 Binance USDT-M 선물 자동 매매 봇. AI 불필요 — 순수 테크니컬 분석.

## Tech Stack

- **Python** 3.11+ / **Build**: setuptools (pyproject.toml)
- **ccxt**: Binance 거래소 API
- **pandas / pandas-ta**: 데이터 분석 + 기술 지표
- **APScheduler**: 스캔/매매 스케줄링
- **websockets**: 실시간 가격 스트림 (miniTicker, markPrice, bookTicker)
- **aiosqlite**: 비동기 SQLite (거래 기록)
- **httpx**: HTTP 클라이언트
- **Docker**: 컨테이너 배포 (2서비스: bot + scalp)

## Architecture

```
[스윙] APScheduler (30분 주기 스캔)
  -> scanner/ (8개 필터로 코인 스캔)
    -> indicators/ (14개 기술 지표 계산)
      -> strategy/ (분석 + 오케스트레이션)
        -> risk/ (10-gate 리스크 관리 + 레버리지 계산)
          -> trading/ (페이퍼/실전 매매 + 5분 REST 모니터링)
            -> db/ (거래 기록)
            -> notifications/ (Discord 웹훅 알림)

[스캘핑] WebSocket 이벤트 드리븐
  -> scalping/watcher (볼륨 급등/가격 급변 실시간 감지)
    -> scalping/pipeline (3분봉 분석 → 리스크 → 매매)
      -> scalping/monitor (markPrice@1s 틱 모니터링)
```

## Directory Structure

```
config/
  settings.py        # 전역 설정 (지표, 리스크, 레버리지, 스케줄, 수수료)
  profiles.py        # 4개 트레이딩 프로필 (conservative/neutral/aggressive/scalp)
src/
  main.py            # CLI 엔트리포인트 (futuresbot 커맨드)
  clients/           # Binance REST & WebSocket 클라이언트
  scanner/           # 코인 스캐너 (8개 필터)
  indicators/        # 14개 기술 지표 (RSI, MACD, BB 등)
  strategy/          # 분석기, 오케스트레이터 (멀티 프로필 병렬 실행)
  risk/              # 리스크 매니저 (10-gate), 레버리지 계산기 (수수료 모델링)
  trading/           # 페이퍼 트레이더, 주문 실행 (수수료 차감), 스윙 포지션 모니터
  scalping/          # 이벤트 드리븐 스캘핑 (watcher, pipeline, monitor)
  db/models.py       # 8개 테이블 + CRUD
  notifications/     # Discord 웹훅 (2채널, 멀티 프로필 비교 리포트)
tests/               # 138개 단위 테스트 (12개 파일)
Dockerfile
docker-compose.yml   # 2개 서비스 (bot + scalp)
```

## Critical Rules

### 1. Code Organization

- 도메인별 모듈 분리 (scanner → indicators → strategy → risk → trading)
- 파이프라인 순서 유지: 스캔 → 분석 → 리스크 → 매매
- Type hints 필수

### 2. Code Style

- `print()` 대신 `logging` 모듈 사용
- 설정은 config/settings.py 중앙 관리
- 프로필별 설정은 config/profiles.py (frozen dataclass)
- 매직 넘버 금지 - settings/profiles에 상수 정의

### 3. Design Decisions

- **결정론적 매매**: 동일 시장 데이터 → 동일 시그널
- **10-gate 리스크**: 모든 게이트 통과해야 매매 실행 (프로필별 임계값)
- **페이퍼 트레이딩 우선**: 실전 매매 전 반드시 페이퍼 테스트
- **수수료 모델링**: 포지션 사이징에 라운드트립 테이커 수수료 반영
- **동적 자본**: 일일 손실 한도 = 초기 자본 + 당일 수익 (복리 성장)
- **프로필 불변성**: ProfileConfig는 frozen dataclass

### 4. Testing

- `python -m pytest tests/` 로 실행 (138개 테스트)
- 새 지표/전략 추가 시 반드시 테스트 작성
- 거래소 API는 mock 처리

### 5. Security

- .env 파일 커밋 금지
- Binance API Key/Secret 환경변수 관리
- Discord 웹훅 URL 환경변수 관리
- 실전 매매 전 페이퍼 모드 검증 필수
- Live 모드 멀티 프로필 금지 (안전)

## Running

```bash
pip install -e .
python -m pytest tests/
futuresbot                          # 스윙 CLI 실행
futuresbot run --scalp --paper      # 스캘핑 실행
futuresbot run --paper --multi      # 멀티 프로필 실행
docker compose up -d                # Docker 배포 (bot + scalp)
```

## Available Commands

- `/plan` - 구현 계획 수립
- `/tdd` - 테스트 주도 개발
- `/code-review` - 코드 리뷰
- `/build-fix` - 빌드 에러 수정
- `/verify` - 구현 검증
- `/learn` - 세션에서 패턴 추출
- `/refactor-clean` - 데드코드 정리
- `/checkpoint` - 체크포인트 생성
- `/python-review` - Python 코드 리뷰

## Git Workflow

- Conventional commits: `feat:`, `fix:`, `refactor:`, `docs:`, `test:`
- main 직접 커밋 가능 (1인 프로젝트)
- 모든 테스트 통과 후 커밋

## Change Reporting Workflow

코드 작업 완료 후:
1. **작업 내용 리포트**: 변경 사항을 사용자에게 요약 보고
2. **README 반영**: 변경된 기능/구조를 README.md에 반영
3. **사용자 확인**: 사용자가 변경 내용 확인
4. **커밋 & 푸시**: 사용자 승인 후 커밋 및 푸시 실행
