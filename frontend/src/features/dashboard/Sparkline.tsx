interface SparklineProps {
  /** Readings oldest to newest; null = no reading (gap in the line). */
  values: ReadonlyArray<number | null>
  width?: number
  height?: number
}

/** Small decorative trend line. Pair it with the values as text for screen readers. */
export function Sparkline({ values, width = 112, height = 36 }: SparklineProps) {
  const present = values.filter((v): v is number => v !== null)
  if (present.length === 0) return null

  const pad = 4
  const min = Math.min(...present)
  const max = Math.max(...present)
  const range = max - min || 1
  const step = values.length > 1 ? (width - pad * 2) / (values.length - 1) : 0
  const point = (v: number, i: number) => ({
    x: pad + i * step,
    y: height - pad - ((v - min) / range) * (height - pad * 2),
  })

  // Split into segments at missing readings.
  const segments: Array<Array<{ x: number; y: number }>> = [[]]
  values.forEach((v, i) => {
    if (v === null) {
      if (segments[segments.length - 1].length > 0) segments.push([])
    } else {
      segments[segments.length - 1].push(point(v, i))
    }
  })

  const lastIndex = values.length - 1
  const last = values[lastIndex]

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      aria-hidden="true"
      className="overflow-visible text-teal-600"
    >
      {segments
        .filter((s) => s.length > 0)
        .map((s, i) =>
          s.length === 1 ? (
            <circle key={i} cx={s[0].x} cy={s[0].y} r={2} fill="currentColor" />
          ) : (
            <polyline
              key={i}
              points={s.map((p) => `${p.x},${p.y}`).join(' ')}
              fill="none"
              stroke="currentColor"
              strokeWidth={2.5}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          ),
        )}
      {last !== null && (
        <circle
          cx={point(last, lastIndex).x}
          cy={point(last, lastIndex).y}
          r={3.5}
          fill="white"
          stroke="currentColor"
          strokeWidth={2}
        />
      )}
    </svg>
  )
}
