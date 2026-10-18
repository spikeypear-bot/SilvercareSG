import { RefreshCw } from 'lucide-react'
import { Button } from '../../components/Button'
import { Notice } from '../../components/Notice'

export function DashboardError({ onRetry }: { onRetry: () => void }) {
  return (
    <Notice
      tone="error"
      title="We couldn't load the dashboard."
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
