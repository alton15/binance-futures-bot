한국어 | [**English**](README.md)

# Binance Futures Trading Bot

기술적 분석 기반 바이낸스 USDT-M 선물 자동매매 봇

> **Disclaimer / 면책 조항**
>
> This software is for **educational and informational purposes only**. It is not financial advice. Use at your own risk. The author is not responsible for any financial losses incurred from using this software. Past performance does not guarantee future results. Always do your own research before trading.
>
> 이 소프트웨어는 **교육 및 정보 제공 목적**으로만 제공됩니다. 투자 조언이 아니며, 사용에 따른 모든 책임은 사용자에게 있습니다. 이 소프트웨어 사용으로 인한 금전적 손실에 대해 저자는 책임지지 않습니다.

## 개요

AI 없이 순수 기술적 지표 알고리즘만으로 동작하는 바이낸스 선물 자동매매 봇이다. 별도 AI 구독이 필요 없으며, 코인 탐색부터 매매 신호 생성, 리스크 관리, 주문 실행, 포지션 모니터링까지 전 과정을 자동화한다. 추가 비용 없이 로컬 또는 클라우드 서버에서 운영 가능하다.

## 주요 기능

- **기술적 분석**: RSI, MACD, 볼린저밴드, EMA(9/21/200), ATR, ADX, 스토캐스틱 — 8개 지표 가중 투표 + NEUTRAL 데드존 필터링 + 프로필별 시그널 품질 필터
- **양방향 매매**: LONG/SHORT 모두 진입 가능, 하락장에서도 수익 추구
- **4개 트레이딩 프로필**: Conservative / Neutral / Aggressive / Scalp — 성향별 리스크·레버리지 자동 조절
- **멀티 프로필 병렬 실행**: Paper 모드에서 3개 프로필 동시 비교 운용
- **이벤트 드리븐 스캘핑**: WebSocket 실시간 볼륨 급등/가격 급변 감지 → 3분봉 초단타 매매
- **동적 코인 탐색**: 553개+ USDT-M 선물 심볼에서 30분마다 최대 30개 후보 자동 선정
- **동적 레버리지**: 변동성 티어 × 신호 강도 × (1 - 드로다운) 기반, 프로필별 1~15배 자동 조절
- **수수료 모델링**: 포지션 사이징에 라운드트립 테이커 수수료(0.08%) 반영
- **10-Gate 리스크 관리**: 마진 기반 노출 제한, 일일 손실 한도, 드로다운 차단 등 순차 검증
- **시그널 품질 필터**: MACD 반대·저볼륨·BB 충돌 감지 → 프로필별 강도 감쇄/거부
- **ATR 기반 트레일링 스톱**: ATR × activation 수익 도달 후 활성화, ATR × multiplier 역행 시 수익 확정
- **포지션당 마진 상한**: 자본의 10~15%로 제한하여 대형 코인(BTC/ETH)도 진입 가능
- **Paper / Live 모드**: 환경변수 하나로 모의거래/실거래 전환
- **멀티 타임프레임**: 1시간봉 기본 분석 + 15분봉, 4시간봉 보조 확인 (스캘핑: 3분봉 + 1분봉, 5분봉 소프트 확인)
- **7가지 자동 종료**: 손절/익절/ATR 기반 트레일링 스톱/신호 역전/청산 근접/펀딩비 과다/최대 보유시간
- **Discord 알림**: 매매 체결, 포지션 종료, 상태 업데이트, 일일 리포트, 멀티 프로필 비교
- **Isolated 마진**: 라이브 모드에서 포지션별 마진 격리 (한 포지션 청산돼도 다른 포지션 보호)
- **네트워크 안정성**: 3x 지수 백오프 재시도 + WebSocket 자동 재연결 (30초 하트비트)
- **Docker 배포**: `docker compose up -d`로 스윙 + 스캘핑 2개 서비스 동시 배포

## 프로젝트 구조

