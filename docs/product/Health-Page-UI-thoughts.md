# **Arth — Financial Health Dashboard Guideline Document**

**Created:** 24 Mar 2026 | **Last updated:** 26 Mar 2026 **Purpose:** Design guideline for the `/health` page — the goal-connected, triage-first financial health dashboard. **Context:** This is one of three page-level guideline documents. The other two are the Holdings Page (`/portfolio`) and the Statements Page (`/statements`). This page does NOT duplicate content from those pages.

---

## **1 · Design Philosophy**

### **The dashboard is a triage system, not a report**

Every element on the dashboard must answer "so what?" without the user having to think. The answer is always one of:

* **"You're on track, keep going"** — confirmed with evidence  
* **"Something needs attention, here's what and why"** — flagged with a suggested action

If a metric can't do this, it doesn't belong on the dashboard.

### **Core principles**

1. **Every metric connects to a goal.** Absolute numbers without context are meaningless. Every number is shown relative to what's required.  
2. **Action-oriented, not vanity-feeding.** The question is always "should I change something?" — not "how am I doing in the abstract?"  
3. **Goal-centric, not metric-centric.** The home view leads with goals. Financial statements and ratios are supporting evidence on other pages.  
4. **Silence is golden.** When things are on track, the dashboard is calm. Flags only appear when something needs attention.  
5. **No duplication.** This page does not reproduce holdings tables, expense trackers, or financial statements. Those live on their respective pages. This page synthesizes and triages.

---

## **2 · The Four Lenses (MECE Framework)**

### **Lens 1: "Am I converting income to wealth at the rate my goals need?" (P\&L)**

**Primary Signal: Surplus vs. Required Surplus**

Not "you saved ₹1.5L this month" but "your goals need ₹2L/month in deployment. You deployed ₹1.5L. You're ₹50K short."

* Required surplus is derived from the goal pyramid — the sum of monthly allocations needed across all active goals  
* Shows: actual surplus, required surplus, gap (if any), and which goal is most impacted by the shortfall  
* When green: "Surplus of ₹1.6L exceeds the ₹1.5L your goal pyramid requires. ₹10K headroom."  
* When red: "Surplus of ₹1.1L is ₹40K below the ₹1.5L your goals need. At this pace, your house goal slips by 3 months."

**Attention Layer Flags:**

* Monthly surplus falls below goal-required surplus

**Does NOT belong here:**

* Absolute savings rate without the "required" comparison  
* Expense tracker components (those live on the existing expense tracker page)  
* Raw category breakdowns

**Detail:** → Statements page (P\&L view) for full income/expense breakdown \+ Expense Tracker for breakdown of expenses

---

### **Lens 2: "Are the assets serving my goals doing their job?" (Balance Sheet)**

This lens operates at the **goal-pool level**, not the individual holding level. It answers: "For each goal that has linked holdings, is that pool delivering the returns the goal needs?"

**Primary Signal: Goal-Linked Pools Performance**

For each goal that has designated holdings (via the `goal_holdings` junction table):

* Current value of the pool  
* Blended XIRR of the pool  
* Required return rate to hit the goal's target by the target date  
* Projected value at target date vs. required value  
* Status: ON\_TRACK / AT\_RISK / BEHIND

When green:

"House down payment pool: ₹14.2L, returning 13.1%. Projected ₹31.5L by Dec 2029 vs. ₹30L target. On track."

When red:

"House down payment pool: ₹8.4L, returning 5.2%. Goal needs 11%. Projected shortfall: ₹4.8L. \[View holdings on Portfolio page →\]"

The health page does NOT show which individual holding within the pool is underperforming. That's the Holdings page's job. The health page diagnoses at the goal level; the user drills into the Holdings page for holding-level detail.

**Secondary Signal: Unlinked Assets Nudge**

A gentle nudge when significant assets are not linked to any goal:

"₹2.3L in holdings are not linked to any goal. Linking holdings to goals enables health tracking. \[Link holdings →\]"

This serves as a setup prompt — encouraging users to connect their holdings to goals so the health page can provide full value. The same functionality should also appear on the Holdings page (TBD once the Holdings page design is complete).

**Attention Layer Flags:**

* Any goal-linked pool projected to miss its target (BEHIND status)  
* Pool XIRR below goal-required return for 6+ months (sustained underperformance)

