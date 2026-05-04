"""
SQLModel table definitions for Arth's SQLite database.

Tables:
  - PipelineRun       — audit trail of each pipeline execution
  - Transaction       — the core financial data, mirrors CanonicalTransaction
                        with DB-specific additions (id, content_hash, timestamps,
                        source_type, gmail_message_id, spend_category, holding_id)
  - ProcessedEmail    — dedup ledger for the Gmail scraper; one row per Gmail
                        message ID so the same email is never processed twice
  - RecurringPattern  — auto-detected recurring transaction patterns (Phase 4.5c)
  - Goal              — user-defined financial goals (Phase 4.5d, hierarchy Phase B.0)
  - GoalStatusCache   — sim-on-write snapshot per goal (Track 3: dashboard % without re-sim)
  - InflationRate     — cached CPI / sector inflation (Goals architecture V2)
  - LifeEvent         — flags for activation DSL (event:...) (Phase B.0)
  - Holding           — portfolio position snapshot (Phase A.0)
  - NseEquityReference — cached NSE index + bhav snapshot per ticker (cap / industry / instrument kind)
  - InvestmentTransaction — broker/fund ledger rows (Phase A.0)
  - Liability         — loans and recurring obligations (Phase A.0)
  - Price             — daily close/NAV per symbol (Phase A.0)
  - ChatSession / ChatMessage — dashboard agent chat history (Sub-Plan 5)
  - FamilyMember       — household owner for scraper account mappings (Track 2 onboarding)
  - OnboardingState    — persisted wizard step + JSON payloads (Track 2 onboarding)
  - UserPipelineSource — per-user file pipeline: source_key → account_id + statement folder

Design notes:
  - Enum fields are stored as VARCHAR (SQLite has no native enum type anyway).
    SQLModel coerces them automatically on read/write.
  - `amount` / `closing_balance` are stored as FLOAT because SQLite doesn't
    have DECIMAL.  For a personal finance app with INR values this is fine.
  - `content_hash` is a SHA-256 digest used for idempotent inserts (dedup).
  - We skip ORM-level Relationship() here because we don't need lazy-loaded
    navigation in either direction — the FK constraint is what matters, and
    queries use explicit joins or ID lookups.
  - `source_type` on Transaction drives reconciliation logic:
      "statement"  — inserted by the file-based pipeline (default)
      "email"      — inserted by the Gmail scraper (is_reviewed=False)
      "reconciled" — was email-sourced, then upgraded when the matching
                     statement line arrived
"""

import datetime

from sqlalchemy import Column, Index, String, Text
from sqlmodel import Field, SQLModel

from api.services.encryption import EncryptedStr


# ───────────────────────────────────────────────────────────────────────────
# PipelineRun — one row per pipeline execution
# ───────────────────────────────────────────────────────────────────────────

class PipelineRun(SQLModel, table=True):
    __tablename__ = "pipeline_runs"

    id: int | None = Field(default=None, primary_key=True)
    source_key: str                                     # e.g. "hdfc_savings" or "all"
    llm_model: str = "auto"                             # model used, or "none"
    txn_count: int = 0                                  # total rows processed
    new_count: int = 0                                  # rows actually inserted (non-dupes)
    updated_count: int = 0                              # existing rows that had NULLs backfilled
    status: str = "running"                             # running | completed | failed
    txn_date_min: datetime.date | None = None           # earliest txn date in this run
    txn_date_max: datetime.date | None = None           # latest txn date in this run
    started_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )
    completed_at: datetime.datetime | None = None
    error_message: str | None = None


# ───────────────────────────────────────────────────────────────────────────
# Transaction — the core financial data table
# ───────────────────────────────────────────────────────────────────────────

