/** Synthetic users and passwords for tests. No real people or credentials. */
import type { AuthUser } from '../types/auth'

export const TEST_USERS = {
  doctor: { name: 'Test Doctor A', email: 'test.doctor.a@example.test', groups: ['doctor'] },
  nurse: { name: 'Test Nurse A', email: 'test.nurse.a@example.test', groups: ['nurse'] },
  caregiver: {
    name: 'Test Caregiver A',
    email: 'test.caregiver.a@example.test',
    groups: ['caregiver'],
  },
  family: { name: 'Test Family A', email: 'test.family.a@example.test', groups: ['family'] },
  noGroup: { name: 'Test User NoRole', email: 'test.norole@example.test', groups: [] },
} satisfies Record<string, AuthUser>

/** Meets every pool rule: 12+ chars, lower, upper, number, symbol. */
export const VALID_PASSWORD = 'Test-Passw0rd!2026'
export const OTHER_VALID_PASSWORD = 'Another#Test9Pass'
export const TEST_TOTP_SECRET = 'TESTSECRETAAAABBBBCCCCDDDD234567'
export const TEST_TOTP_URI = `otpauth://totp/SilverCare:test?secret=${TEST_TOTP_SECRET}&issuer=SilverCare`

export interface Deferred<T> {
  promise: Promise<T>
  resolve: (value: T) => void
  reject: (reason: unknown) => void
}

/** A promise the test resolves by hand, to observe in-flight (busy) states. */
export function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}