**Does NOT belong here:**

* Individual holdings tables (→ Holdings page)  
* Net worth headline or breakdown (→ Statements page)  
* Asset allocation charts (→ Holdings page)  
* Per-holding XIRR or performance data (→ Holdings page)  
* Debt trajectory details (→ covered in Lens 3 as upcoming obligations)

**Detail:** → Holdings page for per-holding performance. → Statements page for balance sheet view.

---

### **Lens 3: "Can I handle what's coming?" (Cash Flow & Resilience)**

**Primary Signal: Cash Runway → Emergency Fund Goal**

Directly connected to the emergency fund goal.

"Emergency fund goal: 6 months of core expenses (₹4.8L). Current liquid buffer: 4.8 months (₹3.84L). Gap: 1.2 months. At current monthly allocation, gap closes in 4 months."

**Secondary Signal: Liquidity Ladder → Goal Execution Readiness**

Connected to goals with upcoming execution events (house purchase, wedding).

"House down payment: ₹30L target. If you need to deploy within 7 days, ₹22L is accessible without penalties."

This becomes critical as goal execution dates approach.

**Tertiary Signal: Upcoming Obligations (90-day lookahead)**

All committed outflows for the next quarter:

* EMIs, term insurance premium, SIPs, known large expenses  
* Total committed vs. available liquid cash

"Next 90 days: ₹30K EMIs \+ ₹48K insurance \+ ₹X SIPs \= ₹Y committed. Liquid cash covers these Z times over."

**Quaternary Signal: Debt Service Coverage**

Monthly income ÷ total monthly fixed obligations.

Low-priority now (₹26K/month obligations against healthy income) but becomes the \#1 signal in this lens the moment a home loan EMI enters the picture.

"Fixed obligations: ₹26K/month (13% of income). Well within safe limits."

**Attention Layer Flags:**

* Cash runway drops below 3 months (critical) or below emergency fund goal target  
* Large known outflow within 30 days without adequate liquid buffer  
* Debt service coverage drops below 2× (especially relevant post-home-loan)

**Goal Connections:**

* Emergency fund adequacy → Emergency Fund goal  
* Liquidity readiness → any goal with upcoming activation/execution event  
* Debt freedom timeline → cascading reallocation impact when obligations end

**Detail:** → Statements page (Cash Flow Statement) for full operating/investing/financing flows

---

### **Lens 4: "Am I on track for where I want to be?" (Trajectory)**

This is the synthesis lens. It pulls signals from the other three and presents them through the goal filter.

**Primary Signal: Goal Pyramid Health**

"34 goals total. 28 on track. 4 at risk. 2 behind."

The at-risk and behind goals are named explicitly with one-line explanations:

"AT RISK: Eating out (₹8.2K of ₹10K with 12 days left). BEHIND: Emergency fund (₹3.84L of ₹4.8L target, monthly allocation is ₹5K below required)."

**Secondary Signal: Upcoming Activations & Milestones**

Events from the goal hierarchy that are approaching:

"Bike loan ends in 20 months → ₹10K/month freed for reallocation. Wedding fund goal activates when emergency fund completes (projected: 4 months)."

**Tertiary Signal: Required vs. Actual Allocation**

Is the monthly surplus fully deployed to goals, or is there idle cash?

"Goal pyramid requires ₹1.5L/month. Deployed: ₹1.3L. Unallocated: ₹20K. Highest-priority underfunded goal: emergency fund."

**Attention Layer Flags:**

* Required surplus exceeds actual by \>10%  
* Milestone or activation trigger within 3 months  
* A completed goal that triggers a downstream activation

**Detail:** → Goals page for full hierarchy. → Simulation engine for projections.

---

## **3 · Visualization Design**

### **Page Layout: Grid Overview**

The `/health` page uses a **grid layout with four compact quadrants**, one per lens. Each quadrant shows the primary signal from its lens in a compact, scannable form. Clicking any quadrant expands to the full lens view.

┌─────────────────────────────────────────────────────┐  
│  ATTENTION LAYER (only when flags exist)             │  
│  🔴 House goal at risk — pool underperforming       │  
│  🟡 Eating out — 82% consumed, 12 days remaining    │  
└─────────────────────────────────────────────────────┘

