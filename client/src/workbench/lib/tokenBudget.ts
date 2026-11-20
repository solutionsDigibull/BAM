// The token meter of the Translate view (#776, ADR 0028 decision 6): what the project's builds used against the cap, per
// scope, with the alert level, and the one raise a build is allowed. Shapes: `sbam_contracts.budget`. The server counts
// (`llm_calls`, failed attempts included) and decides; nothing here computes a level.
import { useCustom } from '@refinedev/core'
import type { BudgetLevel, BudgetScope, BudgetState, TokenMeter } from '@/contracts'
import { api } from '../api/adapter'

export const METER_POLL_MS = 10_000
export const MAX_CAP = 1_000_000_000

export const SCOPE_LABEL: Record<BudgetScope, string> = {
  SUB_SKILL: 'One Sub-Skill build',
  WORKFLOW: 'Workflow build',
  KNOWLEDGE: 'Knowledge build',
}
export const LEVEL_LABEL: Record<BudgetLevel, string> = {
  OK: 'Within the limit',
  FIRST_ALERT: 'Half used',
  SECOND_ALERT: 'Nearly used up',
  STOPPED: 'Stopped at the limit',
}
export type Tone = 'ok' | 'warn' | 'err'
export const levelTone = (l: BudgetLevel): Tone => (l === 'OK' ? 'ok' : l === 'STOPPED' ? 'err' : 'warn')

/** Whole percent of the cap, held at 100 for the bar (a stopped build can be over). */
export const percent = (s: Pick<BudgetState, 'used' | 'cap'>): number => Math.min(100, Math.floor((s.used * 100) / s.cap))
export const formatTokens = (n: number): string => n.toLocaleString('en-US')

/** Nothing was counted yet: every scope is at zero. */
export const isUnused = (m: TokenMeter): boolean => m.scopes.every(s => s.used === 0)

/** A raise is offered for a stopped scope that names a build and has not been raised for it. */
export const canRaise = (s: BudgetState): boolean => s.level === 'STOPPED' && !!s.build_id && !s.raised_once

/** The number the raise box starts with: twice the cap, held to the platform maximum. */
export const suggestedCap = (cap: number): number => Math.min(MAX_CAP, cap * 2)
export const validNewCap = (value: string, cap: number): boolean => {
  const n = Number(value)
  return Number.isInteger(n) && n > cap && n <= MAX_CAP
}

const code = (e: unknown): number | undefined => (e as { statusCode?: number } | null | undefined)?.statusCode
/** 403 (wrong portal) and 404 (not assigned, or another tenant) both mean the account may not see this project's budget. */
export const isForbidden = (e: unknown): boolean => code(e) === 403 || code(e) === 404

export function problemText(error: unknown): string {
  const e = error as { problem?: { detail?: string; title?: string }; message?: string }
  return e.problem?.detail ?? e.problem?.title ?? e.message ?? 'The request failed.'
}

export function useTokenBudget() {
  const { query } = useCustom<TokenMeter>({
    url: 'token-budget', method: 'get',
    queryOptions: { retry: false, refetchInterval: METER_POLL_MS }, // the panel has its own Retry
  })
  return { data: query.data?.data, error: query.error, isLoading: query.isLoading, refetch: query.refetch }
}

export const raiseCap = (scope: BudgetScope, buildId: string, tokens: number) => api.raiseTokenCap({ scope, build_id: buildId, tokens })
