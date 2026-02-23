# Binance Futures Trading Bot

기술적 분석 기반 바이낸스 USDT-M 선물 자동매매 봇

## 개요

AI 없이 순수 기술적 지표 알고리즘만으로 동작하는 바이낸스 선물 자동매매 봇이다. 별도 AI 구독이 필요 없으며, 코인 탐색부터 매매 신호 생성, 리스크 관리, 주문 실행, 포지션 모니터링까지 전 과정을 자동화한다. 추가 비용 없이 로컬 또는 클라우드 서버에서 운영 가능하다.

## 주요 기능

- **기술적 분석**: RSI, MACD, 볼린저밴드, EMA(9/21/200), ATR, ADX, 스토캐스틱 — 8개 지표 가중 투표 방식
- **양방향 매매**: LONG/SHORT 모두 진입 가능, 하락장에서도 수익 추구
- **동적 코인 탐색**: 553개+ USDT-M 선물 심볼에서 30분마다 최대 30개 후보 자동 선정
- **동적 레버리지**: 변동성 티어 × 신호 강도 × (1 - 드로다운) 기반 2~8배 자동 조절
- **10-Gate 리스크 관리**: 마진 기반 노출 제한, 일일 손실 한도, 드로다운 차단 등 순차 검증
- **포지션당 마진 상한**: 자본의 15%로 제한하여 대형 코인(BTC/ETH)도 진입 가능
- **Paper / Live 모드**: 환경변수 하나로 모의거래/실거래 전환
- **멀티 타임프레임**: 1시간봉 기본 분석 + 15분봉, 4시간봉 보조 확인
- **6가지 자동 종료**: 손절/익절/트레일링 스톱/청산 근접/펀딩비 과다/최대 보유시간
- **Discord 알림**: 매매 체결, 포지션 종료, 상태 업데이트, 일일 리포트
- **Isolated 마진**: 라이브 모드에서 포지션별 마진 격리 (한 포지션 청산돼도 다른 포지션 보호)
- **네트워크 안정성**: 재시도 로직 + 한국 환경 DNS 최적화 (aiodns 우회)
- **Docker 배포**: `docker compose up -d` 한 줄로 서버 배포

## 프로젝트 구조

```
binance-futures-bot/
├── config/
│   └── settings.py              # 전체 설정 (지표, 리스크, 레버리지, 스케줄)
├── data/                        # DB 파일 (gitignored)
├── scripts/
│   ├── scheduler.py             # APScheduler 데몬 (4개 반복 작업)
│   └── backtest.py              # 백테스트 도구
├── src/
│   ├── main.py                  # CLI 진입점 (futuresbot 명령어)
│   ├── clients/
│   │   ├── binance_rest.py      # ccxt binanceusdm 클라이언트 (재시도 로직 포함)
│   │   └── binance_ws.py        # WebSocket 실시간 스트림
│   ├── scanner/
│   │   └── coin_scanner.py      # 거래량/변동성 기반 코인 탐색 (8개 필터)
│   ├── indicators/
│   │   ├── calculator.py        # pandas-ta 기술적 지표 계산 (14개 지표)
│   │   └── signals.py           # 8개 지표 가중 투표 → 매매 신호 생성
│   ├── strategy/
│   │   ├── analyzer.py          # 코인별 종합 분석 + 멀티타임프레임 확인
│   │   └── orchestrator.py      # Scan → Analyze → Risk → Execute 파이프라인
│   ├── risk/
│   │   ├── risk_manager.py      # 10-gate 마진 기반 리스크 검증
│   │   └── leverage_calc.py     # 동적 레버리지 + 포지션 사이징 (마진 상한 포함)
│   ├── trading/
│   │   ├── paper_trader.py      # 모의 거래 (API 호출 없음)
│   │   ├── order_executor.py    # 실거래 주문 (SL/TP 거래소에 등록)
│   │   └── position_monitor.py  # 포지션 모니터링 (6개 종료 조건)
│   ├── db/
│   │   └── models.py            # 8개 테이블 + CRUD (aiosqlite)
│   └── notifications/
│       └── notifier.py          # Discord 웹훅 알림 (2채널)
├── tests/                       # 42개 유닛 테스트
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
└── .env.example
```

