/**
 * InvestmentTransactionFiltersBar — filter row for the investment ledger tab.
 *
 * Mirrors the bank ``TransactionFiltersBar`` layout so the Transactions page feels
 * consistent: search + date presets on row 1; dropdowns on row 2.
 *
 * Mapping from bank → investments:
 *   - Account  → Platform (``account_platform`` on each ledger row)
 *   - Direction → Flow (INFLOW = buys / dividends / …, OUTFLOW = sells / switch out)
 *   - Category → (omitted — not on investment rows)
 *   - Type → Ledger ``txn_type`` (BUY, SELL, …)
 *   - Reviewed → same flag
 *
 * Search hits **symbol and notes** on the server (substring, case-insensitive).
 */

"use client";

import * as React from "react";
import { SearchIcon, XIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  DateRangePicker,
  type Preset,
} from "@/components/dashboard/date-range-picker";
import { useHoldings } from "@/hooks/use-portfolio";
import type {
  InvestmentFlowFilter,
  InvestmentLedgerTxnType,
  InvestmentTransactionFilters,
  DateRange,
} from "@/lib/types";

const LEDGER_TYPES: InvestmentLedgerTxnType[] = [
  "BUY",
  "SELL",
  "DIVIDEND",
  "SIP",
  "SWITCH_IN",
  "SWITCH_OUT",
];

interface InvestmentTransactionFiltersBarProps {
  filters: InvestmentTransactionFilters;
  onFiltersChange: (update: Partial<InvestmentTransactionFilters>) => void;
  onReset: () => void;
  activeCount: number;
  datePreset: Preset;
  onDatePresetChange: (preset: Preset, range: DateRange) => void;
  /** Logged-in username — used to load distinct platforms from holdings. */
  userId: string | null;
}

export function InvestmentTransactionFiltersBar({
  filters,
  onFiltersChange,
  onReset,
  activeCount,
  datePreset,
  onDatePresetChange,
  userId,
}: InvestmentTransactionFiltersBarProps) {
  const { data: holdings } = useHoldings(userId ? { user_id: userId } : {}, {
    enabled: Boolean(userId),
  });

  const platforms = React.useMemo(() => {
    if (!holdings?.length) return [];
    const set = new Set<string>();
    for (const h of holdings) {
      if (h.account_platform) set.add(h.account_platform);
    }
    return Array.from(set).sort();
  }, [holdings]);

  const [searchInput, setSearchInput] = React.useState(filters.search ?? "");

  React.useEffect(() => {
    setSearchInput(filters.search ?? "");
  }, [filters.search]);

  React.useEffect(() => {
    const timer = setTimeout(() => {
      if (searchInput !== (filters.search ?? "")) {
        onFiltersChange({ search: searchInput || undefined, page: 1 });
      }
    }, 300);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchInput]);

  const [customRange, setCustomRange] = React.useState<DateRange>({});

  function handlePresetChange(newPreset: Preset, newRange: DateRange) {
    onDatePresetChange(newPreset, newRange);
    onFiltersChange({
      date_from: newRange.date_from,
      date_to: newRange.date_to,
      page: 1,
    });
  }

  function handleCustomChange(newRange: DateRange) {
    setCustomRange(newRange);
    onDatePresetChange("custom", newRange);
    onFiltersChange({
      date_from: newRange.date_from,
      date_to: newRange.date_to,
      page: 1,
    });
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[180px]">
          <SearchIcon className="absolute left-2.5 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground pointer-events-none" />
          <Input
            type="search"
            placeholder="Search symbol or notes…"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            className="pl-8 h-8 text-sm"
          />
        </div>

        <DateRangePicker
          preset={datePreset}
          customRange={customRange}
          onPresetChange={handlePresetChange}
          onCustomChange={handleCustomChange}
          clearable
        />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Select
          value={filters.account_platform ?? ""}
          onValueChange={(v) =>
            onFiltersChange({ account_platform: v || undefined, page: 1 })
          }
        >
          <SelectTrigger size="sm" className="w-[150px]">
            <SelectValue placeholder="All platforms" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="">All platforms</SelectItem>
            {platforms.map((p) => (
              <SelectItem key={p} value={p}>
                {p}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select
          value={filters.flow ?? ""}
          onValueChange={(v) =>
            onFiltersChange({
              flow: (v as InvestmentFlowFilter) || undefined,
              page: 1,
            })
          }
        >
          <SelectTrigger size="sm" className="w-[120px]">
            <SelectValue placeholder="All flows" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="">All flows</SelectItem>
            <SelectItem value="INFLOW">Inflow</SelectItem>
            <SelectItem value="OUTFLOW">Outflow</SelectItem>
          </SelectContent>
        </Select>

        <Select
          value={filters.txn_type ?? ""}
          onValueChange={(v) =>
            onFiltersChange({ txn_type: v || undefined, page: 1 })
          }
        >
          <SelectTrigger size="sm" className="w-[150px]">
            <SelectValue placeholder="All types" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="">All types</SelectItem>
            {LEDGER_TYPES.map((t) => (
              <SelectItem key={t} value={t}>
                {t}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select
          value={
            filters.is_reviewed === undefined
              ? ""
              : filters.is_reviewed
                ? "true"
                : "false"
          }
          onValueChange={(v) =>
            onFiltersChange({
              is_reviewed: v === "" ? undefined : v === "true",
              page: 1,
            })
          }
        >
          <SelectTrigger size="sm" className="w-[130px]">
            <SelectValue placeholder="Any status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="">Any status</SelectItem>
            <SelectItem value="true">Reviewed</SelectItem>
            <SelectItem value="false">Unreviewed</SelectItem>
          </SelectContent>
        </Select>

        {activeCount > 0 && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onReset}
            className="gap-1.5 text-muted-foreground"
          >
            <XIcon className="size-3.5" />
            Reset ({activeCount})
          </Button>
        )}
      </div>
    </div>
  );
}
