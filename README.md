# Tenacious Conversion Engine

Automated lead generation and conversion system for Tenacious Consulting and Outsourcing.

> **⚠️ KILL SWITCH: Default is SANDBOX mode.** All outbound email and SMS are intercepted and written to `outputs/sandbox_sink.jsonl`. Set `KILL_SWITCH=live` ONLY with explicit program-staff and Tenacious executive approval. Live mode sends real emails and SMS to real people.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Conversion Engine                           │
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐  │
│  │  Enrichment  │    │  Qualifier   │    │  Outreach        │  │
│  │  Pipeline    │───▶│  (ICP Seg.)  │───▶│  Composer        │  │
│  │              │    │              │    │  + Tone Checker  │  │
│  │ • Crunchbase │    │ • Seg 1 (A/B)│    │  + Bench Gate    │  │
│  │ • Layoffs    │    │ • Seg 2 (Mid)│    │                  │  │
│  │ • Job Posts  │    │ • Seg 3 (CTO)│    └────────┬─────────┘  │
│  │ • Leadership │    │ • Seg 4 (AI) │             │            │
│  │ • AI Maturity│    │ • ABSTAIN    │             │            │
│  │ • Comp. Gap  │    └──────────────┘             ▼            │
│  └──────────────┘                       ┌──────────────────┐   │
│                                         │  Kill Switch     │   │
│                                         │  SANDBOX → sink  │   │
│                                         │  LIVE → Resend   │   │
│                                         └────────┬─────────┘   │
│                                                  │             │
│  ┌──────────────┐    ┌──────────────┐   ┌────────▼──────────┐  │
│  │  HubSpot CRM │◀───│  Conversation│◀──│  Webhook Server   │  │
│  │              │    │  Manager     │   │  /webhook/email/  │  │
│  │              │    │  (threads)   │   │  /webhook/sms/    │  │
│  └──────────────┘    └──────────────┘   │  /webhook/calcom/ │  │
│                                         └──────────────────-┘  │
│  ┌──────────────┐    ┌──────────────┐   ┌──────────────────┐   │
│  │  Cal.com     │    │  Africa's    │   │  Langfuse        │   │
│  │  (Bookings)  │    │  Talking SMS │   │  (Observability) │   │
│  └──────────────┘    └──────────────┘   └──────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              │
                    ┌─────────▼─────────┐
                    │   τ²-Bench Eval   │
                    │   (retail domain) │
                    │   eval/harness.py │
                    └───────────────────┘