class Transaction(SQLModel, table=True):
    __tablename__ = "transactions"

    # Composite index used by the reconciliation query in db_writer.py.
    # When a statement row arrives, we look for an unreconciled email row
    # with (account_id, amount, txn_date ± 1 day, source_type='email').
    # This index makes that scan fast even with thousands of transactions.
    __table_args__ = (
        Index(
            "ix_txn_reconciliation",
            "account_id", "amount", "txn_date", "source_type",
        ),
        Index("ix_transactions_user_id", "user_id"),
    )

    id: int | None = Field(default=None, primary_key=True)

    # Dedup key: SHA-256(txn_date|raw_description|amount|account_id)
    content_hash: str = Field(unique=True, index=True)

    # Core fields (mirror CanonicalTransaction)
    txn_date: datetime.date = Field(index=True)
    account_id: str = Field(index=True)
    # Arth user who owns this row (same string as auth username). Set on insert from
    # account→user mapping; API filters all reads by session user.
    user_id: str | None = Field(default=None)
    source_statement: str
    direction: str = Field(index=True)                  # INFLOW / OUTFLOW
    amount: float
    currency: str = "INR"

    # Classification fields (nullable — filled progressively by pipeline)
    txn_type: str | None = Field(default=None, index=True)
    channel: str | None = None
    upi_type: str | None = None
    counterparty: str | None = Field(default=None, index=True)
    counterparty_category: str | None = Field(default=None, index=True)

    # Raw / audit
    raw_description: str
    ref_number: str | None = None
    closing_balance: float | None = None
    value_date: datetime.date | None = None
    notes: str | None = None

    # DB-only metadata
    is_reviewed: bool = Field(default=True)
    pipeline_run_id: int | None = Field(default=None, foreign_key="pipeline_runs.id")
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )
    updated_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )

    # ── Email scraper additions ──────────────────────────────────────────
    # Where did this transaction come from?
    #   "statement"  — default, inserted by the file-based pipeline
    #   "email"      — inserted by the Gmail scraper (is_reviewed=False)
    #   "reconciled" — email row upgraded when matching statement arrived
    source_type: str = Field(default="statement", index=True)

    # Foreign key back to ProcessedEmail.gmail_message_id.
    # NULL for statement-sourced rows; set for email + reconciled rows.
    gmail_message_id: str | None = Field(default=None, index=True)

    # ── Phase 4.5c: Needs / Wants / Savings tagging ──────────────────────
    # Macro classification of what this OUTFLOW transaction is going towards.
    # Values: "NEED" | "WANT" | "SAVING" | "INVESTMENT" | NULL
    # NULL for INFLOW transactions (income) and any unclassified rows.
    spend_category: str | None = Field(default=None, index=True)

    # When True, transaction is hidden from all dashboard metrics (still listed in table).
    exclude_from_analytics: bool = Field(default=False, index=True)
    # Stored reason: "refund" | "test_transaction" | "duplicate" | "other" or free text for "other".
    exclusion_reason: str | None = Field(default=None)

    # Provenance of last automated classification (RULES_* / LLM); USER_REVIEWED when edited in UI.
    classification_source: str | None = Field(default=None, index=True)

    # HIGH | MEDIUM | LOW — how much manual review this email-sourced row likely needs.
    review_confidence: str | None = Field(default=None, index=True)

    # ── Phase A.0: link bank rows (e.g. INCOME_DIVIDEND) to a holding ────────
    holding_id: int | None = Field(default=None, foreign_key="holdings.id")


# ───────────────────────────────────────────────────────────────────────────
# ProcessedEmail — dedup ledger for the Gmail scraper
# ───────────────────────────────────────────────────────────────────────────

class ProcessedEmail(SQLModel, table=True):
    """One row per Gmail message that the scraper has attempted to process.

    Purpose: prevent double-processing on server restarts.  Before fetching
    a message body, the orchestrator checks this table.  If the message ID
    is already here (any status), the email is skipped.

    Status values:
      "processed" — parsed successfully; txn_count transactions were created
      "skipped"   — no matching parser (non-transaction email), or parser
                    returned [] (e.g. E-mandate with no amount)
      "failed"    — an exception was raised during parsing or DB write
    """

    __tablename__ = "processed_emails"

    id: int | None = Field(default=None, primary_key=True)

    # The Gmail message ID (e.g. "18e4f2a3b1c9d7e5").  Unique so the same
    # email can never be inserted twice regardless of race conditions.
    gmail_message_id: str = Field(unique=True, index=True)

    sender: str                             # normalised from-address
    subject: str
    received_at: datetime.datetime          # timestamp from the email header

    txn_count: int = 0                      # how many transactions were created
    status: str = "processed"              # processed | skipped | failed
    error_message: str | None = None        # populated on status='failed'

    processed_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )


# ───────────────────────────────────────────────────────────────────────────
# RecurringPattern — auto-detected recurring transaction patterns (Phase 4.5c)
# ───────────────────────────────────────────────────────────────────────────

