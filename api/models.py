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
  - GoalLink          — parent/child causal links between goals (Phase B.0)
  - LifeEvent         — flags for activation DSL (event:...) (Phase B.0)
  - Holding           — portfolio position snapshot (Phase A.0)
  - InvestmentTransaction — broker/fund ledger rows (Phase A.0)
  - Liability         — loans and recurring obligations (Phase A.0)
  - Price             — daily close/NAV per symbol (Phase A.0)

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

from sqlalchemy import Column, Index
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
    )

    id: int | None = Field(default=None, primary_key=True)

    # Dedup key: SHA-256(txn_date|raw_description|amount|account_id)
    content_hash: str = Field(unique=True, index=True)

    # Core fields (mirror CanonicalTransaction)
    txn_date: datetime.date = Field(index=True)
    account_id: str = Field(index=True)
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

    One row per unique (counterparty, direction, frequency) combination.
    The algorithm runs a statistical analysis on transaction history to find
    groups with consistent intervals (std dev < 25% of median interval).

    is_confirmed: False = auto-detected, True = user has confirmed the pattern.
    is_active: True if the counterparty was seen within the last 2× expected intervals.
    """

    __tablename__ = "recurring_patterns"

    id: int | None = Field(default=None, primary_key=True)

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

    status values: "ON_TRACK" | "AT_RISK" | "BEHIND" | "ACHIEVED" | "PAUSED"

    Progress for EXPENSE_LIMIT goals is auto-computed live from the transactions DB.
    All other goals use current_value (manually updated by the user).

    ``chart_key`` binds a goal to a dashboard chart metric (e.g. expense_need_want_stack,
    investment_net, category:swiggy_food) so limits match chart filters.

    Phase B.0 — hierarchy / pyramid:
      ``tier`` groups goals (VISION / STRATEGY / TACTIC / OPERATIONAL).
      ``activation_status`` is lifecycle (PENDING / ACTIVE / COMPLETED / PAUSED), separate
      from ``status`` which remains progress (ON_TRACK / AT_RISK / …).
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

    user_id: str = Field(default="sashank", index=True)  # "sashank" or "aditi"

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
        description="VISION | STRATEGY | TACTIC | OPERATIONAL",
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
        description="PENDING | ACTIVE | COMPLETED | PAUSED",
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

    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )
    updated_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )


# ───────────────────────────────────────────────────────────────────────────
# GoalLink — directed edges in the goal pyramid (Phase B.0)
# ───────────────────────────────────────────────────────────────────────────


class GoalLink(SQLModel, table=True):
    """Relationship between two goals owned by the same user.

    link_type values: DECOMPOSES_INTO | DEPENDS_ON | CONTRIBUTES_TO
    Cycles and duplicate (parent, child, type) triples are rejected in application code (B.1).
    """

    __tablename__ = "goal_links"
    __table_args__ = (
        Index(
            "uq_goal_link_parent_child_type",
            "parent_goal_id",
            "child_goal_id",
            "link_type",
            unique=True,
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    parent_goal_id: int = Field(foreign_key="goals.id", index=True)
    child_goal_id: int = Field(foreign_key="goals.id", index=True)
    link_type: str = Field(max_length=32)
    description: str | None = Field(default=None, max_length=500)
    contribution_amount: float | None = Field(default=None, ge=0)
    user_id: str = Field(default="sashank", index=True)
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )


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
    user_id: str = Field(default="sashank", index=True)
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
    user_id: str = Field(default="sashank", index=True)
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

    user_id: str = Field(default="sashank", index=True)
    is_active: bool = Field(default=True, index=True)
    notes: str | None = None
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )
    updated_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )


# ───────────────────────────────────────────────────────────────────────────
# InvestmentTransaction — trades, SIPs, switches (Phase A.0)
# ───────────────────────────────────────────────────────────────────────────


class InvestmentTransaction(SQLModel, table=True):
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
    user_id: str = Field(default="sashank", index=True)
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
