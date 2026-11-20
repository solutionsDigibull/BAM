import { describe, expect, it } from 'vitest'
import { knowledgeFailure, knowledgeFailureText } from './stop-cause'

describe('#777 knowledge stop causes', () => {
  it('wg24_ui_t01 a credit refusal reads as "the model account is out of credit" with what to do', () => {
    const f = knowledgeFailure('ModelCreditExhausted')
    expect(f?.title).toBe('The model account is out of credit.')
    expect(f?.hint).toMatch(/add credit/)
  })
  it('wg24_ui_t02 a reached cap says it was stopped and who raises it', () => {
    expect(knowledgeFailureText('KnowledgeTokenCap')).toMatch(/token limit.*Admin to raise/)
  })
  it('wg24_ui_t03 any other reason is shown as it came, and nothing stays nothing', () => {
    expect(knowledgeFailure('EmptyCorpus')).toBeUndefined()
    expect(knowledgeFailureText('EmptyCorpus')).toBe('EmptyCorpus')
    expect(knowledgeFailureText(null)).toBeUndefined()
  })
})
