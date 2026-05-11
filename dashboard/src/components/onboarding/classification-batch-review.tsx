"use client";

/**
 * Inline classification queue for onboarding (shown under **Import mail**).
 *
 * **Beginner flow**
 * 1. We load one *page* of rows from ``GET /api/onboarding/unknowns`` (oldest first — new rows join at the bottom): anything
 *    still missing counterparty or category, **plus** LLM-labelled rows in sensitive categories
 *    (Friends & Family, Gifts & Personal Transfers, Miscellaneous) so you can fix common mis-tags
 *    (unless you already confirmed that counterparty on another transaction).
 *    Omit ``source`` on this component to review **all** email-linked accounts in one queue.
 * 2. Pick a **category** per row (or select many rows and use the bulk bar).
 * 3. **Confirm** sends ``POST /api/onboarding/classify`` (no ``source`` when mixed), then for each
 *    affected ``source_key`` opens ``GET /api/onboarding/backfill/{source}/stream?resume_after_classification=true``
 *    so mail import continues with live SSE progress (same resume semantics as the POST chunk API).
 * 4. While the mail importer is **paused for classification** (``needs_classification``), after **every
 *    email source** has finished (``complete`` on the last source but rows still need labels), or while
 *    reviewing **statement upload** rows (``pipelineRunId``), an optional **“rest of queue = Uber”**
 *    shortcut fetches *every* pending unknown (not only the visible page), sets counterparty **Uber** +
 *    **Transport & Fuel**, and saves in chunks (with a destructive confirm). Statement runs use
 *    ``POST /api/pipeline/runs/{id}/classify`` instead of the onboarding classify route.
 * 5. While the parent is **actively pulling mail** (``processing*`` statuses), a translucent overlay
 *    explains that the queue is temporarily read-only so saving labels does not fight the importer.
 *
 * **Selection** is stored as a ``Set`` of transaction ids so it survives page changes.
 */

import * as React from "react";
import { Check, ChevronLeft, ChevronRight, Loader2, Pencil } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  ClassifierPausedApiError,
  fetchOnboardingUnknowns,
  fetchPipelineRunUnknowns,
  postOnboardingClassify,
  postPipelineRunClassify,
  streamOnboardingBackfill,
  type OnboardingClassifyItem,
  type OnboardingUnknownTxnBrief,
  type ProviderFailurePayload,
} from "@/lib/api";
import { COUNTERPARTY_CATEGORY_OPTIONS } from "@/lib/counterparty-categories";
import {
  guardSingleLineText,
  ONBOARDING_INPUT_LIMITS,
} from "@/lib/onboarding-input-validation";
import { humanizeSourceKey } from "@/lib/source-label";
import { getUserFacingErrorMessage } from "@/lib/user-facing-api-error";
import type { CounterpartyCategory } from "@/lib/types";
import { cn } from "@/lib/utils";

/** Matches onboarding UX: short pages + outer page scroll (no nested scroll panes). */
const PAGE_SIZE = 10;

/** Server allows up to 500 rows per ``GET /api/onboarding/unknowns`` — use it when draining the whole queue. */
const UNKNOWN_FULL_PAGE = 500;

/** Keep each ``POST /api/onboarding/classify`` body reasonably small for slow networks / proxies. */
const CLASSIFY_CHUNK_SIZE = 120;

/** Values must match ``COUNTERPARTY_CATEGORY_OPTIONS`` exactly (API stores the string as-is). */
const UBER_QUEUE_COUNTERPARTY = "Uber";
const UBER_QUEUE_CATEGORY: CounterpartyCategory = "Transport & Fuel";

/**
 * Walks unknown pages until empty so “mark rest as Uber” covers rows beyond the UI page size.
 */
async function fetchAllUnknownRowsInQueue(
  scopedSource: string | undefined,
  pipelineRunId?: number | null,
): Promise<OnboardingUnknownTxnBrief[]> {
  const acc: OnboardingUnknownTxnBrief[] = [];
  let offset = 0;
  while (true) {
    const data =
      pipelineRunId != null && pipelineRunId > 0
        ? await fetchPipelineRunUnknowns(pipelineRunId, {
            limit: UNKNOWN_FULL_PAGE,
            offset,
          })
        : await fetchOnboardingUnknowns({
            source: scopedSource,
            limit: UNKNOWN_FULL_PAGE,
            offset,
          });
    if (!data.transactions.length) break;
    acc.push(...data.transactions);
    if (data.transactions.length < UNKNOWN_FULL_PAGE) break;
    offset += UNKNOWN_FULL_PAGE;
    if (acc.length > data.pending_total + UNKNOWN_FULL_PAGE) break;
  }
  return acc;
}