## 빠른 시작

### 요구사항

- Python 3.11+
- 바이낸스 API 키 (읽기 권한 필수, 선물 거래 권한은 라이브 모드 시 필요)
- Discord 웹훅 URL (선택)

### 설치

```bash
git clone https://github.com/alton15/binance-futures-bot.git
cd binance-futures-bot

# 가상환경 설정
python -m venv .venv
source .venv/bin/activate
pip install -e .

# 환경변수 설정
cp .env.example .env
# .env 파일에 API 키 입력
```

### 환경변수 (.env)

```env
# 바이낸스 API
BINANCE_API_KEY=your_api_key_here
BINANCE_API_SECRET=your_api_secret_here
BINANCE_TESTNET=false             # true: 테스트넷, false: 메인넷

# 거래 모드
TRADING_MODE=paper                # paper: 모의거래, live: 실거래

# 초기 자본 (USDT)
INITIAL_CAPITAL=100

# Discord 알림 (선택)
DISCORD_WEBHOOK_ALERTS=           # 매매 체결/종료 알림
DISCORD_WEBHOOK_REPORTS=          # 상태 업데이트 + 일일 리포트
```

### CLI 명령어

```bash
# 파이프라인 실행
futuresbot run --paper              # Paper 모드 1회 실행
futuresbot run --paper --dry-run    # 스캔/분석만 (매매 없음)
futuresbot run --paper --loop       # 스케줄러 데몬 시작 (30분 주기)
futuresbot run --live               # Live 모드 실행

# 개별 기능
futuresbot scan                     # 코인 스캔만 실행
futuresbot scan --limit 30          # 상위 30개 후보
futuresbot analyze BTCUSDT          # 특정 심볼 분석
futuresbot status                   # 봇 상태 조회
futuresbot positions                # 오픈 포지션 상세
futuresbot history                  # 최근 거래 내역 (20건)
futuresbot history --limit 50       # 최근 50건

# 백테스트
futuresbot backtest BTCUSDT
futuresbot backtest ETHUSDT --from 2024-01-01 --to 2024-02-01
```

### 백그라운드 실행 (macOS)

```bash
# 절전 방지 + 봇 실행
caffeinate -i futuresbot run --paper --loop
```

## 핵심 아키텍처

### 전체 파이프라인

```
┌──────────────────────────────────────────────────────────────────────┐
│                     전체 실행 흐름 (30분 주기)                         │
└──────────────────────────────────────────────────────────────────────┘

[1단계] SCAN ─────────────────────────────────────────────────────────
  │  553개+ USDT-M 선물 심볼 조회
  │  필터: 거래량 ≥$50M, 변동성 ≥1.5%, 스프레드 ≤0.05%, 펀딩비 ≤0.1%
  │  점수 = 거래량 × 변동성 × (1 - 스프레드) → 상위 30개 선정
  ↓
[2단계] ANALYZE (코인별) ─────────────────────────────────────────────
  │  1시간봉 250개 캔들 → 8개 기술 지표 계산 (로컬 CPU)
  │  가중 투표 시그널 생성 (LONG / SHORT / NEUTRAL)
  │  15분봉 + 4시간봉 멀티타임프레임 확인 → 강도 보정
  │  조건: 확인 지표 ≥3개 AND 강도 ≥0.6 → Actionable
  ↓
[3단계] RISK CHECK (10-Gate) ─────────────────────────────────────────
  │  시그널 강도 → 포지션 수 → 중복 → 일일 손실 → 드로다운
  │  → 가용 마진 → 총 노출(마진 기반) → 레버리지 → 청산 버퍼 → 펀딩비
  │  하나라도 실패 시 거래 거부
  ↓
[4단계] EXECUTE ──────────────────────────────────────────────────────
  │  Paper: DB에 기록만 (API 호출 없음)
  │  Live: Isolated 마진 설정 → 레버리지 설정 → 마켓 주문 → SL/TP 등록
  ↓
[백그라운드] MONITOR (5분 주기) ──────────────────────────────────────
  │  현재가 조회 → 미실현 P&L 계산 → 트레일링 고점/저점 갱신
  │  6가지 종료 조건 체크 → 해당 시 즉시 종료 + Discord 알림
  ↓
[백그라운드] REPORT ──────────────────────────────────────────────────
     상태 업데이트 (10분) → 일일 리포트 (23:00 UTC)
```