class RecurringPattern(SQLModel, table=True):
    """A recurring transaction pattern detected by the detection algorithm.

    One row per unique (user_id, counterparty, direction, frequency) combination.
    The algorithm runs a statistical analysis on transaction history to find
    groups with consistent intervals (std dev < 25% of median interval).

    is_confirmed: False = auto-detected, True = user has confirmed the pattern.
    is_active: True if the counterparty was seen within the last 2× expected intervals.
    """

    __tablename__ = "recurring_patterns"
    __table_args__ = (
        Index(
            "uq_recurring_pattern_user_cp_dir_freq",
            "user_id",
            "counterparty",
            "direction",
            "frequency",
            unique=True,
        ),
    )

    id: int | None = Field(default=None, primary_key=True)

    user_id: str = Field(index=True)

    counterparty: str = Field(index=True)
    counterparty_category: str | None = None
    direction: str = Field(index=True)          # "INFLOW" or "OUTFLOW"

    # Statistical properties derived from matched transactions
    expected_amount: float                       # median of matched transaction amounts
    amount_tolerance: float = 0.0               # std dev of amounts (how much it varies)
    frequency: str = Field(index=True)          # "WEEKLY" | "MONTHLY" | "QUARTERLY" | "YEARLY"
    day_of_month: int | None = None             # typical day (for monthly patterns)

    # Temporal tracking
    last_seen_date: datetime.date
    next_expected_date: datetime.date | None = None  # last_seen + median_interval

    # State
    is_active: bool = Field(default=True, index=True)  # False if overdue by 2× interval
    is_confirmed: bool = False                          # True when user confirms the pattern

    # Aggregate stats
    match_count: int = 0                        # how many transactions matched this pattern
    total_amount: float = 0.0                   # sum of all matched amounts

    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )
    updated_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )


# ───────────────────────────────────────────────────────────────────────────
# Goal — user-defined financial goals (Phase 4.5d)
# ───────────────────────────────────────────────────────────────────────────