/**
 * Radix ``Select`` treats ``value={undefined}`` as *uncontrolled*. If we later pass a string,
 * React warns about switching to controlled. We always pass a string and map this sentinel to
 * "no category chosen" in our own state (empty string).
 */
const SELECT_NONE = "__none__" as const;

/** Fallback label when the bank narration has no parsed counterparty yet. */
function defaultCounterpartyLabel(t: OnboardingUnknownTxnBrief): string {
  const c = t.counterparty?.trim();
  if (c) return c;
  const raw = (t.raw_description || "").trim();
  if (raw.length <= 48) return raw || "Unknown";
  return `${raw.slice(0, 45)}…`;
}

export type ClassificationBatchReviewProps = {
  /** Pipeline ``source_key``; omit to load unknowns across every email source. */
  source?: string;
  /** Optional heading override when ``source`` is set. */
  sourceLabel?: string;
  /**
   * When set, this queue reads ``GET /api/pipeline/runs/{id}/unknowns`` (statement upload import).
   * Do not pass ``source`` for API purposes — email unknowns use ``/api/onboarding/unknowns`` instead.
   */
  pipelineRunId?: number | null;
  /**
   * Live ``unknowns_pending`` from the parent's backfill progress poll. When this value
   * changes (e.g. from 0 → 28), the component re-fetches its page so newly-discovered
   * unknowns appear without a manual reload.
   *
   * The parent may also pass a **string** (e.g. ``status + source index``): progress
   * ``unknowns_pending`` is per active source, while this list is often **all** email
   * sources — when the last source hits ``complete`` the numeric pending can stay ``0``
   * even though the combined queue still has rows, so the string key forces a refetch.
   */
  unknownsTrigger?: number | string;
  /** After classify + SSE resume — parent may refresh backfill UI via ``onImportProgress``. */
  onSubmitted?: () => void
  /**
   * Called once when, after a successful save and list refresh, **no** rows remain in this queue
   * (``pending_total === 0``). Use this to hide a statement-upload review card without breaking the
   * email wizard (which still uses ``onSubmitted`` on every save for ``bfTick``).
   */
  onQueueCleared?: () => void;
  /** Forward live import counters while ``streamOnboardingBackfill`` runs after classification resume. */
  onImportProgress?: (snapshot: Record<string, unknown>) => void;
  /**
   * When true (e.g. backfill progress ``status === "needs_classification"``), show the
   * **mark entire queue as Uber** shortcut so users can bulk-fix counterparty + category together.
   */
  importAwaitingClassification?: boolean;
  /**
   * When true, chunk import has finished for **all** wizard email sources (last source ``complete``).
   * The Uber bulk shortcut still applies to any remaining global queue rows — same as mid-import pause.
   */
  allMailSourcesImported?: boolean;
  /**
   * When true, the Gmail importer is **actively parsing mail into the ledger** (not paused for
   * classification/password). We dim the queue so saves do not race with ingestion — see parent
   * ``mailImportActivelyProcessing`` (SSE-aware; not the same as “HTTP stream connected”).
   */
  mailImportActivelyProcessing?: boolean;
  /**
   * While the next source’s SSE stream has not produced a progress snapshot yet, the parent may
   * hide transaction rows if the **global** unknown backlog is still under the pause threshold
   * so the card does not flash rows under a “Connecting…” import header.
   */
  hideClassificationRowsForImportLimbo?: boolean;
  /**
   * Same numeric threshold the server uses to pause for classification (~20). Used for the Uber
   * bulk shortcut and copy when the list scope is all accounts.
   */
  pauseThresholdForShortcuts?: number;
  /**
   * Last meaningful progress snapshot from the wizard (sticky). Used to populate a synthetic
   * "processing" event with real counters so the progress card doesn't flash zero when
   * classification is confirmed and the resume stream hasn't connected yet.
   */
  lastKnownProgress?: Record<string, unknown> | null;
  /**
   * Called whenever this queue's ``pending_total`` changes (initial fetch, after saves, bulk Uber).
   * Use to sync external UI (e.g. the statement upload summary) with the live review backlog.
   */
  onPendingTotalChange?: (pendingTotal: number) => void;
  /**
   * Resume-after-classify SSE hit ``classifier_paused`` — every smart-label provider failed.
   * Parent shows ``ProviderPausedDialog`` so the user can fix keys or retry.
   */
  onClassifierImportPaused?: (failures: ProviderFailurePayload[]) => void;
};