### 매매 신호 생성 (8개 지표 가중 투표)

8개 기술적 지표가 각각 LONG/SHORT/NEUTRAL로 투표하고, 가중치를 적용한 종합 점수로 방향과 강도를 결정한다.

| 지표 | 가중치 | LONG 조건 | SHORT 조건 |
|------|:------:|----------|-----------|
| MACD (12/26/9) | 2.0 | 불리시 크로스오버, 히스토그램 양수 | 베어리시 크로스오버, 히스토그램 음수 |
| RSI (14) | 1.5 | 과매도 (< 30), 중간선 아래 | 과매수 (> 70), 중간선 위 |
| EMA 200 추세 | 1.5 | 가격 > 200 EMA | 가격 < 200 EMA |
| 볼린저밴드 (20, 2σ) | 1.0 | 하단밴드 근처 | 상단밴드 근처 |
| EMA 교차 (9/21) | 1.0 | 골든크로스 (9 > 21) | 데드크로스 (9 < 21) |
| 스토캐스틱 (14, 3) | 1.0 | 과매도 크로스오버 | 과매수 크로스오버 |
| 거래량 (SMA 20) | 1.0 | 고거래량 + 상승 | 고거래량 + 하락 |
| ADX (14) | 0.5 | 강한 상승 추세 | 강한 하락 추세 |

**신호 결정**:
```
long_score > short_score → LONG
short_score > long_score → SHORT
동점 → NEUTRAL (진입 안 함)
강도 = 승리 진영 점수 / 전체 가중치 합
```

**통과 조건**: 확인 지표 ≥ 3개 AND 강도 ≥ 0.6

**멀티 타임프레임 보정**:
- 15분봉 + 4시간봉 모두 동일 방향 → 강도 × 1.2
- 하나만 동일 방향 → 강도 × 1.1
- 모두 다른 방향 → 강도 × 0.8

### 10-Gate 리스크 관리

모든 매매 신호는 10단계 리스크 게이트를 순차적으로 통과해야 실행된다. 하나라도 실패하면 즉시 거부.

| Gate | 검증 항목 | 임계값 | 설명 |
|:----:|----------|:------:|------|
| 1 | 신호 강도 | ≥ 0.6 | 가중 투표 종합 점수 |
| 2 | 오픈 포지션 수 | ≤ 7개 | 동시 포지션 제한 |
| 3 | 중복 심볼 | 없음 | 동일 코인 중복 진입 방지 |
| 4 | 일일 손실 | ≤ 자본의 8% | 당일 실현 손실 한도 |
| 5 | 최대 드로다운 | ≤ 피크 대비 25% | 누적 자본 감소 한도 |
| 6 | 가용 마진 | 필요 마진 이상 | 잔여 자본 확인 |
| 7 | 총 마진 노출 | ≤ 자본의 70% (soft) / 75% (hard) | 마진 기반 노출 제한, 잔여 5% 미만 시 스킵 |
| 8 | 레버리지 | 2~8배 | 허용 범위 내 검증 |
| 9 | 청산 버퍼 | ≥ 30% 거리 | 진입가~청산가 최소 거리 |
| 10 | 펀딩비 | ≤ 0.1% / 8시간 | 과도한 펀딩비 회피 |

### 동적 레버리지

변동성에 따라 최대 레버리지를 제한하고, 신호 강도와 드로다운을 반영하여 최종 레버리지를 결정한다.

| 일일 변동성 | 최대 레버리지 |
|:----------:|:-----------:|
| 0~2% | 8배 |
| 2~4% | 5배 |
| 4~6% | 3배 |
| 6%+ | 2배 |

