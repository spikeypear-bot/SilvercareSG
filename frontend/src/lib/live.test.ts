import { act, renderHook } from '@testing-library/react'

type Observer = { next: (data: unknown) => void; error: (e: unknown) => void }

const amplify = vi.hoisted(() => {
  const channels: { path: string; observer: Observer | null; close: ReturnType<typeof vi.fn> }[] = []
  const connect = vi.fn(async (path: string) => {
    const entry = { path, observer: null as Observer | null, close: vi.fn() }
    channels.push(entry)
    return {
      subscribe(observer: Observer) {
        entry.observer = observer
        return { unsubscribe() {} }
      },
      close: entry.close,
    }
  })
  return { channels, connect }
})
vi.mock('aws-amplify/data', () => ({ events: { connect: amplify.connect } }))

import { LIVE_DEBOUNCE_MS, RESUBSCRIBE_MS, STAFF_CHANNELS, residentChannels, useLiveRefresh } from './live'

async function flush() {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  amplify.channels.length = 0
  amplify.connect.mockClear()
  vi.stubEnv('VITE_EVENTS_URL', 'https://events.example.test/event')
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllEnvs()
})

describe('useLiveRefresh (live-alerts criteria 18-22)', () => {
  it('subscribes to each channel with the user pool token', async () => {
    renderHook(() => useLiveRefresh(STAFF_CHANNELS, () => {}))
    await flush()
    expect(amplify.connect.mock.calls).toEqual([
      ['/health/*', { authMode: 'userPool' }],
      ['/device/*', { authMode: 'userPool' }],
    ])
    expect(amplify.channels.every((c) => c.observer !== null)).toBe(true)
  })

  it('connects nothing without VITE_EVENTS_URL', async () => {
    vi.stubEnv('VITE_EVENTS_URL', '')
    renderHook(() => useLiveRefresh(STAFF_CHANNELS, () => {}))
    await flush()
    expect(amplify.connect).not.toHaveBeenCalled()
  })

  it('connects nothing for an empty channel list', async () => {
    renderHook(() => useLiveRefresh([], () => {}))
    await flush()
    expect(amplify.connect).not.toHaveBeenCalled()
  })

  it('a ping refetches once, after the debounce', async () => {
    const onPing = vi.fn()
    renderHook(() => useLiveRefresh(residentChannels('res-0001'), onPing))
    await flush()
    act(() => amplify.channels[0].observer?.next({ type: 'changed', resident_id: 'res-0001' }))
    expect(onPing).not.toHaveBeenCalled()
    act(() => vi.advanceTimersByTime(LIVE_DEBOUNCE_MS))
    expect(onPing).toHaveBeenCalledTimes(1)
  })

  it('two pings within the debounce share one refetch', async () => {
    const onPing = vi.fn()
    renderHook(() => useLiveRefresh(residentChannels('res-0001'), onPing))
    await flush()
    act(() => amplify.channels[0].observer?.next({}))
    act(() => vi.advanceTimersByTime(1_000))
    act(() => amplify.channels[1].observer?.next({}))
    act(() => vi.advanceTimersByTime(LIVE_DEBOUNCE_MS))
    expect(onPing).toHaveBeenCalledTimes(1)
  })

  it('the same channels in a new array do not resubscribe', async () => {
    const { rerender } = renderHook(({ ids }) => useLiveRefresh(ids, () => {}), {
      initialProps: { ids: residentChannels('res-0001') },
    })
    await flush()
    rerender({ ids: residentChannels('res-0001') })
    await flush()
    expect(amplify.connect).toHaveBeenCalledTimes(2)
  })

  it('re-subscribes every 55 minutes, closing the old channels', async () => {
    renderHook(() => useLiveRefresh(residentChannels('res-0001'), () => {}))
    await flush()
    const first = [...amplify.channels]
    act(() => vi.advanceTimersByTime(RESUBSCRIBE_MS))
    await flush()
    expect(first.every((c) => c.close.mock.calls.length === 1)).toBe(true)
    expect(amplify.connect).toHaveBeenCalledTimes(4)
  })

  it('closes its channels on unmount and ignores a late ping', async () => {
    const onPing = vi.fn()
    const { unmount } = renderHook(() => useLiveRefresh(residentChannels('res-0001'), onPing))
    await flush()
    act(() => amplify.channels[0].observer?.next({}))
    unmount()
    act(() => vi.advanceTimersByTime(LIVE_DEBOUNCE_MS))
    expect(onPing).not.toHaveBeenCalled()
    expect(amplify.channels.every((c) => c.close.mock.calls.length === 1)).toBe(true)
  })

  it('a failed connect is only a console warning', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    amplify.connect.mockRejectedValueOnce(new Error('synthetic failure'))
    renderHook(() => useLiveRefresh(['/health/*'], () => {}))
    await flush()
    expect(warn).toHaveBeenCalledWith('Live updates unavailable; using polling.')
    warn.mockRestore()
  })
})
