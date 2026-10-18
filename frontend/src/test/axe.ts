import { axe } from 'vitest-axe'

/**
 * Runs axe and returns readable violations ([] when clean). Colour contrast is skipped
 * because jsdom does not compute styles; check it in the browser.
 */
export async function axeViolations(container: Element): Promise<string[]> {
  const results = await axe(container, { rules: { 'color-contrast': { enabled: false } } })
  return results.violations.map(
    (v) => `${v.id}: ${v.help} -> ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`,
  )
}
