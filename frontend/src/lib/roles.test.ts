import { roleLabel, viewForGroups } from './roles'

describe('viewForGroups (Users and access rules)', () => {
  it.each([
    [['doctor'], 'doctor'],
    [['nurse'], 'nurse'],
    [['caregiver'], 'family'],
    // The `family` group is removed (docs/features/alert-routing.md): alone, no access.
    [['family'], 'no-access'],
    // Nurse wins over doctor, whichever order the groups arrive in.
    [['nurse', 'doctor'], 'nurse'],
    [['doctor', 'nurse'], 'nurse'],
    // Any staff group wins over caregiver; a leftover `family` group changes nothing.
    [['family', 'doctor'], 'doctor'],
    [['family', 'nurse'], 'nurse'],
    [['caregiver', 'nurse'], 'nurse'],
    [['caregiver', 'family', 'doctor'], 'doctor'],
    [['caregiver', 'family'], 'family'],
    [['family', 'caregiver'], 'family'],
    // ops: after nurse and doctor, before caregiver.
    [['ops'], 'ops'],
    [['ops', 'nurse'], 'nurse'],
    [['doctor', 'ops'], 'doctor'],
    [['caregiver', 'ops'], 'ops'],
    [['ops', 'family'], 'ops'],
    [['Ops'], 'no-access'],
    [[], 'no-access'],
    [['admin'], 'no-access'],
    [['Doctor'], 'no-access'],
    [['Nurse'], 'no-access'],
    [['Family'], 'no-access'],
    [['Caregiver'], 'no-access'],
  ])('%j -> %s', (groups, view) => {
    expect(viewForGroups(groups)).toBe(view)
  })
})

describe('roleLabel', () => {
  it.each([
    [['doctor'], 'Doctor'],
    [['nurse'], 'Nurse'],
    [['caregiver'], 'Caregiver'],
    // `family` is not a role any more.
    [['family'], 'No role'],
    [['family', 'nurse'], 'Nurse'],
    [['family', 'doctor'], 'Doctor'],
    // Same precedence as viewForGroups, so the badge matches the dashboard shown.
    [['nurse', 'doctor'], 'Nurse'],
    [['doctor', 'nurse'], 'Nurse'],
    [['family', 'caregiver'], 'Caregiver'],
    [['ops'], 'Ops'],
    [['caregiver', 'ops'], 'Ops'],
    [['ops', 'doctor'], 'Doctor'],
    [[], 'No role'],
    [['admin'], 'No role'],
  ])('%j -> %s', (groups, label) => {
    expect(roleLabel(groups)).toBe(label)
  })

  it('never returns "Family"', () => {
    for (const groups of [['family'], ['family', 'caregiver'], ['family', 'nurse'], ['family', 'doctor'], []]) {
      expect(roleLabel(groups)).not.toBe('Family')
    }
  })

  it.each([
    [['nurse', 'doctor']],
    [['doctor', 'nurse']],
    [['family', 'doctor']],
    [['caregiver', 'nurse']],
    [['caregiver', 'family']],
    [['caregiver', 'ops']],
    [['ops', 'doctor']],
    [['family']],
    [[]],
  ])('%j: the badge names the group whose view is shown', (groups) => {
    const view = viewForGroups(groups)
    const expected = { nurse: 'Nurse', doctor: 'Doctor', ops: 'Ops', family: 'Caregiver', 'no-access': 'No role' }[view]
    expect(roleLabel(groups)).toBe(expected)
  })
})