class Goal(SQLModel, table=True):
    """A financial goal tracked by the user.

    goal_type values:
      "EXPENSE_LIMIT" — keep category spending under target_amount/month (auto-computed)
      "SAVINGS"       — accumulate target_amount by target_date (manual current_value)
      "EMERGENCY_FUND"— maintain N months of expenses as liquid savings (manual)
      "INVESTMENT"    — hit portfolio target or SIP consistency (manual)
      "DEBT_PAYOFF"   — pay off loan ahead of schedule (manual)
      "INSURANCE"     — maintain adequate insurance cover (manual)
      "TAX"           — maximise 80C deductions / harvest losses (manual)

    ``status`` column: legacy string (defaults ON_TRACK) — not used for API progress;
    the goals API returns ``computed_percentage`` instead of categorical status.

    Progress for EXPENSE_LIMIT goals is auto-computed live from the transactions DB.
    All other goals use current_value (manually updated by the user).

    ``chart_key`` binds a goal to a dashboard chart metric (e.g. expense_need_want_stack,
    investment_net, category:swiggy_food) so limits match chart filters.

    Phase B.0 — hierarchy / pyramid:
      ``tier`` groups goals (L1 / L2 / L3 / L4; legacy VISION / STRATEGY / TACTIC / OPERATIONAL).
      ``activation_status`` is lifecycle (PENDING / ACTIVE / COMPLETED), separate
      from legacy ``status``; progress is expressed as ``computed_percentage`` in API responses.
      ``pyramid_id`` is a short stable id (e.g. V1, S4) unique per ``user_id`` for DSL refs.
    """

    __tablename__ = "goals"
    __table_args__ = (
        # Named unique index (SQLite names inline UNIQUE as sqlite_autoindex_* otherwise).
        Index("uq_goals_user_pyramid_id", "user_id", "pyramid_id", unique=True),
    )

    id: int | None = Field(default=None, primary_key=True)

    name: str
    goal_type: str = Field(index=True)          # GoalType string

    target_amount: float | None = None           # e.g. 10000 (spend limit, target balance)
    target_date: datetime.date | None = None     # deadline for the goal

    # JSON blob for goals with complex conditions (e.g. "savings_rate >= 40").
    # NULL for most goals; used only when target_amount alone isn't enough.
    target_metric: str | None = None

    priority: int = 3                            # 1 (highest) to 5 (lowest)
    linked_layer: int = 3                        # 1-5 financial layers from goals_framework
    linked_category: str | None = None          # e.g. "Food & Dining" for EXPENSE_LIMIT goals (legacy)
    # Dashboard chart binding: expense_need_want_stack, investment_net, category:<series>
    chart_key: str | None = Field(default=None, index=True)

    # MONTHLY: cap / progress per calendar month (default). ANNUAL: EXPENSE_LIMIT only — YTD vs target.
    progress_cadence: str = Field(default="MONTHLY", index=True)

    user_id: str = Field(index=True)  # always set from authenticated user on create

    # Decomposition: child goals created from POST .../decompose?auto_create=true (replaces goal_links).
    parent_goal_id: int | None = Field(
        default=None,
        foreign_key="goals.id",
        index=True,
        description="Parent goal id when this row was created as a decomposition child; NULL otherwise.",
    )

    # Manual override for non-auto-computable goals (updated by PATCH /api/goals/{id})
    current_value: float | None = None

    status: str = Field(default="ON_TRACK", index=True)
    notes: str | None = None

    # ── Phase B.0: goal pyramid / activation (see module docstring) ─────────
    # Field(max_length=, ge=, le=) documents the contract for OpenAPI / future use.
    # SQLModel does not run Pydantic validation when you construct or load ORM rows,
    # so bounds and string lengths are enforced on create/update in the API (Phase B.3).
    pyramid_id: str | None = Field(
        default=None,
        max_length=10,
        description="Stable tier label, e.g. V1, S4 — unique per user when set.",
    )
    tier: str | None = Field(
        default=None,
        index=True,
        max_length=32,
        description="L1 | L2 | L3 | L4 (legacy: VISION | STRATEGY | TACTIC | OPERATIONAL)",
    )
    time_horizon: str | None = Field(
        default=None,
        max_length=32,
        description="MONTHLY | QUARTERLY | ANNUAL | MULTI_YEAR | DECADE",
    )
    funding_mode: str | None = Field(
        default=None,
        max_length=32,
        description="ACCUMULATION | CONSTRAINT | EVENT | MAINTENANCE",
    )
    activation_status: str = Field(
        default="ACTIVE",
        index=True,
        max_length=32,
        description="PENDING | ACTIVE | COMPLETED",
    )
    activation_condition: str | None = Field(
        default=None,
        max_length=500,
        description="DSL, e.g. goal:S4:completed AND event:child_born",
    )
    monthly_allocation: float | None = Field(
        default=None,
        ge=0,
        description="Current monthly INR from surplus pool.",
    )
    allocation_priority: int | None = Field(
        default=None,
        ge=1,
        le=100,
        description="Surplus funding order; 1 = highest.",
    )
    interruptible: bool = Field(
        default=True,
        description="Whether pausing this goal is considered safe.",
    )
    sensitivity_to_returns: str | None = Field(
        default=None,
        max_length=16,
        description="LOW | MEDIUM | HIGH",
    )

    # ── Goals architecture V2 (simulation / surplus / priority) ─────────
    goal_class: str | None = Field(
        default=None,
        max_length=32,
        description="POINT_IN_TIME | RECURRING_CASH_FLOW",
    )
    recurrence_amount: float | None = Field(
        default=None,
        ge=0,
        description="Amount per recurrence period (e.g. EMI per month).",
    )
    recurrence_frequency: str | None = Field(
        default=None,
        max_length=16,
        description="MONTHLY | QUARTERLY | ANNUAL",
    )
    recurrence_start: datetime.date | None = Field(
        default=None,
        description="When recurring payments begin.",
    )
    recurrence_end: datetime.date | None = Field(
        default=None,
        description="When recurring payments end (NULL = ongoing).",
    )
    goal_specific_inflation_rate: float | None = Field(
        default=None,
        ge=0,
        le=50,
        description="Annual inflation % for this goal's cost; NULL = use general CPI.",
    )
    expected_return_rate: float | None = Field(
        default=None,
        ge=0,
        le=50,
        description="Expected annual return % for this goal's horizon.",
    )
    starting_balance: float | None = Field(
        default=None,
        ge=0,
        description="Amount already saved toward this goal.",
    )
    system_priority_score: float | None = Field(
        default=None,
        description="Computed priority 0–100; higher = fund first (Sub-Plan E).",
    )
    goal_subtype: str | None = Field(
        default=None,
        max_length=64,
        description="HOME_PURCHASE | VEHICLE | WEDDING | … | CUSTOM",
    )

    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )
    updated_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )


# ───────────────────────────────────────────────────────────────────────────
# GoalStatusCache — full-simulation progress snapshot (Track 3)
# ───────────────────────────────────────────────────────────────────────────


class GoalStatusCache(SQLModel, table=True):
    """One row per goal: last full ``simulate()`` projection fields + invalidation hash.

    Rows are rebuilt when goals/transactions/holdings change (fingerprint mismatch) or
    when the user calls ``POST /api/goals/refresh-status``. ``monthly_trajectory`` is
    not stored here — only headline % and supporting scalars (see ``status_data`` JSON).
    """

    __tablename__ = "goal_status_cache"

    id: int | None = Field(default=None, primary_key=True)
    goal_id: int = Field(foreign_key="goals.id", unique=True, index=True)
    user_id: str = Field(index=True)
    goal_class: str = Field(
        max_length=32,
        description="POINT_IN_TIME | RECURRING_CASH_FLOW (effective class at compute time).",
    )
    percentage: float = Field(
        description="Headline progress: projected_completion_pct (PIT) or periods_met_pct (recurring).",
    )
    status_data: str = Field(sa_column=Column(Text, nullable=False))
    simulation_hash: str = Field(index=True, max_length=64)
    computed_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )


# ───────────────────────────────────────────────────────────────────────────
# InflationRate — cached CPI / sector inflation (Sub-Plan A / F)
# ───────────────────────────────────────────────────────────────────────────


class InflationRate(SQLModel, table=True):
    """Cached inflation: one row per category, or for ``CPI_GENERAL`` one row per ``YYYY-MM`` (IMF monthly YoY)."""

    __tablename__ = "inflation_rates"

    id: int | None = Field(default=None, primary_key=True)
    category: str = Field(index=True, max_length=64)
    rate: float
    source: str = Field(max_length=128)
    period: str = Field(max_length=32)
    fetched_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )
    user_id: str = Field(default="system", index=True)


# ───────────────────────────────────────────────────────────────────────────
# LifeEvent — boolean flags referenced by activation_condition DSL (Phase B.0)
# ───────────────────────────────────────────────────────────────────────────


class LifeEvent(SQLModel, table=True):
    """Named life milestone (employed, married, …) for event:<key> in the activation DSL."""

    __tablename__ = "life_events"

    id: int | None = Field(default=None, primary_key=True)
    event_key: str = Field(index=True, max_length=64)
    occurred: bool = Field(default=False)
    occurred_date: datetime.date | None = None
    user_id: str = Field(index=True)
    notes: str | None = Field(default=None, max_length=2000)
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )
    updated_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )


# ───────────────────────────────────────────────────────────────────────────
# Reminder — manual recurring obligations (rent, credit card due, etc.)
# ───────────────────────────────────────────────────────────────────────────


class Reminder(SQLModel, table=True):
    """User-configured payment reminders (e.g. rent by 5th, CC by 15th)."""

    __tablename__ = "reminders"

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    name: str
    due_day_of_month: int = Field(ge=1, le=31)
    amount: float | None = None
    counterparty_category: str | None = None
    # JSON array of transaction IDs, e.g. "[12, 45]" — optional mapping for matching.
    example_transaction_ids: str | None = Field(default=None)
    # JSON array of substrings; ANY must match raw_description or ref_number (case-insensitive).
    description_match_anchors: str | None = Field(default=None)
    is_active: bool = Field(default=True)
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )
    updated_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )


# ───────────────────────────────────────────────────────────────────────────
# Holding — one row per position / manual asset (Phase A.0)
# ───────────────────────────────────────────────────────────────────────────


class Holding(SQLModel, table=True):
    """A portfolio position or manually tracked asset.

    Enum-like fields (asset_class, valuation_method, …) store string values
    matching ``pipeline.models`` (e.g. AssetClass.EQUITY.value).
    """

    __tablename__ = "holdings"

    id: int | None = Field(default=None, primary_key=True)
    symbol: str | None = None
    name: str
    quantity: float | None = None
    asset_class: str = Field(index=True)
    # Display label only ("ICICI Direct") — safe to filter in SQL.
    account_platform: str = Field(index=True)
    # Sensitive account / demat fragments — encrypted at rest.
    account_identifier_encrypted: str | None = Field(
        default=None,
        sa_column=Column("account_identifier_encrypted", EncryptedStr(), nullable=True),
    )
    valuation_method: str
    current_value: float | None = None
    last_valued_date: datetime.date | None = None
    liquidity_class: str
    currency: str = "INR"

    average_cost_per_unit: float | None = None
    current_price_per_unit: float | None = None

    principal_amount: float | None = None
    interest_rate: float | None = None
    maturity_date: datetime.date | None = None
    compounding_frequency: str | None = None

    face_value: float | None = None
    coupon_rate: float | None = None
    coupon_frequency: str | None = None

    folio_number_encrypted: str | None = Field(
        default=None,
        sa_column=Column("folio_number_encrypted", EncryptedStr(), nullable=True),
    )
    fund_type: str | None = None

    # Holdings page classification (enriched from NSE / AMFI; all optional).
    sector: str | None = None  # NSE industry, e.g. "COMPUTERS - SOFTWARE"
    market_cap_class: str | None = None  # LARGE_CAP | MID_CAP | SMALL_CAP
    fund_category: str | None = None  # SEBI / AMFI bucket, e.g. "Equity Scheme - Large Cap Fund"
    fund_house: str | None = None  # AMC name, e.g. "SBI Mutual Fund"

    earliest_liquidity_date: datetime.date | None = Field(
        default=None,
        description="Earliest date value becomes accessible (Sub-Plan C). NULL = unknown.",
    )

    user_id: str = Field(index=True)
    is_active: bool = Field(default=True, index=True)
    notes: str | None = None
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )
    updated_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )


