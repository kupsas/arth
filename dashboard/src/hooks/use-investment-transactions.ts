/**
 * Mutations for investment ledger rows — review queue (Phase 5) and bulk actions.
 *
 * Invalidates ``portfolioKeys.all`` so holdings summaries and investment history
 * stay in sync after a PATCH.
 */

"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { portfolioKeys } from "@/hooks/use-portfolio";
import {
  bulkUpdateInvestmentTransactions,
  updateInvestmentTransaction,
} from "@/lib/api";
import type {
  BulkInvestmentUpdateRequest,
  InvestmentTransactionUpdate,
} from "@/lib/types";

export function useUpdateInvestmentTransaction() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (args: { id: number; update: InvestmentTransactionUpdate }) =>
      updateInvestmentTransaction(args.id, args.update),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: portfolioKeys.all });
    },
  });
}

export function useBulkUpdateInvestmentTransactions() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: BulkInvestmentUpdateRequest) =>
      bulkUpdateInvestmentTransactions(body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: portfolioKeys.all });
    },
  });
}
