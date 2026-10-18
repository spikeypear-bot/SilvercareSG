import { useEffect } from 'react'

/** Sets the tab title to "SilverCare - <page>". Never pass personal data. */
export function useDocumentTitle(page: string): void {
  useEffect(() => {
    document.title = `SilverCare - ${page}`
  }, [page])
}
