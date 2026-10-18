import { PASSWORD_RULES, meetsPasswordRules } from './passwordRules'

const passing = (password: string) =>
  PASSWORD_RULES.filter((rule) => rule.test(password)).map((rule) => rule.id)

describe('password rules (criterion 2: min 12, lower, upper, number, symbol)', () => {
  it('accepts a password meeting every rule', () => {
    expect(meetsPasswordRules('Test-Passw0rd!2026')).toBe(true)
  })

  it.each([
    ['Ab1!efghijk', 'length'], // 11 characters
    ['TEST-PASSW0RD!', 'lower'],
    ['test-passw0rd!', 'upper'],
    ['Test-Password!', 'number'],
    ['TestPassw0rd12', 'symbol'],
  ])('rejects %s (missing %s)', (password, missing) => {
    expect(meetsPasswordRules(password)).toBe(false)
    expect(passing(password)).not.toContain(missing)
  })

  it('accepts exactly 12 characters', () => {
    expect(meetsPasswordRules('Abcdefgh12!x')).toBe(true)
  })

  it('reports each rule separately for the checklist', () => {
    expect(passing('')).toEqual([])
    expect(passing('a')).toEqual(['lower'])
    expect(passing('aA1')).toEqual(['lower', 'upper', 'number'])
  })
})
