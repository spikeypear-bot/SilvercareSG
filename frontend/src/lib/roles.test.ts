import { roleLabel, viewForGroups } from './roles'

describe('viewForGroups (Users and access rules)', () => {
  it.each([
    [['doctor'], 'doctor'],
    [['nurse'], 'nurse'],
    [['caregiver'], 'family'],
    [['family'], 'family'],
    // Nurse wins over doctor, whichever order the groups arrive in.
    [['nurse', 'doctor'], 'nurse'],
    [['doctor', 'nurse'], 'nurse'],
    // Any staff group wins over caregiver/family.
    [['family', 'doctor'], 'doctor'],
    [['caregiver', 'nurse'], 'nurse'],
    [['caregiver', 'family', 'doctor'], 'doctor'],
    [['caregiver', 'family'], 'family'],
    [[], 'no-access'],
    [['admin'], 'no-access'],
    [['Doctor'], 'no-access'],
    [['Nurse'], 'no-access'],
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
    [['family', 'doctor'], 'Doctor'],
    // Same precedence as viewForGroups, so the badge matches the dashboard shown.
    [['nurse', 'doctor'], 'Nurse'],
    [['doctor', 'nurse'], 'Nurse'],
    [['family', 'caregiver'], 'Caregiver'],
    [[], 'No role'],
    [['admin'], 'No role'],
  ])('%j -> %s', (groups, label) => {
    expect(roleLabel(groups)).toBe(label)
  })

  it.each([
    [['nurse', 'doctor']],
    [['doctor', 'nurse']],
    [['family', 'doctor']],
    [['caregiver', 'nurse']],
    [['caregiver', 'family']],
  ])('%j: the badge names the group whose view is shown', (groups) => {
    const view = viewForGroups(groups)
    const expected = { nurse: 'Nurse', doctor: 'Doctor' }[view as 'nurse' | 'doctor']
    if (expected) expect(roleLabel(groups)).toBe(expected)
    else expect(['Caregiver', 'Family']).toContain(roleLabel(groups))
  })
})
