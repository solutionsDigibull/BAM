// The choose-a-repository question of a build (#756, ADR 0028): when no repository is named and the Admin catalogue
// has no clear winner, the build waits for the FDE. The server decides everything (the short list, the scores, the
// answer's validity, who and when); this file only reads the question and sends the answer.
import { useCustom } from '@refinedev/core'
import type { PendingRepoQuestion, RepoAnswerRequest, RepoCandidate, RepoQuestionKind, RepoQuestions, RepoSource } from '@/contracts'
import { api } from '../api/adapter'
import { idempotencyKey } from '../http/http'

export { isForbidden } from './workflowCode'
export { repoName } from './workflowRepos'

/** How often a waiting card checks whether the build has been answered elsewhere. */
export const REFRESH_MS = 15_000

export const KIND_TITLE: Record<RepoQuestionKind, string> = {
  CLOSE_CANDIDATES: 'Several repositories fit about equally well',
  WEAK_MATCH: 'No repository is clearly the right starting point',
  NO_CANDIDATE: 'The catalogue has no repository for this Sub-Skill',
}

export const KIND_HELP: Record<RepoQuestionKind, string> = {
  CLOSE_CANDIDATES: 'Pick the one to start from, or say none fits and SBAM writes the code.',
  WEAK_MATCH: 'The best match is related but not clearly the same job. Pick one, or say none fits and SBAM writes the code.',
  NO_CANDIDATE: 'Do you have a repository to start from? If not, SBAM writes the code.',
}

export const SOURCE_LABEL: Record<RepoSource, string> = {
  ISATVON: 'Named in the ISATVON',
  ADMIN_AGENTS: 'Admin: Agents master',
  ADMIN_TOOLING: 'Admin: Tooling master',
  FDE: 'You',
}

/** `0.85` -> `85%`. */
export const percent = (score: number): string => `${Math.round(score * 100)}%`

/** Green for the "same job" band of the repo-scout prompt, amber for "related", grey below. */
export const scoreTone = (score: number): 'ok' | 'warn' | 'muted' => (score >= 0.8 ? 'ok' : score >= 0.4 ? 'warn' : 'muted')

/** The cheap client check before sending: an https address with a host and a path. The server decides for real. */
export const looksLikeRepoUrl = (value: string): boolean => /^https:\/\/[A-Za-z0-9.-]+(:\d{1,5})?\/[^\s?#@]+$/.test(value.trim())

export const candidates = (q: PendingRepoQuestion): RepoCandidate[] => [...(q.question.candidates ?? [])]

export const reasonsOf = (c: RepoCandidate): string[] => [...(c.reasons ?? [])]

/** What the server said, in its own words, for a failed answer. */
export function problemText(error: unknown): string {
  const e = error as { problem?: { detail?: string; title?: string; errors?: { message?: string }[] }; message?: string }
  return e.problem?.errors?.[0]?.message ?? e.problem?.detail ?? e.problem?.title ?? e.message ?? 'The answer was not accepted.'
}

export function useRepoQuestions(workflowId: string | undefined) {
  const { query } = useCustom<RepoQuestions>({
    url: 'repo-questions', method: 'get', config: { query: { workflowId } },
    queryOptions: { enabled: !!workflowId, retry: false, refetchInterval: REFRESH_MS }, // the card has its own Retry
  })
  return { data: query.data?.data, error: query.error, isLoading: query.isLoading, refetch: query.refetch }
}

/** One answer: a fresh Idempotency-Key per card, so a double click is the same command. */
export const newAnswerKey = (): string => idempotencyKey()

export const sendAnswer = (workflowId: string, subSkillId: string, body: RepoAnswerRequest, key: string) =>
  api.answerRepoQuestion(workflowId, subSkillId, body, key)