**공식**: `최종 레버리지 = 티어 최대값 × 신호 강도 × (1 - 드로다운율)` → [2, 8] 범위 클램프

### 포지션 사이징

고정 비율 리스크 모델 + 마진 상한:

```
리스크 금액 = 자본 × 3%
SL 거리    = ATR × 1.5
TP 거리    = ATR × 3.0   (R:R = 1:2)
포지션 크기 = 리스크 금액 / SL 거리
notional   = 포지션 크기 × 진입가
마진       = notional / 레버리지
마진 상한  = 자본 × 15%  (초과 시 포지션 크기 축소)
```

마진 상한이 있어서 BTC, ETH 같은 대형 코인도 소액 자본으로 진입 가능하다.

### 포지션 모니터링 (6가지 자동 종료)

스케줄러가 5분마다 모든 오픈 포지션을 검사하여 다음 조건 발생 시 자동 종료한다.

| 조건 | 기준 | 설명 |
|------|:----:|------|
| 손절 (SL) | ATR × 1.5 | 가격이 SL 도달 시 즉시 종료 |
| 익절 (TP) | ATR × 3.0 | 가격이 TP 도달 시 즉시 종료 |
| 트레일링 스톱 | 고점 -2% | 수익 구간에서 고점 대비 2% 역행 시 수익 확정 |
| 청산 근접 | 5% 이내 | 청산가까지 5% 이내 접근 시 선제 종료 |
| 펀딩비 과다 | 0.2% 초과 | 포지션 불리한 방향 펀딩비 시 종료 |
| 최대 보유시간 | 72시간 | 3일 초과 시 무조건 종료 |

**Paper vs Live 종료 방식**:
- Paper: DB의 SL/TP 가격과 현재가를 5분마다 비교하여 종료
- Live: 거래소에 SL/TP 주문이 실제 등록됨 (봇 꺼져도 거래소가 체결) + 모니터가 트레일링/펀딩비/시간 추가 체크

## 투자 성향 (현재: 중립)

### 리스크 프로필 ($100 기준)

| 항목 | 값 | 금액 |
|------|:---:|:----:|
| 건당 리스크 | 3% | SL 시 -$3 |
| 건당 기대 수익 | R:R 1:2 | TP 시 +$6 |
| 포지션당 마진 상한 | 15% | 최대 $15/건 |
| 동시 포지션 | 7개 | 평균 $10/건 × 7 |
| 총 마진 노출 | 70~75% | $70~75 |
| 일일 손실 한도 | 8% | 하루 최대 -$8 |
| 최대 드로다운 | 25% | 고점 대비 -$25이면 중단 |

### 시나리오

```
최악의 하루:  7건 전부 SL → 하지만 일일 한도 -$8에서 자동 중단
보통 하루:    승률 40%, 8건 → (3승 × $6) - (5패 × $3) = +$3
좋은 하루:    승률 60%, 8건 → (5승 × $6) - (3패 × $3) = +$21
```

### 투자 성향 조절 (`config/settings.py`)

| 항목 | 보수적 | 중립 (현재) | 공격적 |
|------|:-----:|:---------:|:-----:|
| `risk_per_trade_pct` | 2% | **3%** | 5% |
| `max_open_positions` | 5 | **7** | 10 |
| `max_exposure_pct` | 50% | **70%** | 90% |
| `daily_loss_limit_pct` | 5% | **8%** | 10% |
| `max_drawdown_pct` | 15% | **25%** | 35% |

## 스케줄러 동작

`futuresbot run --paper --loop` 실행 시 APScheduler 데몬이 자동 수행:

| 작업 | 주기 | 설명 |
|------|:----:|------|
| 코인 스캔 + 매매 | 30분 | 전체 파이프라인 실행 (최대 3건 거래/사이클) |
| 포지션 모니터링 | 5분 | 6가지 종료 조건 검사 |
| Discord 상태 업데이트 | 10분 | Wallet, Available, 마진, P&L 리포트 |
| 일일 리포트 | 매일 23:00 UTC | 종합 P&L + 리스크 + 최근 거래 |

