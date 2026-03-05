# binance-futures-bot

기술적 분석 기반 Binance USDT-M 선물 자동 매매 봇. AI 불필요 — 순수 테크니컬 분석.

## Tech Stack

- **Python** 3.11+ / **Build**: setuptools (pyproject.toml)
- **ccxt**: Binance 거래소 API
- **pandas / pandas-ta**: 데이터 분석 + 기술 지표
- **APScheduler**: 스캔/매매 스케줄링
- **websockets**: 실시간 가격 스트림
- **aiosqlite**: 비동기 SQLite (거래 기록)
- **httpx**: HTTP 클라이언트
- **Docker**: 컨테이너 배포

## Architecture

```
APScheduler (주기적 스캔)
  -> scanner/ (8개 필터로 코인 스캔)
    -> indicators/ (14개 기술 지표 계산)
      -> strategy/ (분석 + 오케스트레이션)
        -> risk/ (10-gate 리스크 관리 + 레버리지 계산)
          -> trading/ (페이퍼/실전 매매 + 포지션 모니터링)
            -> db/ (거래 기록)
            -> notifications/ (Discord 웹훅 알림)
```

## Directory Structure

```
config/
  settings.py        # 전역 설정 (지표, 리스크, 레버리지, 스케줄)
src/
  main.py            # CLI 엔트리포인트 (futuresbot 커맨드)
  clients/           # Binance REST & WebSocket 클라이언트
  scanner/           # 코인 스캐너 (8개 필터)
  indicators/        # 14개 기술 지표 (RSI, MACD, BB 등)
  strategy/          # 분석기, 오케스트레이터
  risk/              # 리스크 매니저 (10-gate), 레버리지 계산기
  trading/           # 페이퍼 트레이더, 주문 실행, 포지션 모니터
  db/models.py       # 8개 테이블 + CRUD
  notifications/     # Discord 웹훅
tests/               # 49개 단위 테스트
Dockerfile
docker-compose.yml
```

## Critical Rules

### 1. Code Organization

- 도메인별 모듈 분리 (scanner → indicators → strategy → risk → trading)
- 파이프라인 순서 유지: 스캔 → 분석 → 리스크 → 매매
- Type hints 필수

### 2. Code Style

- `print()` 대신 `logging` 모듈 사용
- 설정은 config/settings.py 중앙 관리
- 매직 넘버 금지 - settings에 상수 정의

### 3. Design Decisions

- **결정론적 매매**: 동일 시장 데이터 → 동일 시그널
- **10-gate 리스크**: 모든 게이트 통과해야 매매 실행
- **페이퍼 트레이딩 우선**: 실전 매매 전 반드시 페이퍼 테스트

### 4. Testing

- `python -m pytest tests/` 로 실행 (49개 테스트)
- 새 지표/전략 추가 시 반드시 테스트 작성
- 거래소 API는 mock 처리

### 5. Security

- .env 파일 커밋 금지
- Binance API Key/Secret 환경변수 관리
- Discord 웹훅 URL 환경변수 관리
- 실전 매매 전 페이퍼 모드 검증 필수

## Running

```bash
pip install -e .
python -m pytest tests/
futuresbot            # CLI 실행
docker compose up -d  # Docker 배포
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