```
binance-futures-bot/
├── config/
│   ├── settings.py              # 전역 설정 (지표, 리스크, 레버리지, 스케줄)
│   └── profiles.py              # 4개 트레이딩 프로필 (frozen dataclass)
├── data/                        # DB 파일 (gitignored)
├── scripts/
│   ├── scheduler.py             # APScheduler 데몬 (4개 반복 작업)
│   └── backtest.py              # 백테스트 도구
├── src/
│   ├── main.py                  # CLI 진입점 (futuresbot 명령어)
│   ├── clients/
│   │   ├── binance_rest.py      # ccxt binanceusdm 클라이언트 (3x 지수 백오프)
│   │   └── binance_ws.py        # WebSocket 실시간 스트림 (자동 재연결)
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
│   │   └── leverage_calc.py     # 동적 레버리지 + 포지션 사이징 (수수료 모델링)
│   ├── trading/
│   │   ├── paper_trader.py      # 모의 거래 (API 호출 없음)
│   │   ├── order_executor.py    # 실거래 주문 (SL/TP 거래소에 등록, 수수료 차감)
│   │   └── position_monitor.py  # 스윙 포지션 모니터링 (5분 REST 폴링, 7개 종료 조건)
│   ├── scalping/
│   │   ├── watcher.py           # WebSocket 볼륨 급등/가격 급변 실시간 감지
│   │   ├── pipeline.py          # 스파이크 → 분석 → 리스크 → 매매 파이프라인
│   │   └── monitor.py           # 1초 WebSocket 틱 모니터링 (초단타 종료)
│   ├── db/
│   │   └── models.py            # 8개 테이블 + CRUD (aiosqlite)
│   └── notifications/
│       └── notifier.py          # Discord 웹훅 알림 (2채널)
├── tests/                       # 169개 유닛 테스트 (12개 파일)
├── Dockerfile
├── docker-compose.yml           # 2개 서비스 (bot + scalp)
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
# 스윙 트레이딩
futuresbot run --paper              # Paper 모드 1회 실행 (neutral 프로필)
futuresbot run --paper --dry-run    # 스캔/분석만 (매매 없음)
futuresbot run --paper --loop       # 스케줄러 데몬 시작 (30분 주기)
futuresbot run --live               # Live 모드 실행

# 프로필 선택
futuresbot run --paper --profile conservative   # 보수적
futuresbot run --paper --profile aggressive     # 공격적
futuresbot run --paper --multi                  # 3개 프로필 병렬 실행

# 이벤트 드리븐 스캘핑
futuresbot run --scalp --paper      # WebSocket 기반 스캘핑 시작

# 개별 기능
futuresbot scan                     # 코인 스캔만 실행
futuresbot scan --limit 30          # 상위 30개 후보
futuresbot analyze BTCUSDT          # 특정 심볼 분석
futuresbot status                   # 봇 상태 조회
futuresbot status --profile scalp   # 특정 프로필 상태
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

### 스윙 트레이딩 파이프라인 (30분 주기)

```
┌──────────────────────────────────────────────────────────────────────┐
│                     스윙 실행 흐름 (30분 주기)                          │
└──────────────────────────────────────────────────────────────────────┘

[1단계] SCAN ─────────────────────────────────────────────────────────
  │  553개+ USDT-M 선물 심볼 조회
  │  필터: 거래량 ≥$50M, 변동성 ≥1.5%, 스프레드 ≤0.05%, 펀딩비 ≤0.1%
  │  점수 = 거래량 × 변동성 × (1 - 스프레드) → 상위 30개 선정
  ↓
[2단계] ANALYZE (코인별) ─────────────────────────────────────────────
  │  1시간봉 250개 캔들 → 8개 기술 지표 계산 (로컬 CPU)
  │  가중 투표 시그널 생성 (LONG / SHORT / NEUTRAL) + NEUTRAL 데드존 필터링
  │  15분봉 + 4시간봉 멀티타임프레임 확인 → 강도 보정
  │  시그널 품질 필터 (MACD/볼륨/BB) → 강도 보정
  │  조건: 확인 지표 ≥ 프로필별 최소 AND 강도 ≥ 프로필별 최소 AND MTF ≥1 (Scalp: 소프트) → Actionable
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
  │  7가지 종료 조건 체크 → 해당 시 즉시 종료 + Discord 알림
  ↓
[백그라운드] REPORT ──────────────────────────────────────────────────
     상태 업데이트 (30분) → 일일 리포트 (23:00 UTC)
