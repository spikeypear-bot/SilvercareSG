import { useLocation } from 'react-router-dom'

/** Renders the current router path so tests can assert redirects. */
export function LocationDisplay() {
  const location = useLocation()
  return <div data-testid="location">{location.pathname}</div>
}
