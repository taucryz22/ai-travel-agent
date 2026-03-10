import type { DayPlan } from '../types'

interface Props {
  days: DayPlan[]
  activeIndex: number
  onChange: (idx: number) => void
}

export default function DayTabs({ days, activeIndex, onChange }: Props) {
  return (
    <div className="tabs">
      {days.map((day, idx) => (
        <button
          key={day.title}
          className={idx === activeIndex ? 'tab active' : 'tab'}
          onClick={() => onChange(idx)}
        >
          {day.title}
        </button>
      ))}
    </div>
  )
}