# ───────────────────────────────────────────────────────────────────────────
# NseEquityReference — Nifty index + full CM bhav snapshot (cap, industry, instrument kind)
# ───────────────────────────────────────────────────────────────────────────


class NseEquityReference(SQLModel, table=True):
    """One row per NSE **ticker** in the CM bhav universe — populated by ``refresh_nse_equity_reference``.

    The bhav file mixes common stock with NCDs, G-Secs, SGBs, T-bills, InvITs, REITs, etc.
    ``instrument_kind`` captures that coarse classification (from ``SCTYSRS`` / series).

    ``market_cap_class`` is **only** set for ``instrument_kind=EQUITY``: NIFTY 100 →
    ``LARGE_CAP``, NIFTY MIDCAP 150 → ``MID_CAP``, other equity-style bhav rows →
    ``SMALL_CAP``. Non-equities use ``NULL`` here so enrichment does not treat them as small-cap stocks.

    ``reference_json`` stores ``{"index_row": ..., "bhav_row": ...}`` (either side may
    be null) so future features can read extra NSE fields without new migrations.
    """

    __tablename__ = "nse_equity_reference"

    symbol: str = Field(primary_key=True, max_length=32)
    # NULL when the row is not a classified equity (bonds, REITs, etc.).
    market_cap_class: str | None = Field(default=None, max_length=16, index=True)
    # Coarse instrument bucket from bhav ``SCTYSRS`` (e.g. EQUITY, REIT, NCD, SGB, …).
    instrument_kind: str = Field(default="UNKNOWN", max_length=24, index=True)
    company_name: str | None = Field(default=None, max_length=512)
    industry: str | None = Field(default=None, max_length=256)
    isin: str | None = Field(default=None, max_length=16, index=True)
    last_price: float | None = None
    ffmc: float | None = None
    reference_json: str = Field(sa_column=Column(Text, nullable=False))
    updated_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )


# ───────────────────────────────────────────────────────────────────────────
# InvestmentTransaction — trades, SIPs, switches (Phase A.0)
# ───────────────────────────────────────────────────────────────────────────


class InvestmentTransaction(SQLModel, table=True):
    """Broker / fund ledger row.

    ``is_reviewed`` / ``source_type`` / ``gmail_message_id`` mirror :class:`Transaction`
    — email-sourced rows (scraper, statement PDFs) enter with ``source_type='email'``
    and ``is_reviewed=False`` so they appear on the Review page until approved.
    """

    __tablename__ = "investment_transactions"

    id: int | None = Field(default=None, primary_key=True)
    txn_date: datetime.date = Field(index=True)
    symbol: str | None = None
    txn_type: str = Field(index=True)
    quantity: float
    price_per_unit: float
    total_amount: float
    account_platform: str
    holding_id: int | None = Field(default=None, foreign_key="holdings.id")
    bank_transaction_id: int | None = Field(default=None, foreign_key="transactions.id")
    notes: str | None = None

    is_reviewed: bool = Field(default=True, index=True)
    # "statement" | "email" | None (legacy / file import — treated as reviewed pipeline)
    source_type: str | None = Field(default=None, index=True)
    gmail_message_id: str | None = Field(default=None, index=True)

    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )
    updated_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )


# ───────────────────────────────────────────────────────────────────────────
# HoldingValueSnapshot — dated statement / balance snapshots (historical Layer 1)
# ───────────────────────────────────────────────────────────────────────────


class HoldingValueSnapshot(SQLModel, table=True):
    __tablename__ = "holding_value_snapshots"
    __table_args__ = (
        Index("ix_holding_value_snapshot_holding_date", "holding_id", "snapshot_date", unique=True),
    )

    id: int | None = Field(default=None, primary_key=True)
    holding_id: int = Field(foreign_key="holdings.id", index=True)
    snapshot_date: datetime.date = Field(index=True)
    value: float
    source: str = "statement"
    notes: str | None = None
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )


# ───────────────────────────────────────────────────────────────────────────
# Liability — loans, EMIs, recurring premiums (Phase A.0)
# ───────────────────────────────────────────────────────────────────────────


class Liability(SQLModel, table=True):
    __tablename__ = "liabilities"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    liability_type: str = Field(index=True)
    principal_outstanding: float
    interest_rate: float
    emi_amount: float | None = None
    tenure_remaining_months: int | None = None
    emi_start_date: datetime.date | None = None
    emi_end_date: datetime.date | None = None
    user_id: str = Field(index=True)
    is_active: bool = Field(default=True, index=True)
    notes: str | None = None
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )
    updated_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )


