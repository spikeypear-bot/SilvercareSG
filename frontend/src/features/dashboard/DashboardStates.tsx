import { RefreshCw } from 'lucide-react'
import { Button } from '../../components/Button'
import { Notice } from '../../components/Notice'

export function DashboardError({
  onRetry,
  title = "We couldn't load the dashboard.",
}: {
  onRetry: () => void
  title?: string
}) {
  return (
    <Notice
      tone="error"
      title={title}
      action={
        <Button variant="secondary" onClick={onRetry}>
          <RefreshCw className="size-5" aria-hidden="true" />
          Try again
        </Button>
      }
    />
  )
}

/** Screen-reader announcement for skeleton loading states. */
export function LoadingStatus({ label }: { label: string }) {
  return (
    <span role="status" className="sr-only">
      {label}
    </span>
  )
}

/**
 * A background refresh failed (e.g. the route's throttle): the page keeps the data it
 * has, says how old it may be, and when it will try again.
 */
export function RefreshFailedNotice({ polling }: { polling: boolean }) {
  return (
    <Notice tone="warning" title="Couldn't refresh.">
      {polling
        ? 'Showing what was loaded before. Trying again in 30 seconds.'
        : 'Showing what was loaded before. Come back to this tab or reload the page to try again.'}
    </Notice>
  )
}