## Discord 알림

2개 웹훅 채널로 분리:

### #alerts 채널 (`DISCORD_WEBHOOK_ALERTS`)

- **거래 체결**: 심볼, 방향(LONG/SHORT), 레버리지, 진입가, 사이즈, SL/TP
- **포지션 종료**: 심볼, 방향, P&L, 종료 사유 (WIN/LOSS 표시)

### #reports 채널 (`DISCORD_WEBHOOK_REPORTS`)

- **상태 업데이트 (10분)**: Wallet(전체 자산), Available(사용 가능 현금), 마진 사용량, 실현/미실현 P&L, 승률, 오픈 포지션 목록
- **일일 리포트 (23:00 UTC)**: 4개 임베드
  1. Overview — 자본, 총 P&L, 오늘 P&L, 승률 바
  2. P&L Breakdown — 총 수익/손실, 최고/최악 거래, 펀딩비
  3. Risk — 일일 손실 한도 사용률, 마진/노출 현황
  4. Recent Trades — 최근 거래 목록 + P&L

## DB 스키마 (8개 테이블)

| 테이블 | 용도 |
|--------|------|
| `coins` | 스캔된 코인 메타데이터 (거래량, 변동성, 점수) |
| `signals` | 생성된 매매 신호 (방향, 강도, 지표 상세) |
| `trades` | 주문 실행 기록 (진입가, 레버리지, 마진) |
| `positions` | 포지션 상태 (SL/TP, 청산가, 미실현P&L, 트레일링) |
| `orders` | SL/TP 주문 관리 (거래소 주문 ID 포함) |
| `pnl_snapshots` | 일일 P&L 스냅샷 (피크 자본, 드로다운 추적) |
| `funding_payments` | 펀딩비 수금/지급 기록 |
| `indicator_snapshots` | 기술 지표 이력 (분석용) |

## Docker 배포

```bash
git clone https://github.com/alton15/binance-futures-bot.git
cd binance-futures-bot
cp .env.example .env   # API 키 설정
docker compose up -d   # 백그라운드 실행
docker compose logs -f # 로그 확인
docker compose down    # 중지
docker compose restart # 재시작
```

`restart: unless-stopped`으로 크래시 시 자동 재시작. `./data:/app/data` 볼륨으로 DB 영속성 보장.

## 클라우드 배포 (Oracle Cloud)

Oracle Cloud Always Free 티어로 평생 무료 운영 가능:

| 항목 | 무료 스펙 |
|------|----------|
| CPU | ARM 4코어 |
| RAM | 24GB |
| 스토리지 | 200GB |
| 네트워크 | 월 10TB |

Shape: `VM.Standard.A1.Flex` (ARM), Image: Ubuntu 22.04

## 테스트

```bash
# 전체 테스트 (42개)
pytest tests/ -v

# 개별 모듈
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
| 언어 | Python 3.11+ | async/await 전체 |
| 거래소 API | ccxt (binanceusdm) | 바이낸스 USDT-M 선물 전용 |
| 기술적 지표 | pandas-ta | RSI, MACD, BB 등 (순수 Python) |
| 데이터 처리 | pandas, numpy | OHLCV 데이터 가공 |
| 데이터베이스 | aiosqlite | 비동기 SQLite |
| 스케줄러 | APScheduler | 주기적 작업 실행 |
| 실시간 스트림 | websockets | 마크 가격, 북 티커 |
| HTTP 클라이언트 | httpx | Discord 웹훅 전송 |
| 환경변수 | python-dotenv | .env 파일 관리 |
| 컨테이너 | Docker | 서버 배포 |

## 운영 비용

| 항목 | 비용 |
|------|:----:|
| 바이낸스 API (시세 조회) | 무료 |
| pandas-ta 지표 계산 | 무료 |
| SQLite DB | 무료 |
| Discord 웹훅 | 무료 |
| Oracle Cloud 서버 | 무료 (Always Free) |
| **실거래 시 거래 수수료** | **0.02~0.04%/건** |
