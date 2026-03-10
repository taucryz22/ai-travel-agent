import { useMemo, useState } from 'react'
import type { Mode, PlanRequest, UiFilter } from '../types'

interface Props {
  onSubmit: (payload: PlanRequest) => Promise<void>
  loading: boolean
}

const FILTER_LABELS: Record<UiFilter, string> = {
  less_walking: 'Меньше ходить',
  more_art: 'Больше искусства',
  budget_friendly: 'Бюджетнее',
  more_food: 'Больше еды',
  more_walks: 'Больше прогулок',
}

const FILTER_HINTS: Record<UiFilter, string> = {
  less_walking: 'Старайся делать маршрут компактнее и уменьшать длинные пешие переходы.',
  more_art: 'Сделай акцент на современном искусстве, музеях и галереях.',
  budget_friendly: 'Сделай маршрут экономнее и чаще выбирай недорогие или бесплатные места.',
  more_food: 'Добавь больше интересных мест для еды и атмосферных остановок.',
  more_walks: 'Добавь больше прогулочных мест, парков и уличных маршрутов.',
}

export default function TripForm({ onSubmit, loading }: Props) {
  const [query, setQuery] = useState('Хочу выходные в Санкт-Петербурге, люблю рок-бары и современное искусство')
  const [days, setDays] = useState('2')
  const [budget, setBudget] = useState('15000')
  const [mode, setMode] = useState<Mode>('smart')
  const [filters, setFilters] = useState<UiFilter[]>([])

  const enhancedQuery = useMemo(() => {
    if (filters.length === 0) return query
    const hints = filters.map((filter) => FILTER_HINTS[filter]).join(' ')
    return `${query.trim()} ${hints}`.trim()
  }, [filters, query])

  function keepDigits(value: string) {
    return value.replace(/[^0-9]/g, '')
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const parsedDays = Math.min(5, Math.max(1, Number(days || '1')))
    const parsedBudget = Math.max(0, Number(budget || '0'))
    await onSubmit({ query: enhancedQuery, days: parsedDays, budget: parsedBudget, mode })
  }

  function toggleFilter(filter: UiFilter) {
    setFilters((prev) => prev.includes(filter) ? prev.filter((item) => item !== filter) : [...prev, filter])
  }

  return (
    <form className="card form" onSubmit={handleSubmit}>
      <label>
        Запрос
        <textarea rows={4} value={query} onChange={(e) => setQuery(e.target.value)} />
      </label>

      <div className="row">
        <label>
          Дни
          <input
            type="text"
            inputMode="numeric"
            pattern="[0-9]*"
            value={days}
            onChange={(e) => setDays(keepDigits(e.target.value))}
          />
        </label>

        <label>
          Бюджет
          <input
            type="text"
            inputMode="numeric"
            pattern="[0-9]*"
            value={budget}
            onChange={(e) => setBudget(keepDigits(e.target.value))}
          />
        </label>

        <label>
          Режим
          <select value={mode} onChange={(e) => setMode(e.target.value as Mode)}>
            <option value="smart">Умный режим</option>
            <option value="walking">Пешком</option>
            <option value="transit">Общественный транспорт</option>
            <option value="driving">На машине</option>
          </select>
        </label>
      </div>

      <div className="filters-box">
        <div className="filters-label">Быстрые настройки маршрута</div>
        <div className="filters-row">
          {(Object.keys(FILTER_LABELS) as UiFilter[]).map((filter) => (
            <button
              key={filter}
              type="button"
              className={filters.includes(filter) ? 'filter-chip active' : 'filter-chip'}
              onClick={() => toggleFilter(filter)}
            >
              {FILTER_LABELS[filter]}
            </button>
          ))}
        </div>
      </div>

      <button disabled={loading} type="submit">
        {loading ? 'Генерируем...' : 'Сгенерировать план'}
      </button>
    </form>
  )
}
