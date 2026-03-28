# **Arth — Holdings Page Guideline Document**

**Created:** 26 Mar 2026 **Purpose:** Design guideline for the upgraded `/portfolio` holdings page. This replaces the existing F2 portfolio page with a richer, public-facing holdings view. **Context:** Workstream A (Asset Data Foundation) and F2 (Portfolio Dashboard) are complete. This document defines the upgraded holdings page that serves as the primary "what do I own and how's it doing?" view. No goal linkage is required on this page — it serves all users, from casual investors to goal-based planners.

---

## **1 · Page Purpose**

The Holdings page answers three questions depending on what the user needs:

1. **"Am I making money?"** — Portfolio P\&L at a glance  
2. **"What do I own and how's it performing?"** — Inventory with performance metrics  
3. **"Where should I put my next rupee?"** — Allocation and concentration visibility

---

## **2 · Page Structure**

┌─────────────────────────────────────────────────────┐  
│  TOP SECTION                                         │  
│  Portfolio Value (headline) \+ Portfolio Value Trend   │  
│  Asset Allocation Donut \+ Summary Table              │  
├─────────────────────────────────────────────────────┤  
│  EQUITIES SECTION (expandable, deep)                 │  
│  Grouping toggle \+ Pie chart \+ Best Gains/Drags      │  
│  Detailed holdings table                             │  
├─────────────────────────────────────────────────────┤  
│  MUTUAL FUNDS SECTION (expandable, deep)             │  
│  Grouping toggle \+ Pie chart                         │  
│  Detailed holdings table                             │  
├─────────────────────────────────────────────────────┤  
│  OTHER ASSET CLASSES (expandable, simple)            │  
│  FD, PPF, NPS, Gold, SGBs, Corporate Bonds           │  
│  Simple table rows per asset class                   │  
└─────────────────────────────────────────────────────┘

---

## **3 · Top Section**

### **3.1 Portfolio Value Headline**

* **Total Portfolio Value** — large, prominent number (e.g., ₹14,47,988)  
* **Day's Change** — ₹ and %, color-coded green/red  
* **Overall Change** — ₹ and % from total cost basis, color-coded green/red  
* **As of date** — "as on 26-Mar-2026"

### **3.2 Portfolio Value Trend — Area Chart**

* **Chart type:** Area chart with gradient fill (darker at the line, fading to transparent at the bottom). Accent color — not green/red coded, just a neutral portfolio color.  
* **Data points:** Monthly consolidated portfolio value with percentage change labels on each data point on hover (like the NSDL CAS style: "+31.57%", "-19.51%").  
* **Time range selector:** Pill toggles: `1M | 3M | 6M | 12M | All`  
  * Default: 12M  
  * 1M: daily data points if available, otherwise weekly  
  * 3M/6M: monthly data points  
  * 1Y/All: monthly data points  
* **Data source:** Monthly portfolio values can be computed from holdings × prices for any historical date.

### **3.3 Asset Allocation Donut**

* **Chart type:** Donut/pie chart showing portfolio value breakdown by asset class  
* **Segments:** Equities, Mutual Funds, Corporate Bonds, SGBs, NPS, FDs, PPF, Gold, etc.  
* **Each segment shows:** Value in ₹ and percentage of total portfolio  
* **Position:** Alongside the summary table (side by side on desktop)

### **3.4 Summary Table**

ICICI Direct style. One row per asset class with non-zero holdings:

| Column | Description |
| ----- | ----- |
| **Product** | Asset class name (Stocks, Mutual Fund, FD, Corporate Bonds, NPS, etc.) |
| **Investments** | Total cost basis / principal invested |
| **Current Value** | Current market value |
| **Day's Gain** | Today's change in ₹ |
| **Day's Gain %** | Today's change as percentage |
| **Overall Gain** | Total gain/loss in ₹ (current value − investments) |
| **Overall Gain %** | Total gain/loss as percentage |

* All gain/loss columns color-coded green (positive) / red (negative)  
* Each row is clickable — scrolls to or expands the corresponding asset class section below

---

## **4 · Equities Section (Deep)**

### **Note:** if the page is opened during trading hours (0830 \- 1600 IST): "Market currently in trading hours. Current market price will be updated at the end of the day. Please check later prices below denote yesterday's values."

### **4.1 Section Header**

* **Total Equity Value** — headline number for equities only  
* **Day's Change** and **Overall Change** for the equity sub-portfolio

### **4.2 Grouping Toggle**

Pill selector: `Sector (default) | Market Cap | Holding Period`