┌────────────────────────┬────────────────────────────┐  
│  LENS 1: FLOW          │  LENS 2: POSITION          │  
│  Surplus vs Required   │  Goal-Pool Status          │  
│  \[compact chart\]       │  \[pool status badges\]      │  
│  \[click to expand →\]   │  \[click to expand →\]       │  
├────────────────────────┼────────────────────────────┤  
│  LENS 3: RESILIENCE    │  LENS 4: TRAJECTORY        │  
│  Cash Runway \+ Ladder  │  Goal Pyramid Summary      │  
│  \[compact visual\]      │  \[summary bar\]             │  
│  \[click to expand →\]   │  \[click to expand →\]       │  
└────────────────────────┴────────────────────────────┘

**Compact mode per quadrant:**

* Lens 1: Mini bar chart (latest 3 months of surplus vs. required line) \+ one-line status  
* Lens 2: Count of goal-linked pools and their status badges (3 on track, 1 at risk)  
* Lens 3: Cash runway number \+ mini liquidity ladder bar  
* Lens 4: Goal pyramid summary bar (28 green | 4 amber | 2 red)

### **Lens 1 Expanded Visualizations**

**Surplus vs. Required Surplus: Bar Chart with Goal Line**

* Monthly bars showing actual surplus deployed toward goals  
* Horizontal goal line showing required surplus (from goal pyramid)  
* Bars colored green when above the line, red/amber when below  
* **Toggle: monthly ↔ quarterly aggregation.** Quarterly smooths lumpy months.  
* Time range: last 6 months default, expandable to 12

### **Lens 2 Expanded Visualizations**

**Goal-Linked Pools: Table with Status Badges**

| Goal | Pool Value | XIRR | Required Return | Projected | Target | Status |
| ----- | ----- | ----- | ----- | ----- | ----- | ----- |
| House down payment | ₹14.2L | 13.1% | 11% | ₹31.5L | ₹30L | 🟢 |
| Emergency fund | ₹3.8L | 6.2% | 8% | ₹4.4L | ₹4.8L | 🟡 |

Status badge does the triage. For AT\_RISK or BEHIND rows, a link: "\[View holdings →\]" goes to the Holdings page.

**Unlinked Assets Nudge**

* Callout card if significant assets are unlinked: "₹2.3L not linked to any goal. \[Link holdings →\]"

### **Lens 3 Expanded Visualizations**

**Cash Runway: Big Number \+ Sparkline \+ Progress Bar**

* Headline: "4.8 months"  
* Subtext: "Target: 6 months (emergency fund goal)"  
* Sparkline showing 6-month trend  
* Progress bar: 80% filled

**Liquidity Ladder: Horizontal Stacked Bar**

* Single bar, segments by time-to-access: Instant | T+1-3 | Weeks | Illiquid  
* Hover/click shows amount per segment  
* Connected to goal execution readiness when relevant

**Upcoming Obligations: 90-Day List**

* Simple list with date markers and amounts  
* Total committed outflows and coverage ratio

**Debt Service Coverage: Single Number**

* "₹26K/month \= 13% of income. Healthy."  
* Low prominence now; gains prominence post-home-loan

### **Lens 4 Expanded Visualizations**

**Goal Pyramid Health: Summary Bar**

* Horizontal bar: green (on track) | amber (at risk) | red (behind)  
* Click expands to list of flagged goals with one-line explanations

**Allocation Gap: Bar**

* Total surplus → allocated chunks → unallocated remainder  
* If unallocated \> 0: "₹20K unallocated. Highest-priority underfunded goal: emergency fund."

---

## **4 · Financial Ratios**

All financial ratios are displayed on the Statements page (`/statements`) as a unified set. They are not split across pages. The health page surfaces goal-connected *signals* (surplus vs required, pool XIRR vs required return, cash runway vs target) but does not display formal ratios.

The full ratios reference (savings rate, needs:wants, capital contribution ratio, cash runway, debt service coverage, liquidity ratio, portfolio XIRR, goal velocity) is defined in the Statements Page guideline document.

---

*This document is standalone and covers only the Financial Health Dashboard (`/health`). Separate guideline documents exist for the Holdings Page (`/portfolio`) and the Statements Page (`/statements`). This page does not duplicate content from those pages — it synthesizes, triages, and links to them for detail.*