```

### 이벤트 드리븐 스캘핑 파이프라인

```
┌──────────────────────────────────────────────────────────────────────┐
│                  스캘핑 실행 흐름 (WebSocket 이벤트)                     │
└──────────────────────────────────────────────────────────────────────┘

[실시간] DETECT (WebSocket !miniTicker@arr) ──────────────────────────
  │  전 심볼 1초 간격 스트리밍 → 5분 슬라이딩 윈도우 분석
  │  감지: 볼륨 급등 (15분 평균 ×2), 가격 급변 (±1.0%)
  │  + REST 핫코인 폴링 (3분) → 상위 등락/거래량 코인
  │  필터: $20M 최소 거래량, 60초 쿨다운, 중복 제거
  ↓
[분석] ANALYZE (3분봉 기반) ──────────────────────────────────────────
  │  3분봉 기본 + 1분봉/5분봉 멀티타임프레임 (소프트 — 패널티만, 하드 게이트 없음)
  │  동시 분석 최대 3건 (세마포어)
  ↓
[리스크] RISK CHECK (10-Gate, Scalp 프로필) ──────────────────────────
  │  스캘핑 전용: max 3 포지션, 60% 노출, 5~15배 레버리지
  ↓
[매매] EXECUTE ───────────────────────────────────────────────────────
  ↓
[모니터] TICK (markPrice@1s WebSocket) ──────────────────────────────
     포지션별 1초 틱 구독 → SL/TP/트레일링 매 틱 체크
     최대 보유시간 4시간
