import { roleLabel, viewForGroups } from './roles'

describe('viewForGroups (Users and access rules)', () => {
  it.each([
    [['doctor'], 'staff'],
    [['nurse'], 'staff'],
    [['caregiver'], 'family'],
    [['family'], 'family'],
    [['family', 'doctor'], 'staff'],
    [['caregiver', 'nurse'], 'staff'],
    [['caregiver', 'family'], 'family'],
    [[], 'no-access'],
    [['admin'], 'no-access'],
    [['Doctor'], 'no-access'],
  ])('%j -> %s', (groups, view) => {
    expect(viewForGroups(groups)).toBe(view)
  })
})

describe('roleLabel', () => {
  it.each([
    [['doctor'], 'Doctor'],
    [['nurse'], 'Nurse'],
    [['caregiver'], 'Caregiver'],
    [['family'], 'Family'],
    [['family', 'nurse'], 'Nurse'],
    [['nurse', 'doctor'], 'Doctor'],
    [[], 'No role'],
    [['admin'], 'No role'],
  ])('%j -> %s', (groups, label) => {
    expect(roleLabel(groups)).toBe(label)
  })
})
