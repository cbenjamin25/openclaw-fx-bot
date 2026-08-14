# openclaw-fx-bot

Deterministic FX trading system with a Claude regime layer. Full plan:
`docs/build-plan.md` (drop the plan doc from our chat in there).

**Current phase: 0 — Foundation.**

## One-time setup (manual — only you can do these)

### 1. Oanda practice account (~5 min)
1. Sign up at oanda.com → choose a **Demo / Practice** account (US entity)
2. In the account portal → **Manage API Access** → generate a **Personal Access Token**
3. Note your **account ID** (format `101-001-XXXXXXX-001`)

### 2. Secrets into SSM (from your workstation with AWS creds)
```bash
aws ssm put-parameter --name /fxbot/oanda_token \
  --type SecureString --value 'YOUR_TOKEN'
aws ssm put-parameter --name /fxbot/oanda_account_id \
  --type SecureString --value 'YOUR_ACCOUNT_ID'
```
The token never lives in a file, env script, or repo. (We both remember the
Desk Djinn key incident.)

### 3. Infrastructure
```bash
cd terraform
terraform init
terraform apply \
  -var vpc_id=vpc-XXXX \
  -var subnet_id=subnet-XXXX \
  -var alert_email=you@example.com
```
Confirm the SNS subscription email AWS sends you. Connect to the box with
the `connect_command` output (SSM Session Manager — no SSH ports exist).

### 4. Deploy code to the box
```bash
# in the SSM session:
sudo -iu fxbot
git clone https://github.com/YOUR_USER/openclaw-fx-bot.git
cd openclaw-fx-bot
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Phase 0 exit criteria — verify

```bash
python -m src.data.fetch EUR_USD H1 5000
```
Expected: batched fetch output ending in
`done: 5000 candles processed, latest stored ts = 2026-...`

Re-run it — second run should report `new: 0` per batch (idempotency).

Then run the test suite locally:
```bash
pip install -r requirements-dev.txt
pytest -v
```

## Safety rails already in place (Phase 0)
- Practice is the default environment; **live requires
  `FXBOT_I_UNDERSTAND_LIVE=yes`** and is gated behind Phase 6 criteria
- IAM is least-privilege: the box can read `/fxbot/*` secrets, write its
  own logs/metrics, publish alerts — nothing else
- Security group has **zero inbound rules**; admin via Session Manager
- CloudWatch heartbeat alarm scaffolded (goes hot when the Phase 4
  service starts publishing)

## Repo layout
```
src/data       candle fetch + SQLite store        ← Phase 0 (done)
src/backtest   engine + cost model                ← Phase 1
src/strategy   signal generators                  ← Phase 2
src/risk       sizing, breakers, kill switch      ← Phase 3
src/execution  Oanda streaming + orders           ← Phase 4
src/brain      Claude regime layer (OpenClaw)     ← Phase 5
```
