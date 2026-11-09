// BuLLM logo lockup (4 Nov 2026, per user): the transparent logo (bull mark + wordmark + tagline), black tagline on light
// and white tagline on dark, reused in the top bar. Sized by height from `--bl` (brand.css).
import './brand.css'
import { useUi } from '../lib/store'
import light from '../assets/brand-logo-light.png'
import dark from '../assets/brand-logo-dark.png'

export function BrandLockup({ className = '' }: { className?: string }) {
  const theme = useUi(s => s.theme)
  return <img className={`brand-lockup ${className}`} src={theme === 'dark' ? dark : light} alt="BuLLM. Enterprise Workflows. Intelligently Done." />
}