export function ClassificationBatchReview({
  source,
  sourceLabel,
  pipelineRunId = null,
  unknownsTrigger,
  onSubmitted,
  onQueueCleared,
  onImportProgress,
  importAwaitingClassification = false,
  allMailSourcesImported = false,
  mailImportActivelyProcessing = false,
  hideClassificationRowsForImportLimbo = false,
  pauseThresholdForShortcuts,
  lastKnownProgress = null,
  onPendingTotalChange,
  onClassifierImportPaused,
}: ClassificationBatchReviewProps) {
  const scopedSource = source?.trim() || undefined;
  const reviewRunId = pipelineRunId != null && pipelineRunId > 0 ? pipelineRunId : null;
  const statementRunMode = reviewRunId != null;
  const displayScope =
    sourceLabel?.trim() ||
    (statementRunMode
      ? "This statement import"
      : scopedSource
        ? humanizeSourceKey(scopedSource)
        : "All email accounts");

  const [page, setPage] = React.useState(0);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [rows, setRows] = React.useState<OnboardingUnknownTxnBrief[]>([]);
  const [pendingTotal, setPendingTotal] = React.useState(0);
  const [threshold, setThreshold] = React.useState<number | null>(null);

  /** Per-txn category pick (required before confirm). */
  const [categoryById, setCategoryById] = React.useState<Record<number, CounterpartyCategory | "">>({});
  /** Editable merchant / counterparty label we send to the API. */
  const [counterpartyById, setCounterpartyById] = React.useState<Record<number, string>>({});
  /** ``source_statement`` for each id we have seen (used for resume + mixed classify). */
  const [sourceById, setSourceById] = React.useState<Record<number, string>>({});

  const [selectedIds, setSelectedIds] = React.useState<Set<number>>(() => new Set());
  const [bulkCategory, setBulkCategory] = React.useState<string>(SELECT_NONE);
  const [editingCpId, setEditingCpId] = React.useState<number | null>(null);

  const [busy, setBusy] = React.useState(false);

  /** Mirrors the checkbox while the destructive-confirm dialog is open (Base UI controlled checkbox). */
  const [uberDialogOpen, setUberDialogOpen] = React.useState(false);

  /** Zero when the queue is empty so we can hide pagination chrome entirely. */
  const totalPages =
    pendingTotal > 0 ? Math.max(1, Math.ceil(pendingTotal / PAGE_SIZE)) : 0;

  const onPendingTotalChangeRef = React.useRef(onPendingTotalChange);
  onPendingTotalChangeRef.current = onPendingTotalChange;
  React.useEffect(() => {
    if (busy || loading) return;
    onPendingTotalChangeRef.current?.(pendingTotal);
  }, [pendingTotal, busy, loading]);

  /** Anchor for scrolling back to the list top whenever ``page`` changes (see effect below). */
  const listTopRef = React.useRef<HTMLDivElement>(null);
  const prevPageForScrollRef = React.useRef<number | undefined>(undefined);

  const mergeRowState = React.useCallback((txns: OnboardingUnknownTxnBrief[]) => {
    setCategoryById((prev) => {
      const next = { ...prev };
      for (const t of txns) {
        if (next[t.id] === undefined) {
          next[t.id] = (t.counterparty_category as CounterpartyCategory) ?? "";
        }
      }
      return next;
    });
    setCounterpartyById((prev) => {
      const next = { ...prev };
      for (const t of txns) {
        if (next[t.id] === undefined) {
          next[t.id] = guardSingleLineText(
            defaultCounterpartyLabel(t),
            ONBOARDING_INPUT_LIMITS.counterpartyLabelChars,
          );
        }
      }
      return next;
    });
    setSourceById((prev) => {
      const next = { ...prev };
      for (const t of txns) {
        if (t.source_statement) next[t.id] = t.source_statement;
      }
      return next;
    });
  }, []);

  /** Same reload path as after a successful classify — keeps pagination coherent when the last page empties. */
  const reloadQueueAfterMutation = React.useCallback(async (): Promise<number> => {
    setLoading(true);
    let lastPending = 0;
    try {
      let pi = page;
      while (true) {
        const offset = pi * PAGE_SIZE;
        const data = statementRunMode
          ? await fetchPipelineRunUnknowns(reviewRunId!, {
              limit: PAGE_SIZE,
              offset,
            })
          : await fetchOnboardingUnknowns({
              source: scopedSource,
              limit: PAGE_SIZE,
              offset,
            });
        setThreshold(data.unknown_threshold);
        setPendingTotal(data.pending_total);
        lastPending = data.pending_total;
        const rowBatch = data.transactions;
        if (rowBatch.length || offset === 0 || data.pending_total === 0) {
          if (pi !== page) setPage(pi);
          setRows(rowBatch);
          mergeRowState(rowBatch);
          break;
        }
        pi -= 1;
      }
    } catch (e) {
      setError(getUserFacingErrorMessage(e) || "Couldn't refresh the list.");
    } finally {
      setLoading(false);
    }
    return lastPending;
  }, [page, scopedSource, mergeRowState, statementRunMode, reviewRunId]);

  React.useEffect(() => {
    queueMicrotask(() => {
      setSelectedIds(new Set());
      setPage(0);
    });
  }, [scopedSource, reviewRunId]);

  React.useEffect(() => {
    let cancelled = false;
    async function runFetch() {
      setLoading(true);
      setError(null);
      try {
        let pi = page;
        // If this page is empty but work remains (e.g. after bulk-clear on the last page), walk back.
        while (true) {
          const offset = pi * PAGE_SIZE;
          const data = statementRunMode
            ? await fetchPipelineRunUnknowns(reviewRunId!, {
                limit: PAGE_SIZE,
                offset,
              })
            : await fetchOnboardingUnknowns({
                source: scopedSource,
                limit: PAGE_SIZE,
                offset,
              });
          if (cancelled) return;
          setThreshold(data.unknown_threshold);
          setPendingTotal(data.pending_total);
          const rowBatch = data.transactions;
          if (rowBatch.length || offset === 0 || data.pending_total === 0) {
            if (pi !== page) setPage(pi);
            setRows(rowBatch);
            mergeRowState(rowBatch);
            break;
          }
          pi -= 1;
        }
      } catch (e) {
        if (!cancelled) {
          setError(getUserFacingErrorMessage(e) || "Couldn't load transactions to review.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void runFetch();
    return () => {
      cancelled = true;
    };
  }, [page, scopedSource, mergeRowState, unknownsTrigger, statementRunMode, reviewRunId]);

  /**
   * After Prev/Next, bring the review list back into view so you land on row 1 of the new page,
   * not halfway down the wizard. Skip the first run so initial load does not jump the viewport.
   */
  React.useEffect(() => {
    const prev = prevPageForScrollRef.current;
    prevPageForScrollRef.current = page;
    if (prev === undefined || prev === page) return;
    requestAnimationFrame(() => {
      listTopRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }, [page]);

  function toggleOne(id: number) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function buildItem(txnId: number, category: string): OnboardingClassifyItem | null {
    const cat = category.trim();
    if (!cat) return null;
    const raw = (counterpartyById[txnId] ?? "").trim();
    const cp = guardSingleLineText(raw, ONBOARDING_INPUT_LIMITS.counterpartyLabelChars).trim();
    if (!cp) return null;
    return {
      txn_id: txnId,
      counterparty: cp,
      counterparty_category: cat,
      apply_to_future: true,
      merchant_rule_keyword: null,
    };
  }

  async function flushClassifyAndResume(
    items: OnboardingClassifyItem[],
    resolveSource: (txnId: number) => string | undefined,
  ) {
    if (statementRunMode) {
      return postPipelineRunClassify(reviewRunId!, { items });
    }
    const result = await postOnboardingClassify({
      source: scopedSource ?? null,
      items,
    });

    if (result.should_resume) {
      const sources = new Set<string>();
      for (const it of items) {
        const sk = resolveSource(it.txn_id);
        if (sk) sources.add(sk);
      }
      // Signal the progress card immediately so it transitions from
      // "Waiting for your review" to "Importing statement emails" before the
      // resume stream connects (avoids 2-5s of visual stall). Carry forward
      // the last known counters so the card doesn't flash zeros.
      for (const sk of sources) {
        onImportProgress?.({
          ...(lastKnownProgress ?? {}),
          source: sk,
          status: "processing_statements",
        } as Record<string, unknown>);
      }
      for (const sk of sources) {
        await streamOnboardingBackfill(sk, {
          resume_after_classification: true,
          onProgress: (snap) => onImportProgress?.(snap),
        });
      }
    }
    return result;
  }

  async function submitItems(items: OnboardingClassifyItem[]) {
    if (!items.length) return;
    setBusy(true);
    setError(null);
    // Optimistic: clear rows immediately so the UI feels instant.
    setRows([]);
    setPendingTotal(0);
    setSelectedIds(new Set());
    setBulkCategory(SELECT_NONE);
    try {
      await flushClassifyAndResume(items, (id) => sourceById[id]);
      onSubmitted?.();
      const remaining = await reloadQueueAfterMutation();
      if (remaining === 0) {
        onQueueCleared?.();
      }
    } catch (e) {
      if (e instanceof ClassifierPausedApiError && e.failures.length) {
        onClassifierImportPaused?.(e.failures);
      } else {
        setError(getUserFacingErrorMessage(e) || "Couldn't save your changes.");
      }
      // Reload the real state so the user can retry.
      void reloadQueueAfterMutation();
    } finally {
      setBusy(false);
    }
  }

  /**
   * Fetches the full unknown backlog (all pages), then classifies every row as Uber / Transport & Fuel.
   * Used after the user confirms in the destructive dialog — not reversible as a single undo.
   */
  async function onUberQueueBulkConfirmed() {
    setUberDialogOpen(false);
    setBusy(true);
    setError(null);
    // Optimistic: clear rows immediately so the UI feels instant.
    setRows([]);
    setPendingTotal(0);
    setSelectedIds(new Set());
    setBulkCategory(SELECT_NONE);
    try {
      const queueRows = await fetchAllUnknownRowsInQueue(scopedSource, reviewRunId);
      if (!queueRows.length) {
        setError("Nothing left to review in this list right now.");
        return;
      }
      const sourceLookup: Record<number, string> = {};
      for (const r of queueRows) {
        if (r.source_statement) sourceLookup[r.id] = r.source_statement;
      }
      const resolveSource = (id: number) => sourceLookup[id] ?? sourceById[id];
      const items: OnboardingClassifyItem[] = queueRows.map((r) => ({
        txn_id: r.id,
        counterparty: UBER_QUEUE_COUNTERPARTY,
        counterparty_category: UBER_QUEUE_CATEGORY,
        apply_to_future: true,
        merchant_rule_keyword: null,
      }));
      for (let i = 0; i < items.length; i += CLASSIFY_CHUNK_SIZE) {
        const slice = items.slice(i, i + CLASSIFY_CHUNK_SIZE);
        await flushClassifyAndResume(slice, resolveSource);
      }
      onSubmitted?.();
      const remaining = await reloadQueueAfterMutation();
      if (remaining === 0) {
        onQueueCleared?.();
      }
    } catch (e) {
      if (e instanceof ClassifierPausedApiError && e.failures.length) {
        onClassifierImportPaused?.(e.failures);
      } else {
        setError(getUserFacingErrorMessage(e) || "Couldn't apply that bulk change.");
      }
      void reloadQueueAfterMutation();
    } finally {
      setBusy(false);
    }
  }

  async function onConfirmRow(txnId: number) {
    const cat = categoryById[txnId] ?? "";
    const item = buildItem(txnId, cat);
    if (!item) {
      setError("Pick a category and ensure the counterparty label is not empty.");
      return;
    }
    await submitItems([item]);
  }

  async function onConfirmBulk() {
    if (bulkCategory === SELECT_NONE) {
      setError("Pick a category in the bulk bar first.");
      return;
    }
    const ids = [...selectedIds];
    const items: OnboardingClassifyItem[] = [];
    for (const id of ids) {
      const item = buildItem(id, bulkCategory);
      if (!item) {
        setError(`Row #${id}: needs a non-empty counterparty label (tap to edit).`);
        return;
      }
      items.push(item);
    }
    if (!items.length) {
      setError("Select at least one transaction.");
      return;
    }
    await submitItems(items);
  }

  /**
   * Uber bulk row: same destructive confirm for **email** queues (pause / all sources done / big
   * backlog) and for **statement run** queues (``pipelineRunId``) whenever rows remain — import
   * path already uses ``postPipelineRunClassify`` in that mode.
   */
  const pauseBar = pauseThresholdForShortcuts ?? threshold ?? 20;
  const showUberQueueBulkShortcut =
    pendingTotal > 0 &&
    !mailImportActivelyProcessing &&
    !hideClassificationRowsForImportLimbo &&
    (statementRunMode ||
      importAwaitingClassification ||
      allMailSourcesImported ||
      pendingTotal >= pauseBar);

  const showPagination =
    !hideClassificationRowsForImportLimbo && pendingTotal > 0 && totalPages > 1;

  return (
    <>
      <Dialog open={uberDialogOpen} onOpenChange={setUberDialogOpen}>
        <DialogContent showCloseButton={false} className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Mark entire list as Uber?</DialogTitle>
            <DialogDescription>
              {statementRunMode ? (
                <>
                  This applies only to <strong>{displayScope}</strong>. It is effectively{" "}
                  <strong>irreversible in bulk</strong>: about{" "}
                  <strong>{pendingTotal.toLocaleString("en-IN")}</strong> transaction
                  {pendingTotal === 1 ? "" : "s"} still in this list will be saved as counterparty{" "}
                  <strong>{UBER_QUEUE_COUNTERPARTY}</strong> and category{" "}
                  <strong>{UBER_QUEUE_CATEGORY}</strong>
                  , including rows you have not opened. We also persist <strong>apply to future</strong>{" "}
                  so similar narrations learn a merchant rule keyed on <strong>UBER</strong>. Only
                  continue if you have already fixed non-Uber/Rapido rows and everything that is clearly
                  not Uber.
                </>
              ) : (
                <>
                  This is effectively <strong>irreversible in bulk</strong>: about{" "}
                  <strong>{pendingTotal.toLocaleString("en-IN")}</strong> transaction
                  {pendingTotal === 1 ? "" : "s"} still in this list will be saved as counterparty{" "}
                  <strong>{UBER_QUEUE_COUNTERPARTY}</strong> and category{" "}
                  <strong>{UBER_QUEUE_CATEGORY}</strong>
                  , including rows you have not opened. We also persist <strong>apply to future</strong> so
                  similar narrations learn a merchant rule keyed on <strong>UBER</strong>. Only continue if
                  you have already fixed non-Uber/Rapido rows and everything that is clearly not Uber.
                </>
              )}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="-mx-4 -mb-4 flex flex-col-reverse gap-3 rounded-b-xl border-t border-border/50 bg-muted/25 px-6 pb-6 pt-5 sm:flex-row sm:justify-end sm:gap-4">
            <Button
              type="button"
              variant="outline"
              size="lg"
              className="min-h-10 px-6"
              onClick={() => setUberDialogOpen(false)}
              disabled={busy}
            >
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              size="lg"
              className="min-h-10 px-6"
              disabled={busy}
              onClick={() => void onUberQueueBulkConfirmed()}
            >
              {busy ? "Saving…" : "Yes, mark all"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Card className="border-dashed">
      <CardHeader>
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <CardTitle className="flex items-center gap-2">
            Review labels
            {loading && !rows.length ? <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /> : null}
          </CardTitle>
          {pendingTotal > 0 && !hideClassificationRowsForImportLimbo && (
            <span
              className="rounded-md border border-border bg-muted/50 px-2.5 py-1 text-sm font-medium tabular-nums text-foreground"
              title="Gaps (missing counterparty or category), auto-categorised rows in Friends & Family / Gifts & Personal Transfers / Miscellaneous (unless that counterparty was already confirmed in a prior round), and the count the importer uses before pausing."
            >
              {pendingTotal.toLocaleString("en-IN")} pending
            </span>
          )}
        </div>
        <CardDescription>
          Scope: <span className="font-medium text-foreground">{displayScope}</span>
          {" — "}
          Rows here are either missing labels or are auto-categorised Friends &amp; Family, Gifts &amp;
          Personal Transfers, or Miscellaneous (worth double-checking). Names you already saved in a
          prior round are skipped for that pattern.
          {!statementRunMode && threshold != null && (
            <>
              {" "}
              Import pauses when about {threshold} need review (lower if no optional AI key).
            </>
          )}
        </CardDescription>
      </CardHeader>
      <CardContent className="relative flex min-h-48 flex-col gap-3">
        {mailImportActivelyProcessing && !statementRunMode && (
          <div
            className="absolute inset-0 z-20 flex flex-col items-center justify-start gap-2 rounded-b-lg bg-background/70 px-5 pb-6 pt-8 text-center shadow-[inset_0_0_0_1px_hsl(var(--border)/0.35)] backdrop-blur-[3px]"
            role="status"
            aria-live="polite"
            aria-label="Import in progress"
          >
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" aria-hidden />
            <p className="max-w-sm text-sm font-medium text-foreground">
              Transactions being processed, please wait.
            </p>
            <p className="max-w-xs text-xs text-muted-foreground">
              You can confirm labels once this mail-ingest burst finishes or when import pauses for
              review.
            </p>
          </div>
        )}
        {error && <p className="text-sm text-destructive">{error}</p>}

        {hideClassificationRowsForImportLimbo && (
          <p className="text-sm text-muted-foreground" role="status">
            Connecting the next email account. The list appears when you have at least{" "}
            <span className="font-medium text-foreground tabular-nums">{pauseBar}</span> items to
            review, or when import pauses for labels.
          </p>
        )}

        {!hideClassificationRowsForImportLimbo && showUberQueueBulkShortcut && (
          <div className="rounded-lg border border-amber-500/35 bg-amber-500/10 px-4 py-3.5">
            <div className="flex gap-3">
              <Checkbox
                id="uber-queue-bulk"
                className="mt-1 shrink-0"
                checked={uberDialogOpen}
                onCheckedChange={(v) => {
                  if (v === true) setUberDialogOpen(true);
                  else setUberDialogOpen(false);
                }}
                disabled={busy}
                aria-labelledby="uber-queue-bulk-label"
              />
              {/*
                Label defaults to `display:flex` + gap between *every* child node, which splits
                plain text and <strong> into separate flex items and creates ugly “rivers” of space.
                One <span> keeps the sentence normal inline flow.
              */}
              <Label
                id="uber-queue-bulk-label"
                htmlFor="uber-queue-bulk"
                className="min-w-0 flex-1 cursor-pointer font-normal leading-snug text-foreground"
              >
                <span className="block text-pretty text-sm leading-relaxed">
                  I finished fixing non-Uber/Rapido rows and am ready to mark{" "}
                  <strong>every remaining row in this queue</strong> as <strong>Uber</strong> (
                  {UBER_QUEUE_CATEGORY}).
                </span>
              </Label>
            </div>
          </div>
        )}

        {!hideClassificationRowsForImportLimbo && selectedIds.size > 0 && (
          <div className="sticky top-0 z-10 rounded-lg border bg-card/95 p-3 shadow-sm backdrop-blur">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-medium tabular-nums">{selectedIds.size} selected</span>
              <Label className="sr-only" htmlFor="bulk-cat">
                Category for selected
              </Label>
              <Select
                value={bulkCategory}
                onValueChange={(v) => setBulkCategory(v ?? SELECT_NONE)}
              >
                <SelectTrigger id="bulk-cat" className="h-8 w-full max-w-[220px] text-xs">
                  <SelectValue placeholder="Bulk category" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={SELECT_NONE} className="text-muted-foreground">
                    Choose category…
                  </SelectItem>
                  {COUNTERPARTY_CATEGORY_OPTIONS.map((c) => (
                    <SelectItem key={c} value={c}>
                      {c}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button type="button" size="sm" disabled={busy} onClick={() => void onConfirmBulk()}>
                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : "Confirm"}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                disabled={busy}
                onClick={() => {
                  setSelectedIds(new Set())
                  setBulkCategory(SELECT_NONE)
                }}
              >
                Clear
              </Button>
            </div>
          </div>
        )}

        {!hideClassificationRowsForImportLimbo && !rows.length && !loading && (
          <p className="text-sm text-muted-foreground">No rows to review on this page — you are clear.</p>
        )}

        {!hideClassificationRowsForImportLimbo && !!rows.length && (
          <>
            <div
              ref={listTopRef}
              className="space-y-2 scroll-mt-6"
            >
              {showPagination && (
                <nav
                  aria-label="Review queue pages (top)"
                  className="flex flex-wrap items-center justify-end gap-2"
                >
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={busy || page <= 0}
                    onClick={() => setPage((p) => Math.max(0, p - 1))}
                  >
                    <ChevronLeft className="h-4 w-4" />
                    Prev
                  </Button>
                  <span className="text-xs tabular-nums text-muted-foreground">
                    {page + 1} / {totalPages}
                  </span>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={busy || page >= totalPages - 1}
                    onClick={() => setPage((p) => p + 1)}
                  >
                    Next
                    <ChevronRight className="h-4 w-4" />
                  </Button>
                </nav>
              )}

              <div className="flex flex-col gap-2">
              {rows.map((t) => {
                const checked = selectedIds.has(t.id);
                const cat = categoryById[t.id] ?? "";
                const cpFallback = guardSingleLineText(
                  defaultCounterpartyLabel(t),
                  ONBOARDING_INPUT_LIMITS.counterpartyLabelChars,
                );
                const cp = guardSingleLineText(
                  counterpartyById[t.id] ?? cpFallback,
                  ONBOARDING_INPUT_LIMITS.counterpartyLabelChars,
                );
                const sk = t.source_statement ?? sourceById[t.id] ?? "";
                const amountNum = Number(t.amount);
                const amountDisplay = Number.isFinite(amountNum)
                  ? amountNum.toLocaleString("en-IN", { minimumFractionDigits: 2 })
                  : "—";
                return (
                  <div
                    key={t.id}
                    className={cn(
                      "rounded-lg border p-3 text-sm transition-colors",
                      checked && "border-primary/40 bg-primary/5",
                    )}
                  >
                    {/* Checkbox + left column (counterparty → amount → meta → category) + confirm */}
                    <div className="flex items-start gap-2">
                      <Checkbox
                        checked={checked}
                        onCheckedChange={() => toggleOne(t.id)}
                        aria-label={`Select transaction ${t.id}`}
                        disabled={busy}
                        className="mt-1 shrink-0"
                      />
                      <div className="min-w-0 flex-1 space-y-1">
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0 flex-1 space-y-0.5">
                            {editingCpId === t.id ? (
                              <div className="min-w-0 flex flex-col gap-1">
                                <Input
                                  className="h-7 text-sm"
                                  value={cp}
                                  maxLength={ONBOARDING_INPUT_LIMITS.counterpartyLabelChars}
                                  autoFocus
                                  aria-invalid={!cp.trim()}
                                  aria-describedby={`cp-edit-hint-${t.id}`}
                                  onChange={(e) =>
                                    setCounterpartyById((prev) => ({
                                      ...prev,
                                      [t.id]: guardSingleLineText(
                                        e.target.value,
                                        ONBOARDING_INPUT_LIMITS.counterpartyLabelChars,
                                      ),
                                    }))
                                  }
                                  onBlur={() => {
                                    setCounterpartyById((prev) => {
                                      const cur = guardSingleLineText(
                                        prev[t.id] ?? cpFallback,
                                        ONBOARDING_INPUT_LIMITS.counterpartyLabelChars,
                                      ).trim();
                                      return { ...prev, [t.id]: cur || cpFallback };
                                    });
                                    setEditingCpId(null);
                                  }}
                                  onKeyDown={(e) => {
                                    if (e.key === "Enter") {
                                      (e.target as HTMLInputElement).blur();
                                    }
                                  }}
                                />
                                <span id={`cp-edit-hint-${t.id}`} className="sr-only">
                                  Counterparty label, max{" "}
                                  {ONBOARDING_INPUT_LIMITS.counterpartyLabelChars} characters. Empty
                                  saves as the suggested bank label.
                                </span>
                              </div>
                            ) : (
                              <button
                                type="button"
                                className="group flex max-w-full min-w-0 items-center gap-1.5 rounded-sm text-left font-medium text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
                                title="Click to edit counterparty name"
                                aria-label={`Edit counterparty: ${cp}`}
                                onClick={() => setEditingCpId(t.id)}
                              >
                                {/* Name truncates; pencil fades in on hover / keyboard focus so edit affordance is obvious */}
                                <span className="min-w-0 truncate">{cp}</span>
                                <Pencil
                                  className="size-3.5 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100"
                                  aria-hidden
                                />
                              </button>
                            )}
                            {/* Amount directly under counterparty so eyes stay on the left column */}
                            <p className="text-sm font-semibold tabular-nums text-foreground">
                              ₹{amountDisplay}
                            </p>
                          </div>
                          <Tooltip>
                            {/*
                              Base UI TooltipTrigger renders as a single button — never nest <Button>
                              inside it (invalid HTML). Use shared button styles on the trigger.
                            */}
                            <TooltipTrigger
                              type="button"
                              disabled={busy || !cat}
                              aria-label="Confirm this transaction"
                              className={cn(
                                buttonVariants({ variant: "ghost", size: "icon" }),
                                "size-10 shrink-0",
                              )}
                              onClick={() => void onConfirmRow(t.id)}
                            >
                              <Check className="size-6 stroke-[2.5]" aria-hidden />
                            </TooltipTrigger>
                            <TooltipContent side="top">Take a sec to confirm</TooltipContent>
                          </Tooltip>
                        </div>

                        {/* Meta chips — source, date, direction, narration */}
                        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
                          {sk && (
                            <Badge
                              variant="secondary"
                              className="px-1.5 py-0 font-normal text-[11px]"
                            >
                              {humanizeSourceKey(sk)}
                            </Badge>
                          )}
                          <span className="tabular-nums">{t.txn_date ?? "—"}</span>
                          <span>{t.direction}</span>
                          <span
                            className="truncate font-mono text-[10px]"
                            title={t.raw_description}
                          >
                            {t.raw_description}
                          </span>
                        </div>

                        {/* Category dropdown */}
                        <div className="pt-1">
                          <Select
                            value={cat ? cat : SELECT_NONE}
                            onValueChange={(v) =>
                              setCategoryById((prev) => ({
                                ...prev,
                                [t.id]: !v || v === SELECT_NONE ? "" : (v as CounterpartyCategory),
                              }))
                            }
                          >
                            <SelectTrigger className="h-8 w-full max-w-xs text-xs">
                              <SelectValue placeholder="Pick a category" />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value={SELECT_NONE} className="text-muted-foreground">
                                Pick a category…
                              </SelectItem>
                              {COUNTERPARTY_CATEGORY_OPTIONS.map((c) => (
                                <SelectItem key={c} value={c}>
                                  {c}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })}
              </div>
            </div>
          </>
        )}

        {/* Pagination (bottom) — same controls as the top nav */}
        {showPagination && (
          <nav
            aria-label="Review queue pages (bottom)"
            className="flex flex-wrap items-center justify-end gap-2 border-t pt-3"
          >
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={busy || page <= 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
            >
              <ChevronLeft className="h-4 w-4" />
              Prev
            </Button>
            <span className="text-xs tabular-nums text-muted-foreground">
              {page + 1} / {totalPages}
            </span>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={busy || page >= totalPages - 1}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
              <ChevronRight className="h-4 w-4" />
            </Button>
          </nav>
        )}
      </CardContent>
    </Card>
    </>
  );
}
