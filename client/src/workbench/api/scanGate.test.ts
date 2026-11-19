import { describe, expect, test } from 'vitest'
import { fitMultiBatch, MAX_MULTI_BATCH_BYTES, MAX_MULTI_BATCH_FILES, MAX_MULTI_BATCH_WAVES, multiBatchLimitText } from './scanGate'
import { MAX_BATCH_FILES } from './types'

const files = (n: number, size = 1) => Array.from({ length: n }, (_, i) => ({ name: `f${i}.pdf`, size }))

describe('multi-batch limits', () => {
  test('test_712_multi_batch_limit_text_is_derived_from_the_constants', () => {
    expect(MAX_MULTI_BATCH_FILES).toBe(MAX_BATCH_FILES * MAX_MULTI_BATCH_WAVES)
    const noun = MAX_MULTI_BATCH_WAVES === 1 ? 'batch' : 'batches'
    expect(multiBatchLimitText()).toBe(`${MAX_MULTI_BATCH_WAVES} ${noun} of ${MAX_BATCH_FILES}`)
  })

  test('test_712_too_many_files_error_names_the_real_limit_and_no_stale_number', () => {
    const r = fitMultiBatch(files(MAX_MULTI_BATCH_FILES + 1))
    expect(r.ok).toBe(false)
    expect(r.reason).toContain(`maximum limit of ${MAX_MULTI_BATCH_FILES} files (${multiBatchLimitText()})`)
    if (MAX_MULTI_BATCH_WAVES === 1) expect(r.reason).not.toMatch(/9 batches/)
  })

  test('test_712_too_many_bytes_error_names_the_real_budget', () => {
    const r = fitMultiBatch(files(1, MAX_MULTI_BATCH_BYTES + 1))
    expect(r.ok).toBe(false)
    expect(r.reason).toContain(`across ${multiBatchLimitText()}`)
  })
})