```

**Channel priority**: Email (primary) → SMS (warm leads only) → Voice (discovery call, human delivery lead)

---

## Setup

### 1. Prerequisites

```bash
git clone <repo>
cd conversion-engine
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env — required keys: OPENROUTER_API_KEY, RESEND_API_KEY,
#   HUPSPOT_ACESS_KEY, AFRICASTALK_API_KEY, CALCOM_API_KEY
# KILL_SWITCH=sandbox (default — leave this as-is during testing)
```

### 3. Start Cal.com

```bash
docker compose up -d   # Cal.com on http://localhost:3000
```

### 4. Start Webhook Server + Tunnel

```bash
ngrok http 8000   # copy HTTPS URL → WEBHOOK_BASE_URL in .env
uvicorn agent.webhook_server:app --host 0.0.0.0 --port 8000
```

### 5. Register Webhooks

- **Resend**: Dashboard → Webhooks → `$WEBHOOK_BASE_URL/webhook/email/reply`
- **Africa's Talking**: Sandbox → SMS Callback → `$WEBHOOK_BASE_URL/webhook/sms/incoming`
- **Cal.com**: Settings → Webhooks → `$WEBHOOK_BASE_URL/webhook/calcom/booking`

---

## Environment Variables

| Variable | Description | Required |
|---|---|---|
| `OPENROUTER_API_KEY` | OpenRouter key (DeepSeek V3 default) | ✅ |
| `KILL_SWITCH` | `sandbox` (default) or `live` | ✅ |
| `RESEND_API_KEY` | Resend email key | ✅ |
| `HUPSPOT_ACESS_KEY` | HubSpot Private App token | ✅ |
| `AFRICASTALK_API_KEY` | Africa's Talking key | ✅ |
| `CALCOM_URL` | Cal.com base URL | ✅ |
| `CALCOM_API_KEY` | Cal.com API key | ✅ |
| `LANGFUSE_SECRET_KEY` | Langfuse secret | Optional |
| `LANGFUSE_PUBLIC_KEY` | Langfuse public | Optional |
| `WEBHOOK_BASE_URL` | Public ngrok URL | ✅ |

---

## Running

### Single prospect (test)
```bash
source .venv/bin/activate
python -m agent.main
```

### Batch (5 synthetic prospects)
```bash
python -m agent.main batch
```

### τ²-Bench retail harness (30 tasks × 5 trials)
```bash
eval/tau2-bench/.venv/bin/python eval/harness.py --trials 5 --tasks 0-29
```

### Dry run (no API calls)
```bash
eval/tau2-bench/.venv/bin/python eval/harness.py --dry-run --tasks 0-4
```

---

## Directory Structure

```
conversion-engine/
├── agent/
│   ├── main.py               # Orchestrator (entry point)
│   ├── kill_switch.py        # ⚠️  Sandbox/live gate — read before touching
│   ├── qualifier.py          # ICP segment classifier (4 segments + ABSTAIN)
│   ├── bench_gate.py         # Bench capacity hard constraint
│   ├── outreach_composer.py  # Signal-grounded email composer
│   ├── tone_checker.py       # Style guide enforcer (5 markers)
│   ├── conversation_manager.py  # Per-contact thread isolation
│   ├── enrichment.py         # Signal enrichment pipeline
│   ├── scraper.py            # Playwright public-signal scraper
│   ├── llm.py                # DeepSeek V3 via OpenRouter
│   ├── email_handler.py      # Resend client
│   ├── sms_handler.py        # Africa's Talking (warm-gate enforced)
│   ├── hubspot.py            # HubSpot CRM client
│   ├── calcom_client.py      # Cal.com booking
│   └── webhook_server.py     # FastAPI webhook receiver
├── eval/
│   ├── harness.py            # τ²-Bench retail wrapper
│   ├── score_log.json        # Baseline stats (pass@1, CI, cost, latency)
│   ├── trace_log.jsonl       # Per-trial traces
│   ├── tau2-bench/           # τ²-Bench repo (.venv inside)
│   └── held_out/             # Sealed partition (tasks 30-49, .gitignored)
├── probes/
│   ├── probe_library.md      # 34 adversarial probes (10 categories)
│   ├── failure_taxonomy.md   # Category taxonomy + trigger rates
│   └── target_failure_mode.md  # Highest-ROI target + business cost math
├── outputs/                  # Runtime artifacts (.gitignored)
│   ├── sandbox_sink.jsonl    # All intercepted outbound in sandbox mode
│   └── threads/              # Per-contact thread state files
├── tenacious_sales_data/seed/  # ICP, style guide, bench summary, pricing
├── method.md                 # Act IV: confidence-aware phrasing mechanism
├── ablation_results.json     # 3 ablation variants on dev slice
├── evidence_graph.json       # Every memo number mapped to its source
└── baseline.md               # τ²-Bench retail baseline (pass@1=0.80, CI=[0.62,0.91])
```

---

## Kill Switch

```python
# agent/kill_switch.py
# Default: KILL_SWITCH=sandbox — all sends go to outputs/sandbox_sink.jsonl
# To enable live sends: KILL_SWITCH=live (requires program-staff approval)
```

Every `send_email()` and `send_sms()` call is routed through `kill_switch.send_email()` and `kill_switch.send_sms()`. In sandbox mode, the function writes the outbound record to the sink file and returns immediately without calling Resend or Africa's Talking.

**Pause condition**: The Tenacious CEO should pause the system if the measured wrong-signal email rate (emails where at least one factual claim is unverifiable from the hiring_signal_brief) exceeds 5% over a 2-week rolling window. Trigger metric: manually review 20 randomly sampled sent emails per week; halt if ≥ 1 in 20 contains an ungrounded assertion.

---

## Known Limitations

1. Playwright scraping falls back to synthetic data when chromium is not installed or careers pages are unreachable.
2. HubSpot custom property creation requires `crm.schemas.contacts.write` scope (not in default private app token); enrichment data is written as contact notes as fallback.
3. Timezone-aware scheduling (DST, multi-region) not yet implemented (probes P-025 to P-027 remain open).
4. Job title deduplication for AI maturity scoring not yet implemented (probe P-029).
5. Gap cross-checking against prospect's own public writing not yet implemented (probes P-031, P-034).

---

## Data Handling Policy

All prospect interactions during the challenge week are synthetic. See `tenacious_sales_data/policy/data_handling_policy.md`. The kill switch default (`sandbox`) ensures zero real outbound during testing.