# ───────────────────────────────────────────────────────────────────────────
# Price — historical closes / NAVs (Phase A.0)
# ───────────────────────────────────────────────────────────────────────────


class Price(SQLModel, table=True):
    __tablename__ = "prices"
    __table_args__ = (Index("ix_price_symbol_date", "symbol", "date", unique=True),)

    id: int | None = Field(default=None, primary_key=True)
    symbol: str = Field(index=True)
    date: datetime.date
    close_price: float
    source: str = "nse"


# ───────────────────────────────────────────────────────────────────────────
# User classification — contacts, merchant rules, per-user settings (Item C)
# ───────────────────────────────────────────────────────────────────────────


class UserContact(SQLModel, table=True):
    """Family / friends / acquaintances used for deterministic UPI & bank name matching."""

    __tablename__ = "user_contacts"

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    display_name: str
    # JSON array of alternate strings (truncated bank names, nicknames).
    aliases_json: str = Field(default="[]")
    # SELF | FAMILY | FRIEND | ACQUAINTANCE
    relationship: str = Field(index=True)
    # USER = Settings / API; ONBOARDING = seeded from pre-classification wizard (replaceable).
    contact_source: str = Field(default="USER")
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )
    updated_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )


class UserMerchantRule(SQLModel, table=True):
    """Keyword → counterparty/category; starter pack rows + user-learned overrides."""

    __tablename__ = "user_merchant_rules"
    __table_args__ = (
        Index("ix_user_merchant_rules_user_keyword", "user_id", "keyword"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: str
    keyword: str
    display_name: str
    counterparty_category: str
    # STARTER_PACK | USER_CORRECTION | MANUAL
    source: str
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )


class UserClassificationSettings(SQLModel, table=True):
    """One row per user: self-name, rent regex, salary tokens, custom txn-type patterns."""

    __tablename__ = "user_classification_settings"

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(unique=True, index=True)
    self_name: str = Field(default="")
    self_aliases_json: str = Field(default="[]")
    rent_recipient: str | None = None
    rent_pattern: str | None = None
    salary_indicators_json: str = Field(default='["PAYROLL"]')
    # JSON list of {"substring": "...", "txn_type": "SELF_TRANSFER"}
    custom_patterns_json: str = Field(default="[]")
    account_hints_json: str = Field(default="[]")
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )
    updated_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )


# ───────────────────────────────────────────────────────────────────────────
# Desktop prep — DB-backed Gmail/scraper config, local users, PDF secrets
# ───────────────────────────────────────────────────────────────────────────


class AppUser(SQLModel, table=True):
    """Local login identity (replaces single-user .env-only auth when present)."""

    __tablename__ = "app_users"

    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True)
    password_hash: str = Field(sa_column=Column(String(128)))
    # NULL until first-run setup wizard completes (banks + OAuth + optional secrets).
    setup_completed_at: datetime.datetime | None = None
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )


class UserSecrets(SQLModel, table=True):
    """Encrypted JSON map of env-style PDF/API secret keys → values (see scraper/pdf_utils)."""

    __tablename__ = "user_secrets"

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(unique=True, index=True)
    secrets_json: str | None = Field(
        default=None,
        sa_column=Column("secrets_json", EncryptedStr(), nullable=True),
    )
    updated_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )


class PasswordTemplate(SQLModel, table=True):
    """Recipe for deriving a PDF password from user-supplied ingredients (onboarding / WS3).

    Rows are seeded at startup; ``parser_key`` aligns with :func:`scraper.pdf_passwords.resolve_pdf_password_chain`
    ``parser_key=…`` and optional statement flows.
    """

    __tablename__ = "password_templates"

    id: int | None = Field(default=None, primary_key=True)
    parser_key: str = Field(unique=True, index=True)
    display_name: str = Field(sa_column=Column(String(256)))
    # JSON array of logical ingredient names, e.g. ``["pan"]`` or ``["hdfc_account_number","dob_ddmmyyyy"]``.
    required_fields_json: str = Field(sa_column=Column(Text))
    # Python ``str.format`` pattern using only placeholders listed in ``required_fields_json``.
    password_formula: str = Field(sa_column=Column(Text))
    notes: str | None = Field(default=None, sa_column=Column(Text, nullable=True))


class FamilyMember(SQLModel, table=True):
    """Household member for **account ownership** (which person owns a linked bank source).

    Not the same as :class:`UserContact` (used for UPI / classification hints).
    """

    __tablename__ = "family_members"

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    name: str = Field(sa_column=Column(String(128)))
    # e.g. SELF, SPOUSE, CHILD, PARENT, OTHER — free-form labels for the wizard UI.
    relationship: str = Field(sa_column=Column(String(64)))
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )


class OnboardingState(SQLModel, table=True):
    """Persisted wizard position + intermediate payloads (refresh-safe onboarding)."""

    __tablename__ = "onboarding_states"

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(unique=True, index=True)
    # Machine-readable step id (e.g. welcome, discovery, backfill_savings).
    current_step: str = Field(default="welcome", sa_column=Column(String(64)))
    completed_steps_json: str = Field(default="[]", sa_column=Column(Text))
    discovery_results_json: str = Field(default="{}", sa_column=Column(Text))
    backfill_progress_json: str = Field(default="{}", sa_column=Column(Text))
    # Raw pre-classification form inputs (first/last/aliases/hints) for wizard resume — see GET /preclassification.
    preclassification_raw_json: str = Field(default="{}", sa_column=Column(Text))
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )
    updated_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )


class ScraperBankSender(SQLModel, table=True):
    """One row per Gmail From address the scraper should poll for a user."""

    __tablename__ = "scraper_bank_senders"
    __table_args__ = (
        Index("ix_scraper_bank_senders_user_sender", "user_id", "sender_email", unique=True),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    # Normalised lower-case address, e.g. alerts@hdfcbank.net
    sender_email: str = Field(index=True)
    # Optional tag for tooling; routing still uses sender_email → parser registry.
    parser_key: str | None = None
    first_run_lookback_days: int | None = None
    enabled: bool = Field(default=True)
    # Auto-discovery / onboarding metadata (mirrors ``scraper.config.BANK_SENDERS``).
    display_name: str | None = Field(default=None, sa_column=Column(String(256), nullable=True))
    # High-level source bucket: savings | credit_card | broker (see scraper.config).
    source_type: str | None = Field(default=None, sa_column=Column(String(32), nullable=True))
    discovery_subject_patterns_json: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    expected_cadence: str | None = Field(default=None, sa_column=Column(String(32), nullable=True))
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )


class ScraperAccountMapping(SQLModel, table=True):
    """Maps last-4 (per sender) to pipeline account_id + source_key for a user."""

    __tablename__ = "scraper_account_mappings"
    __table_args__ = (
        Index(
            "uq_scraper_acct_map_user_sender_l4",
            "user_id",
            "sender_email",
            "last_4_digits",
            unique=True,
        ),
        Index("ix_scraper_acct_map_account_id", "account_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    sender_email: str = Field(index=True)
    last_4_digits: str = Field(index=True)
    account_id: str = Field(index=True)
    source_key: str
    # Which household member owns this account mapping (defaults to Self — see patches).
    member_id: int | None = Field(default=None, foreign_key="family_members.id")
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )


class UserPipelineSource(SQLModel, table=True):
    """Per-user file-based pipeline source: parser ``source_key`` → account + statement folder.

    Replaces hardcoded ``SOURCE_CONFIGS`` in ``pipeline/config.py`` so account IDs and
    on-disk statement directory names live in SQLite (one row per user per source_key).
    """

    __tablename__ = "user_pipeline_sources"
    __table_args__ = (
        Index(
            "uq_user_pipeline_sources_user_source",
            "user_id",
            "source_key",
            unique=True,
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    source_key: str = Field(sa_column=Column(String(64)))
    account_id: str = Field(sa_column=Column(String(64)))
    currency: str = Field(default="INR", sa_column=Column(String(8)))
    # Subdirectory name under ``pipeline.config.DATA_DIR`` (e.g. ``HDFC_Savings``).
    statement_folder: str | None = Field(default=None, sa_column=Column(String(256), nullable=True))
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )


# ───────────────────────────────────────────────────────────────────────────
# Dashboard agent chat — persisted sessions + OpenAI-format message rows
# ───────────────────────────────────────────────────────────────────────────


class ChatSession(SQLModel, table=True):
    """One saved Arth chat thread per logged-in user (dashboard Plan 5)."""

    __tablename__ = "chat_sessions"

    id: str = Field(primary_key=True)
    user_id: str = Field(index=True)
    title: str | None = Field(default=None)
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )
    updated_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )
    # 0 = active, 1 = archived (soft delete)
    is_archived: int = Field(default=0)


class ChatMessage(SQLModel, table=True):
    """One row per user/assistant/tool message in a session (OpenAI chat format)."""

    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_session_created", "session_id", "id"),)

    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(foreign_key="chat_sessions.id", index=True)
    role: str
    content: str | None = None
    tool_calls_json: str | None = Field(default=None, sa_column=Column(Text))
    tool_call_id: str | None = None
    tool_name: str | None = None
    metadata_json: str | None = Field(default=None, sa_column=Column(Text))
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )
