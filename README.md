# Binance Futures Trading Bot

기술적 분석 기반 바이낸스 USDT-M 선물 자동매매 봇

## 개요

AI 없이 순수 기술적 지표 알고리즘만으로 동작하는 바이낸스 선물 자동매매 봇이다. 별도 AI 구독이 필요 없으며, 코인 탐색부터 매매 신호 생성, 리스크 관리, 주문 실행, 포지션 모니터링까지 전 과정을 자동화한다.

## 주요 기능

- **기술적 분석**: RSI, MACD, 볼린저밴드, EMA(9/21/200), ATR, ADX, 스토캐스틱 — 8개 지표 가중 투표 방식
- **동적 코인 탐색**: 24시간 거래량/변동성/스프레드/펀딩비 기준으로 매매 후보 자동 선정
- **동적 레버리지**: 변동성 티어 × 신호 강도 × (1 - 드로다운) 기반 2~8배 자동 조절
- **10-Gate 리스크 관리**: 신호 강도, 포지션 제한, 일일 손실, 드로다운, 마진, 노출, 레버리지, 청산 버퍼, 펀딩비 순차 검증
- **Paper / Live 모드**: 환경변수 하나로 모의거래/실거래 전환
- **멀티 타임프레임**: 1시간봉 기본 분석 + 15분봉, 4시간봉 보조 확인
- **포지션 모니터링**: 손절/익절/트레일링 스톱/청산 근접/펀딩비 과다/최대 보유시간 초과 시 자동 종료
- **Discord 알림**: 매매 체결 알림 + 일일 리포트
- **백테스트**: 과거 데이터 기반 전략 검증 도구
- **Docker 배포**: `docker compose up -d` 한 줄로 서버 배포

## 프로젝트 구조

```
binance-futures-bot/
├── config/
│   └── settings.py              # 전체 설정 (지표, 리스크, 레버리지, 스케줄)
├── data/                        # DB 파일 (gitignored)
├── scripts/
│   ├── scheduler.py             # APScheduler 데몬
│   └── backtest.py              # 백테스트 도구
├── src/
│   ├── main.py                  # CLI 진입점 (futuresbot 명령어)
│   ├── clients/
│   │   ├── binance_rest.py      # ccxt 기반 REST 클라이언트
│   │   └── binance_ws.py        # WebSocket 실시간 스트림
│   ├── scanner/
│   │   └── coin_scanner.py      # 거래량/변동성 기반 코인 탐색
│   ├── indicators/
│   │   ├── calculator.py        # pandas-ta 기술적 지표 계산
│   │   └── signals.py           # 지표 조합 → 매매 신호 생성
│   ├── strategy/
│   │   ├── analyzer.py          # 코인별 종합 분석 + MTF 확인
│   │   └── orchestrator.py      # Scan→Analyze→Risk→Execute 파이프라인
│   ├── risk/
│   │   ├── risk_manager.py      # 10-gate 리스크 검증
│   │   └── leverage_calc.py     # 동적 레버리지 + 포지션 사이징
│   ├── trading/
│   │   ├── paper_trader.py      # 모의 거래
│   │   ├── order_executor.py    # 실거래 주문 (SL/TP 자동 설정)
│   │   └── position_monitor.py  # 포지션 모니터링 (7개 종료 조건)
│   ├── db/
│   │   └── models.py            # 8개 테이블 + CRUD (aiosqlite)
│   └── notifications/
│       └── notifier.py          # Discord 웹훅 알림
├── tests/                       # 42개 유닛 테스트
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
└── .env.example
```

## 빠른 시작

```bash
# 클론
git clone https://github.com/alton15/binance-futures-bot.git
cd binance-futures-bot

# 가상환경 설정
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 환경변수 설정
cp .env.example .env
# .env 파일에 API 키 입력
```

### CLI 명령어

```bash
# 파이프라인 실행
futuresbot run --paper           # Paper 모드 1회 실행
futuresbot run --paper --dry-run # 스캔/분석만 (매매 없음)
futuresbot run --paper --loop    # 스케줄러 데몬 시작 (60분 주기)
futuresbot run --live            # Live 모드 실행

# 개별 기능
futuresbot scan                  # 코인 스캔만 실행
futuresbot scan --limit 20       # 상위 20개 후보
futuresbot analyze BTCUSDT       # 특정 심볼 분석
futuresbot status                # 봇 상태 조회
futuresbot positions             # 오픈 포지션 상세
futuresbot history               # 최근 거래 내역
futuresbot history --limit 50    # 최근 50건

# 백테스트
futuresbot backtest BTCUSDT      # BTC 백테스트
futuresbot backtest ETHUSDT      # ETH 백테스트
```

## 환경변수 설정 (.env)

```env
# 바이낸스 API
BINANCE_API_KEY=your_api_key_here
BINANCE_API_SECRET=your_api_secret_here
BINANCE_TESTNET=true              # true: 테스트넷, false: 메인넷

# 거래 모드
TRADING_MODE=paper                # paper: 모의거래, live: 실거래

# 초기 자본 (USDT)
INITIAL_CAPITAL=100

# Discord 알림 (선택)
DISCORD_WEBHOOK_ALERTS=           # 매매 체결/종료 알림
DISCORD_WEBHOOK_REPORTS=          # 상태 업데이트 + 일일 리포트
```

## 핵심 아키텍처

### 파이프라인 흐름

```
[1단계] Scan ──→ [2단계] Analyze ──→ [3단계] Risk Check ──→ [4단계] Execute
   │                  │                    │                      │
   │             기술적 지표 계산        10-Gate 검증           Paper/Live
   │             8개 지표 가중 투표      레버리지 계산           주문 실행
   │             멀티 타임프레임 확인     포지션 사이징           SL/TP 설정
   │
 CoinScanner
 거래량 $50M+
 변동성 1.5%+
 스프레드 0.05% 이하
 펀딩비 0.1% 이하
```

### 매매 신호 생성 방식

8개 기술적 지표가 각각 LONG/SHORT/NEUTRAL로 투표하고, 가중치를 적용한 종합 점수로 방향과 강도를 결정한다.

| 지표 | 가중치 | 판단 기준 |
|------|--------|----------|
| MACD | 2.0 | 크로스오버 + 히스토그램 방향 |
| RSI (14) | 1.5 | 과매수(>70)/과매도(<30) + 중간선 위치 |
| EMA 추세 | 1.5 | 현재가 vs 200 EMA 위치 |
| 볼린저밴드 | 1.0 | 밴드 내 가격 위치 (상단/하단 접근) |
| EMA 교차 | 1.0 | 9/21 EMA 골든크로스/데드크로스 |
| 스토캐스틱 | 1.0 | K/D 크로스오버 + 과매수/과매도 |
| 거래량 | 1.0 | 평균 대비 거래량 확인 (1.5배 이상 시 추세 확인) |
| ADX | 0.5 | 추세 강도 (25 이상이면 강한 추세) |

**신호 통과 조건**: 확인 지표 3개 이상 AND 종합 강도 0.6 이상

**멀티 타임프레임 보정**:
- 15분봉 + 4시간봉 모두 동일 방향 → 강도 ×1.2
- 하나만 동일 방향 → 강도 ×1.1
- 모두 다른 방향 → 강도 ×0.8

### 10-Gate 리스크 관리

모든 매매 신호는 10단계 리스크 게이트를 순차적으로 통과해야 실행된다. 하나라도 실패하면 즉시 거부.

| Gate | 검증 항목 | 임계값 | 설명 |
|------|----------|--------|------|
| 1 | 신호 강도 | ≥ 0.6 | 가중 투표 종합 점수 |
| 2 | 오픈 포지션 수 | ≤ 5개 | 동시 포지션 제한 |
| 3 | 중복 심볼 | 없음 | 동일 코인 중복 진입 방지 |
| 4 | 일일 손실 | ≤ 자본의 5% | 당일 실현 손실 한도 |
| 5 | 최대 드로다운 | ≤ 피크 대비 15% | 누적 자본 감소 한도 |
| 6 | 가용 마진 | 필요 마진 이상 | 잔여 자본 확인 |
| 7 | 총 노출 | ≤ 자본의 50% | 전체 노출 금액 한도 |
| 8 | 레버리지 | 2~8배 | 허용 범위 내 검증 |
| 9 | 청산 버퍼 | ≥ 30% 거리 | 진입가~청산가 최소 거리 |
| 10 | 펀딩비 | ≤ 0.1% / 8시간 | 과도한 펀딩비 회피 |

### 동적 레버리지

변동성에 따라 최대 레버리지를 제한하고, 신호 강도와 드로다운을 반영하여 최종 레버리지를 결정한다.

| 일일 변동성 | 최대 레버리지 |
|------------|-------------|
| 0~2% | 8배 |
| 2~4% | 5배 |
| 4~6% | 3배 |
| 6%+ | 2배 |

**계산 공식**: `최종 레버리지 = 티어 최대값 × 신호 강도 × (1 - 현재 드로다운율)` → 2~8 클램프

### 포지션 사이징

고정 비율 리스크 모델을 사용한다.

- **리스크 금액** = 자본 × 2% (거래당)
- **손절 거리** = ATR × 1.5
- **익절 거리** = ATR × 3.0
- **포지션 크기** = 리스크 금액 / 손절 거리
- **필요 마진** = (포지션 크기 × 진입가) / 레버리지

### 포지션 모니터링 (7개 종료 조건)

스케줄러가 5분마다 모든 오픈 포지션을 검사하여 다음 조건 발생 시 자동 종료한다.

| 조건 | 설명 |
|------|------|
| 손절 (SL) | 가격이 SL 도달 |
| 익절 (TP) | 가격이 TP 도달 |
| 트레일링 스톱 | 고점/저점 대비 2% 역행 시 (수익 구간에서만) |
| 청산 근접 | 청산가까지 5% 이내 접근 |
| 펀딩비 과다 | 8시간 펀딩비 0.2% 초과 (포지션 불리한 방향) |
| 최대 보유 시간 | 72시간 초과 |

## 스케줄러 동작

`futuresbot run --paper --loop` 실행 시 APScheduler 데몬이 다음 작업을 자동 수행한다.

| 작업 | 주기 | 설명 |
|------|------|------|
| 코인 스캔 + 매매 | 60분 | 전체 파이프라인 실행 |
| 포지션 모니터링 | 5분 | 종료 조건 검사 |
| Discord 상태 업데이트 | 10분 | 현재 상태 리포트 |
| 일일 리포트 | 매일 23:00 | P&L 스냅샷 + 종합 리포트 |

## DB 스키마 (8개 테이블)

| 테이블 | 용도 |
|--------|------|
| `coins` | 스캔된 코인 메타데이터 (거래량, 변동성, 점수) |
| `signals` | 생성된 매매 신호 (방향, 강도, 지표 상세) |
| `trades` | 주문 실행 기록 (레버리지, 마진 포함) |
| `positions` | 포지션 상태 (청산가, 미실현손익, 펀딩비, SL/TP) |
| `orders` | SL/TP/트레일링 주문 관리 |
| `pnl_snapshots` | 일일 P&L 스냅샷 (피크 자본, 드로다운 추적) |
| `funding_payments` | 펀딩비 수금 기록 |
| `indicator_snapshots` | 지표 이력 (분석용) |

## Docker 배포

```bash
# 서버에서 실행
git clone https://github.com/alton15/binance-futures-bot.git
cd binance-futures-bot
cp .env.example .env   # API 키 설정
docker compose up -d   # 백그라운드 실행
docker compose logs -f # 로그 확인

# 중지/재시작
docker compose down
docker compose restart
```

`docker-compose.yml`은 `restart: unless-stopped`으로 설정되어 크래시 시 자동 재시작된다.

## 테스트

```bash
# 전체 테스트 (42개)
pytest tests/ -v

# 개별 모듈 테스트
pytest tests/test_db_models.py -v       # DB 모델 (11개)
pytest tests/test_indicators.py -v      # 기술적 지표 (5개)
pytest tests/test_signals.py -v         # 신호 생성 (6개)
pytest tests/test_coin_scanner.py -v    # 코인 스캐너 (4개)
pytest tests/test_leverage_calc.py -v   # 레버리지 계산 (11개)
pytest tests/test_risk_manager.py -v    # 리스크 관리 (5개)
```

## 기술 스택

| 구분 | 기술 | 용도 |
|------|------|------|
| 언어 | Python 3.11+ | async 전체 |
| 거래소 API | ccxt | 바이낸스 선물 REST API (타 거래소 확장 가능) |
| 기술적 지표 | pandas-ta | RSI, MACD, BB 등 (순수 Python, C 컴파일 불필요) |
| 데이터 처리 | pandas, numpy | OHLCV 데이터 가공 |
| 데이터베이스 | aiosqlite | 비동기 SQLite |
| 스케줄러 | APScheduler | 주기적 작업 실행 |
| 실시간 스트림 | websockets | 마크 가격, 북 티커 |
| HTTP 클라이언트 | httpx | Discord 웹훅 전송 |
| 환경변수 | python-dotenv | .env 파일 관리 |
| 컨테이너 | Docker | 서버 배포 |
