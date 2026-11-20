// Why a knowledge build was ended at once (#777): the codes the worker writes to `error_reason` when the model account
// is out of credit or the knowledge token cap is reached (server `StopCause`). Anything else is shown as it came.

export interface KnowledgeFailure { title: string; hint: string }

const CAUSES: Record<string, KnowledgeFailure> = {
  ModelCreditExhausted: {
    title: 'The model account is out of credit.',
    hint: 'Ask your administrator to add credit to the model provider account, then rebuild the knowledge from Index.',
  },
  KnowledgeTokenCap: {
    title: 'The knowledge build reached its token limit and was stopped.',
    hint: 'Ask an Admin to raise the limit, then retry the build.',
  },
}

/** The plain cause for a known `error_reason` code, undefined for any other reason. */
export const knowledgeFailure = (reason: string | null | undefined): KnowledgeFailure | undefined =>
  reason ? CAUSES[reason] : undefined

/** One sentence for the places that show a reason as text: the plain cause and what to do, else the reason itself. */
export function knowledgeFailureText(reason: string | null | undefined): string | undefined {
  const known = knowledgeFailure(reason)
  return known ? `${known.title} ${known.hint}` : reason ?? undefined
}
