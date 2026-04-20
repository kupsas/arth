/**
 * Human-facing, verb-first labels for every agent tool.
 *
 * Each entry has a ``noun`` phrase and a set of ``verbs`` that are grammatically
 * correct with it (e.g. "Diving into" needs "your goals", not "calculate").
 * A lightweight hash of (toolName + position) picks one verb deterministically —
 * the result looks varied and natural but never flickers on re-render.
 */

interface ToolCopy {
  /** The object phrase that follows the verb. Starts lower-case; verb capitalises first word. */
  noun: string;
  /** Pre-formatted verb phrases that read naturally when prepended to ``noun``. */
  verbs: readonly string[];
}

/**
 * Exact ``name=`` values from ``agent/tools/*.py``.
 * Verb sets are curated so every combination is grammatically sound.
 */
const TOOL_COPY: Record<string, ToolCopy> = {
  get_user_profile: {
    noun: "your financial picture",
    verbs: ["Pulling up", "Loading", "Assembling", "Fetching"],
  },
  get_date_context: {
    noun: "today's date & calendar",
    verbs: ["Checking", "Confirming", "Looking up", "Fetching"],
  },
  get_spending_summary: {
    noun: "your spending totals",
    verbs: ["Adding up", "Tallying", "Reviewing", "Calculating"],
  },
  get_spending_by_category: {
    noun: "your spending by category",
    verbs: ["Breaking down", "Analyzing", "Grouping", "Reviewing"],
  },
  get_spending_trends: {
    noun: "your spending trends",
    verbs: ["Analyzing", "Charting", "Tracking", "Mapping out"],
  },
  get_recurring_expenses: {
    noun: "your recurring bills",
    verbs: ["Scanning", "Pulling up", "Reviewing", "Fetching"],
  },
  search_transactions: {
    noun: "your transactions",
    verbs: ["Searching through", "Combing through", "Sifting through", "Scanning"],
  },
  get_goals_overview: {
    noun: "your goals",
    verbs: ["Scanning", "Looking over", "Reviewing", "Checking in on"],
  },
  get_goal_detail: {
    noun: "the details on this goal",
    verbs: ["Diving into", "Zooming in on", "Pulling up", "Examining"],
  },
  get_surplus_allocation: {
    noun: "your surplus allocation",
    verbs: ["Working out", "Calculating", "Analyzing", "Reviewing"],
  },
  get_goal_tree: {
    noun: "how your goals connect",
    verbs: ["Mapping out", "Tracing", "Charting", "Reviewing"],
  },
  get_net_worth: {
    noun: "your net worth",
    verbs: ["Calculating", "Computing", "Tallying up", "Working out"],
  },
  get_holdings_breakdown: {
    noun: "your holdings",
    verbs: ["Breaking down", "Digging into", "Analyzing", "Reviewing"],
  },
  get_net_worth_trend: {
    noun: "your net worth over time",
    verbs: ["Charting", "Tracking", "Analyzing", "Tracing"],
  },
  get_investment_activity: {
    noun: "your recent investment activity",
    verbs: ["Reviewing", "Scanning", "Looking over", "Fetching"],
  },
  run_projection: {
    noun: "a future projection",
    verbs: ["Running", "Computing", "Calculating", "Modeling"],
  },
  compare_scenarios: {
    noun: "your what-if scenarios",
    verbs: ["Comparing", "Weighing", "Running through", "Analyzing"],
  },
  simulate_surplus_change: {
    noun: "how a surplus change plays out",
    verbs: ["Simulating", "Modeling", "Calculating", "Working through"],
  },
};

/**
 * Tiny deterministic hash — same (name, position) always returns the same bucket,
 * so labels are stable across re-renders without any React state.
 */
function stableIndex(toolName: string, position: number, buckets: number): number {
  const seed = `${toolName}:${position}`;
  let h = 0;
  for (let i = 0; i < seed.length; i++) {
    // djb2-style: multiply + XOR
    h = ((h << 5) - h + seed.charCodeAt(i)) >>> 0;
  }
  return h % buckets;
}

/**
 * Returns a varied, grammatically-correct verb phrase for a tool call.
 *
 * @param rawName  - the backend tool name, e.g. ``"get_spending_trends"``
 * @param position - index of this tool in the list (0-based); drives variety
 */
export function formatToolLabel(rawName: string, position = 0): string {
  const key = rawName.trim();
  if (!key) return "Looking something up";

  const copy = TOOL_COPY[key];

  if (copy) {
    const verb = copy.verbs[stableIndex(key, position, copy.verbs.length)];
    // Capitalise first character of the full phrase (verb is already capitalised).
    return `${verb} ${copy.noun}`;
  }

  // Graceful fallback for new / unknown tools.
  let stem = key;
  const prefixes = ["get_", "fetch_", "list_", "run_", "simulate_", "compare_", "search_"] as const;
  for (const p of prefixes) {
    if (stem.startsWith(p)) { stem = stem.slice(p.length); break; }
  }
  const phrase = stem.replace(/_/g, " ").replace(/\s+/g, " ").trim().toLowerCase();
  if (!phrase) return "Looking something up";

  const fallbackVerbs = ["Fetching", "Loading", "Reviewing", "Pulling up"];
  const verb = fallbackVerbs[stableIndex(key, position, fallbackVerbs.length)];
  return `${verb} ${phrase}`;
}