* **Sector:** Groups holdings by industry/sector (Engineering, Paints, Power, Refineries, etc.). Matches the sector classification from the holdings data. Populate the sector classification in the holdings data if not done. Use official NSE classification.  
* **Market Cap:** Groups holdings by Large Cap / Mid Cap / Small Cap. Requires market cap classification on each equity holding. Populate the market cap classification in the holdings data if not done. Use official NSE classification.  
* **Holding Period:** Groups holdings by LTCG eligibility — "Held \> 1 Year" (LTCG eligible, 10% tax above ₹1L) vs "Held \< 1 Year" (STCG, 15% tax). Directly relevant for tax-aware sell decisions.

When toggled, both the pie chart and the holdings table regroup in place. Same data, different lens.

### **4.3 Grouping Pie Chart**

* **Chart type:** Donut/pie chart showing total equity value split by the selected grouping  
* **Segments:** One per group (e.g., per sector, per market cap band, per holding period bucket)  
* **Each segment:** Value in ₹ and percentage of equity portfolio  
* **Updates dynamically** when grouping toggle changes

### **4.4 Best Gains & Biggest Drags**

Two compact cards side by side:

**"Best gains"**

* Shows up to 3 holdings with the highest overall gain %  
* Condition: Only holdings where overall P\&L is positive (in the green)  
* If fewer than 3 holdings are in the green, show however many qualify  
* If no holdings are in the green, this card shows an empty state  
* Format per holding: Symbol, up arrow icon, Overall Gain %

**"Biggest drags"**

* Shows up to 3 holdings with the worst overall gain % (most negative)  
* Condition: Only holdings where overall P\&L is negative (in the red)  
* If fewer than 3 holdings are in the red, show however many qualify  
* If no holdings are in the green, this card shows an empty state  
* Format per holding: Symbol, down arrow icon, Overall Loss %

### **4.5 Detailed Holdings Table**

Grouped by the selected grouping toggle, with group subtotals (like ICICI Direct).

| Column | Description |
| ----- | ----- |
| **Stock Symbol** | Ticker symbol (clickable for detail view in future) |
| **Qty** | Total quantity held |
| **Avg Cost Price** | Average purchase price per unit |
| **CMP** | Current market price |
| **% Change** | Today's price change |
| **Value at Cost** | Qty × Avg Cost Price |
| **Value at CMP** | Qty × CMP (current value) |
| **Unrealized P\&L — Day's** | Today's unrealized gain/loss in ₹ |
| **Unrealized P\&L — Overall** | Total unrealized gain/loss in ₹ |
| **Profit/Loss %** | Overall unrealized P\&L as percentage |

**Additional data visible on expand/hover per holding:**

* **LTCG Eligibility** — Flag: "Held \> 1 Year" or "Held \< 1 Year" with number of days/months held  
* **Market Cap** — Large / Mid / Small cap classification  
* **Weight** — This holding as a percentage of the total equity portfolio (concentration indicator)

**Group subtotals:** Each group (sector, market cap band, or holding period bucket) shows a subtotal row with summed Value at Cost, Value at CMP, Day's P\&L, and Overall P\&L.

**Table total:** Bottom row shows totals across all equity holdings.

---

## **5 · Mutual Funds Section (Deep)**

### **Note:** if the page is opened during trading hours: "Market currently in trading hours (0830 \- 1600 IST). Current market price will be updated at the end of the day. Please check later. Prices below denote yesterday's values."

### **5.1 Section Header**

* **Total MF Value** — headline number for mutual funds only  
* **Overall Change** for the MF sub-portfolio

### **5.2 Grouping Toggle**

Pill selector: `Fund Category (default) | Fund House`

* **Fund Category:** Groups by SEBI classification (Large Cap, Mid Cap, Small Cap, Flexi Cap, ELSS, Sectoral, Debt, Hybrid, Liquid, etc.)  
* **Fund House:** Groups by AMC (HDFC, Axis, SBI, ICICI Prudential, Kotak, etc.)

### **5.3 Grouping Pie Chart**

* Same behavior as equities pie chart — donut showing MF value split by selected grouping  
* Segments show value in ₹ and percentage of MF portfolio  
* Updates dynamically when toggle changes

### **5.4 Detailed Holdings Table**

Grouped by the selected grouping toggle, with group subtotals.

| Column | Description |
| ----- | ----- |
| **Fund Name** | Full fund name |
| **Folio** | Folio number |
| **Fund Category** | SEBI classification |
| **Invested Amount** | Total cost basis |
| **Current Value** | Current NAV × units |
| **XIRR** | Annualized return accounting for SIP timing |
| **Overall Gain (₹)** | Current value − invested amount |
| **Overall Gain %** | Gain as percentage |
| **Units** | Number of units held |
| **NAV** | Current net asset value per unit |

