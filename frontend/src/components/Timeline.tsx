import type { DayPlan, Stop } from '../types'
import StopCard from './StopCard'
import DaySummaryCard from './DaySummaryCard'

interface Props {
  day: DayPlan
  onRegenerateDay?: () => void
  regenerating?: boolean
  onReplaceStop?: (stop: Stop) => void
  replacingStopName?: string | null
}

export default function Timeline({ day, onRegenerateDay, regenerating, onReplaceStop, replacingStopName }: Props) {
  return (
    <div className="timeline-wrap">
      <div className="timeline-actions">
        <a className="button-link" href={day.day_route_url} target="_blank" rel="noreferrer">
          Открыть маршрут дня
        </a>
        {onRegenerateDay && (
          <button type="button" className="secondary-button" disabled={regenerating} onClick={onRegenerateDay}>
            {regenerating ? 'Обновляем...' : 'Перегенерировать день'}
          </button>
        )}
      </div>

      {day.summary && <DaySummaryCard summary={day.summary} />}

      <div className="timeline">
        {day.stops.map((stop, idx) => (
          <StopCard
            key={`${idx}-${stop.start}-${stop.name}`}
            stop={stop}
            onReplace={onReplaceStop}
            replacing={replacingStopName === stop.name}
          />
        ))}
      </div>
    </div>
  )
}