```

### 매매 신호 생성 (8개 지표 가중 투표)

8개 기술적 지표가 각각 LONG/SHORT/NEUTRAL로 투표하고, 가중치를 적용한 종합 점수로 방향과 강도를 결정한다.

| 지표 | 가중치 | LONG 조건 | SHORT 조건 | NEUTRAL 조건 |
|------|:------:|----------|-----------|-------------|
| MACD (12/26/9) | 2.0 | 불리시 크로스오버, 히스토그램 확대 양수 | 베어리시 크로스오버, 히스토그램 확대 음수 | 히스토그램 축소 중 |
| RSI (14) | 1.5 | 과매도 (< 30), 40 미만 | 과매수 (> 70), 60 초과 | 40~60 구간 |
| EMA 200 추세 | 1.5 | 가격 > 200 EMA (+1% 이상) | 가격 < 200 EMA (-1% 이상) | 200 EMA ±1% 이내 |
| 볼린저밴드 (20, 2σ) | 1.0 | 밴드 하위 20% 이하 | 밴드 상위 80% 이상 | 밴드 20~80% 구간 |
| EMA 교차 (9/21) | 1.0 | 골든크로스 or 스프레드 ≥0.3% | 데드크로스 or 스프레드 ≥0.3% | EMA 수렴 (스프레드 <0.3%) |
| 스토캐스틱 (14, 3) | 1.0 | K < 20 과매도 or 크로스업 | K > 80 과매수 or 크로스다운 | K 20~80 구간 |
| 거래량 (SMA 20) | 1.0 | 고거래량 + 200 EMA 위 | 고거래량 + 200 EMA 아래 | 저거래량 or 평균 |
| ADX (14) | 1.0 | ADX ≥ 20 + 상승 추세 | ADX ≥ 20 + 하락 추세 | ADX < 20 (약한 추세) |

**신호 결정**:
```
long_score > short_score → LONG
short_score > long_score → SHORT
동점 → NEUTRAL (진입 안 함)
강도 = 승리 진영 점수 / 전체 가중치 합 (총 가중치 10.0)
```

**통과 조건**: 확인 지표 ≥ 4개 (Conservative: ≥5, Scalp: ≥2) AND 강도 ≥ 프로필별 최소 AND MTF 확인 ≥ 1개 (Scalp: MTF 소프트 — 하드 게이트 없이 강도 패널티만)

**시그널 품질 필터** (프로필별 페널티):

| 필터 | Conservative | Neutral | Aggressive | Scalp |
|------|:-----------:|:-------:|:----------:|:-----:|
| MACD 반대 방향 | 거부 (×1.0) | -30% | -15% | -20% |
| 저볼륨 (avg 미만) | 거부 (0.5x) | -15% (0.5x) | 없음 | 없음 |
| BB 방향 충돌 | -20% | -10% | 없음 | 없음 |

- **거부**: 페널티 1.0 또는 임계값 이하 볼륨 시 시그널 즉시 거부
- **-N%**: 시그널 강도에서 N% 감쇄 후 최소 강도 기준 재검사

**멀티 타임프레임 보정**:
- 15분봉 + 4시간봉 모두 동일 방향 → 강도 × 1.15
- 하나만 동일 방향 → 보정 없음 (기본값)
- 모두 다른 방향 → 강도 × 0.5 + **진입 차단**

### 10-Gate 리스크 관리

모든 매매 신호는 10단계 리스크 게이트를 순차적으로 통과해야 실행된다. 하나라도 실패하면 즉시 거부. 각 게이트의 임계값은 프로필별로 다르다.

| Gate | 검증 항목 | Conservative | Neutral | Aggressive | Scalp |
|:----:|----------|:-----------:|:-------:|:----------:|:-----:|
| 1 | 신호 강도 | ≥ 0.70 | ≥ 0.65 | ≥ 0.60 | ≥ 0.40 |
| 2 | 오픈 포지션 수 | ≤ 3개 | ≤ 5개 | ≤ 5개 | ≤ 5개 |
| 3 | 중복 심볼 | 없음 | 없음 | 없음 | 없음 |
| 4 | 일일 손실 | ≤ 4% | ≤ 6% | ≤ 8% | ≤ 7% |
| 5 | 최대 드로다운 | ≤ 10% | ≤ 20% | ≤ 25% | ≤ 20% |
| 6 | 가용 마진 | 필요 마진 이상 | 〃 | 〃 | 〃 |
| 7 | 총 마진 노출 | ≤ 40% | ≤ 60% | ≤ 70% | ≤ 60% |
| 8 | 레버리지 | 1~3배 | 2~6배 | 3~10배 | 5~15배 |
| 9 | 청산 버퍼 | ≥ 30% | ≥ 20% | ≥ 15% | ≥ 15% |
| 10 | 펀딩비 | ≤ 0.1% | ≤ 0.1% | ≤ 0.1% | ≤ 0.1% |

Gate 4 일일 손실은 **동적 자본**(초기 자본 + 당일 수익)을 기준으로 계산하여 복리 성장을 지원한다.

### 동적 레버리지

변동성에 따라 최대 레버리지를 제한하고, 신호 강도와 드로다운을 반영하여 최종 레버리지를 결정한다. 각 프로필별로 변동성 티어와 레버리지 범위가 다르다.

**Neutral 프로필 변동성 티어 (기본)**:

| 일일 변동성 | 최대 레버리지 |
|:----------:|:-----------:|
| 0~2% | 6배 |
| 2~4% | 4배 |
| 4~6% | 3배 |
| 6%+ | 2배 |

**프로필별 레버리지 범위**:

| 프로필 | 최소 | 최대 | 저변동 티어 | 고변동 티어 |
|--------|:----:|:----:|:----------:|:----------:|
| Conservative | 1배 | 3배 | 3배 | 1배 |
| Neutral | 2배 | 6배 | 6배 | 2배 |
| Aggressive | 3배 | 10배 | 10배 | 3배 |
| Scalp | 5배 | 15배 | 15배 | 5배 |

**공식**: `최종 레버리지 = 티어 최대값 × 신호 강도 × (1 - 드로다운율)` → [min, max] 범위 클램프

### 포지션 사이징

고정 비율 리스크 모델 + 수수료 모델링 + 마진 상한:

```
리스크 금액 = 자본 × risk_per_trade_pct
수수료     = 테이커 라운드트립 (0.04% × 2 = 0.08%)
SL 거리    = ATR × sl_atr_multiplier
TP 거리    = ATR × tp_atr_multiplier   (R:R = 1:2, 기본 2.0/4.0)
포지션 크기 = 리스크 금액 / (SL 거리 + 수수료)
notional   = 포지션 크기 × 진입가
마진       = notional / 레버리지
마진 상한  = 자본 × 15%  (초과 시 포지션 크기 축소)
```

마진 상한이 있어서 BTC, ETH 같은 대형 코인도 소액 자본으로 진입 가능하다.

### 포지션 모니터링 (7가지 자동 종료)

**스윙**: 스케줄러가 5분마다 REST API로 모든 오픈 포지션 검사.
**스캘핑**: markPrice@1s WebSocket으로 매 초 틱 체크.

| 조건 | 기준 | 설명 |
|------|:----:|------|
| 손절 (SL) | ATR × SL 배수 | 가격이 SL 도달 시 즉시 종료 |
| 익절 (TP) | ATR × TP 배수 | 가격이 TP 도달 시 즉시 종료 |
| 트레일링 스톱 | ATR 기반 동적 | ATR × activation 수익 후 활성화, ATR × multiplier 역행 시 수익 확정 |
| 신호 역전 | 반대 방향 신호 | 기존 방향 반대 시그널 감지 시 종료 |
| 청산 근접 | 30% 이내 | 청산가까지 버퍼 부족 시 선제 종료 |
| 펀딩비 과다 | 0.2% 초과 | 포지션 불리한 방향 펀딩비 시 종료 |
| 최대 보유시간 | 프로필별 | Conservative 48h, Neutral/Aggressive 72h, Scalp 4h |

**Paper vs Live 종료 방식**:
- Paper: DB의 SL/TP 가격과 현재가를 비교하여 종료
- Live: 거래소에 SL/TP 주문이 실제 등록됨 (봇 꺼져도 거래소가 체결) + 모니터가 트레일링/펀딩비/시간 추가 체크

## 4개 트레이딩 프로필

`config/profiles.py`에 frozen dataclass로 정의. 프로필별로 리스크, 레버리지, 진입 조건, 보유 시간이 다르다.

### 프로필 비교 ($100 기준)

| 항목 | Conservative | Neutral | Aggressive | Scalp |
|------|:-----------:|:-------:|:----------:|:-----:|
| 건당 리스크 | 1.5% ($1.5) | 2% ($2) | 3% ($3) | 1% ($1) |
| 동시 포지션 | 3개 | 5개 | 5개 | 5개 |
| 총 마진 노출 | 40% | 60% | 70% | 60% |
| 일일 손실 한도 | 4% ($4) | 6% ($6) | 8% ($8) | 7% ($7) |
| 최대 드로다운 | 10% | 20% | 25% | 20% |
| 레버리지 범위 | 1~3배 | 2~6배 | 3~10배 | 5~15배 |
| 최소 신호 강도 | 0.70 | 0.65 | 0.60 | 0.40 |
| SL 배수 (ATR) | 2.0 | 2.0 | 2.0 | 2.5 |
| TP 배수 (ATR) | 4.0 | 4.0 | 4.0 | 4.0 |
| 트레일링 스톱 | 3.0% | 3.0% | 3.5% | 2.5% |
| 트레일링 활성화 | 1.0x ATR | 1.0x ATR | 0.8x ATR | 0.8x ATR |
| 청산 버퍼 | 30% | 20% | 15% | 15% |
| 최대 보유시간 | 48시간 | 72시간 | 72시간 | 4시간 |
| 분석 타임프레임 | 1h + 15m/4h | 1h + 15m/4h | 1h + 15m/4h | 3m + 1m/5m (소프트 MTF) |

### 멀티 프로필 모드

```bash
futuresbot run --paper --multi    # conservative + neutral + aggressive 동시 실행
```

- Paper 모드: 3개 프로필 병렬 실행, 각 프로필 독립적 포지션/통계 관리
- Live 모드: 안전을 위해 단일 프로필만 허용
- Discord에 프로필별 P&L 비교 리포트 자동 발송

## 스케줄러 동작

### 스윙 모드 (`futuresbot run --paper --loop`)

| 작업 | 주기 | 설명 |
|------|:----:|------|
| 코인 스캔 + 매매 | 30분 | 전체 파이프라인 실행 |
| 포지션 모니터링 | 5분 | 7가지 종료 조건 검사 (REST 폴링) |
| Discord 상태 업데이트 | 30분 | Wallet, Available, 마진, P&L 리포트 |
| 일일 리포트 | 매일 23:00 UTC | 종합 P&L + 리스크 + 최근 거래 |

### 스캘핑 모드 (`futuresbot run --scalp --paper`)

| 작업 | 주기 | 설명 |
|------|:----:|------|
| 스파이크 감지 | 실시간 (WebSocket) | 볼륨 급등, 가격 급변 이벤트 |
| 핫코인 폴링 | 3분 | REST API로 상위 등락/거래량 조회 |
| 포지션 모니터링 | 실시간 (1초 틱) | markPrice@1s WebSocket 구독 |
| Discord 상태 업데이트 | 30분 | 스캘핑 전용 상태 리포트 |
| 일일 리포트 | 매일 23:00 UTC | 스캘핑 P&L 요약 |

## Discord 알림

2개 웹훅 채널로 분리:

### #alerts 채널 (`DISCORD_WEBHOOK_ALERTS`)

- **거래 체결**: 심볼, 방향(LONG/SHORT), 레버리지, 진입가, 사이즈, SL/TP
- **포지션 종료**: 심볼, 방향, P&L, 종료 사유 (WIN/LOSS 표시)

### #reports 채널 (`DISCORD_WEBHOOK_REPORTS`)

- **상태 업데이트 (30분)**: Wallet(전체 자산), Available(사용 가능 현금), 마진 사용량, 실현/미실현 P&L, 승률, 오픈 포지션 목록
- **멀티 프로필 비교**: 프로필별 Wallet, P&L, 승률, 거래 수 비교 테이블 + 순위
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

프로필별 쿼리 지원 (paper/live + profile name으로 분리 조회).

## Docker 배포

```bash
git clone https://github.com/alton15/binance-futures-bot.git
cd binance-futures-bot
cp .env.example .env   # API 키 설정
docker compose up -d   # 2개 서비스 동시 시작
docker compose logs -f # 로그 확인
docker compose down    # 중지
docker compose restart # 재시작
```

**2개 서비스**:
- `bot`: 스윙 트레이딩 데몬 (30분 주기 스캔)
- `scalp`: WebSocket 이벤트 드리븐 스캘핑 (별도 프로세스)

`restart: unless-stopped`으로 크래시 시 자동 재시작. `./data:/app/data` 볼륨으로 DB 영속성 보장. 60초 간격 헬스체크.

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
# 전체 테스트 (169개)
pytest tests/ -v

# 개별 모듈
pytest tests/test_db_models.py -v           # DB 모델 (11개)
pytest tests/test_indicators.py -v          # 기술적 지표 (5개)
pytest tests/test_signals.py -v             # 신호 생성 + NEUTRAL 데드존 (13개)
pytest tests/test_signal_quality.py -v      # 시그널 품질 필터 (16개)
pytest tests/test_coin_scanner.py -v        # 코인 스캐너 (4개)
pytest tests/test_leverage_calc.py -v       # 레버리지 + 수수료 모델링 (30개)
pytest tests/test_risk_manager.py -v        # 10-gate 리스크 (14개)
pytest tests/test_profiles.py -v            # 프로필 불변성 + 폴백 (16개)
pytest tests/test_notifier.py -v            # 알림 포맷팅 (7개)
pytest tests/test_order_executor.py -v      # 주문 실행 + 수수료 (3개)
pytest tests/test_position_monitor.py -v    # 트레일링 스톱 + 종료 (13개)
pytest tests/test_scalping.py -v            # 스캘핑 전체 (37개)
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
| 실시간 스트림 | websockets | 마크 가격, 미니 티커, 북 티커 |
| HTTP 클라이언트 | httpx | Discord 웹훅 전송 |
| 환경변수 | python-dotenv | .env 파일 관리 |
| 컨테이너 | Docker | 2서비스 배포 (bot + scalp) |

## 운영 비용

| 항목 | 비용 |
|------|:----:|
| 바이낸스 API (시세 조회) | 무료 |
| pandas-ta 지표 계산 | 무료 |
| SQLite DB | 무료 |
| Discord 웹훅 | 무료 |
| Oracle Cloud 서버 | 무료 (Always Free) |
| **실거래 시 거래 수수료** | **0.02~0.04%/건** |