**Additional data visible on expand/hover per holding:**

* **Realized P\&L** — Gains/losses from redeemed units  
* **SIP Details** — If active SIP: monthly amount, start date, number of installments. If no SIP: "Lump sum"  
* **Fund House** — AMC name (when not grouped by fund house)

**Group subtotals:** Each group shows summed Invested Amount, Current Value, and Overall Gain.

---

## **6 · Other Asset Classes (Simple Rows)**

Each additional asset class with non-zero holdings gets a simple expandable section with a basic table. No grouping toggles, no pie charts — these are straightforward instruments with limited decision surface.

### **FDs (Fixed Deposits)**

| Column | Description |
| ----- | ----- |
| **Name / Bank** | Description and issuing bank |
| **Principal** | Amount deposited |
| **Interest Rate** | Annual rate |
| **Maturity Date** | When the FD matures |
| **Current Value** | Principal \+ accrued interest (if computable) |
| **Tenure** | Total duration |

### **PPF (Public Provident Fund)**

| Column | Description |
| ----- | ----- |
| **Account** | Bank / post office |
| **Total Contributions** | Cumulative deposits |
| **Current Balance** | Balance including interest |
| **Interest Rate** | Current government rate |
| **Maturity Date** | 15-year lock-in end date (Compute from our first transaction date) |

### **NPS (National Pension System)**

| Column | Description |
| ----- | ----- |
| **PRAN** | NPS account number |
| **Total Contributions** | Cumulative deposits |
| **Current Value** | Current corpus value |
| **Overall Gain (₹ / %)** | Appreciation |
| **Asset Allocation** | Equity / Corporate Bonds / Govt Securities split (if available) |

### **Gold / SGBs (Sovereign Gold Bonds)**

| Column | Description |
| ----- | ----- |
| **Name / Series** | SGB series or gold holding description |
| **Quantity** | Grams or units |
| **Purchase Price** | Cost per unit/gram |
| **Value at Purchase** | Total amount invested |
| **Current Price** | Live gold price or SGB NAV |
| **Value at CMP** | Today’s value |
| **Unrealized P\&L — Overall** | Total unrealized gain/loss in ₹ |
| **Realized P\&L — Interest/Others** | Realized gain/loss in ₹ |
| **Maturity Date** | For SGBs: 8-year maturity with 5-year exit option |
| **Coupon** | For SGBs: 2.5% semi-annual interest |

### **Corporate Bonds / Debentures**

| Column | Description |
| ----- | ----- |
| **Name / Issuer** | Bond description |
| **Face Value** | Par value |
| **Purchase Price** | What was paid |
| **Current Value** | Market value |
| **Coupon Rate** | Interest rate |
| **Maturity Date** | When the bond matures |
| **Overall Gain (₹ / %)** | Appreciation |

---

## **7 · Data Sources**

| Data | Source | Notes |
| ----- | ----- | ----- |
| Holdings (all asset classes) | `holdings` table (Workstream A) | All current positions |
| Daily prices (equities, MFs) | `prices` table (Yahoo Finance daily feed) | For CMP, day's change, and portfolio value computation |
| Cost basis / purchase history | `investment_transactions` table | For avg cost price, XIRR, realized P\&L |
| Market cap classification | To be added to `holdings` table | Large / Mid / Small based on current market cap |
| Sector classification | To be added to `holdings` table | Industry/sector for equity grouping |
| SEBI fund category | To be added to `holdings` table | For MF grouping by category |
| Fund house / AMC | To be added to `holdings` table | For MF grouping by fund house |

---

## **8 · Design Principles**

1. **Charts over numbers.** Where a chart can replace a table of numbers, use the chart. The area chart, donut, and pie charts reduce cognitive load compared to scanning rows of figures.  
2. **Numbers where precision matters.** The detail tables provide the exact figures for users who want them. Charts for overview, tables for detail.  
3. **Color means something.** Green \= positive P\&L. Red \= negative P\&L. Don't use green/red for anything else on this page.  
4. **Conditional rendering.** Best gains card only shows when holdings are green. Biggest drags only shows when holdings are red. Empty sections are hidden, not shown as "no data."  
5. **Grouping is a lens, not a filter.** Toggling between sector/market cap/holding period shows the same holdings organized differently. No data is hidden.  
6. **Progressive disclosure.** Summary table → expandable section → detail table → expand/hover for more. Each level adds depth without forcing it on the user.

---

*This document is standalone and covers only the Holdings page (`/portfolio`). Separate guideline documents exist for the Statements page (`/statements`) and the Financial Health Dashboard (`/health`).*

