import type { Stop } from '../types'

function categoryLabel(category: Stop['category']) {
  const labels: Record<Stop['category'], string> = {
    museum: 'Музей',
    gallery: 'Галерея',
    bar: 'Бар',
    cafe: 'Кафе / ресторан',
    park: 'Парк / прогулка',
    landmark: 'Достопримечательность',
    other: 'Другое',
  }
  return labels[category]
}

function openStatusLabel(status: Stop['open_status']) {
  if (status === 'open') return 'Открыто'
  if (status === 'closed') return 'Закрыто'
  return 'Неизвестно'
}

function distanceLabel(km: number) {
  if (!km || km <= 0) return '0 км'
  return `${km.toFixed(km < 10 ? 1 : 0)} км`
}

interface Props {
  stop: Stop
  onReplace?: (stop: Stop) => void
  replacing?: boolean
}

export default function StopCard({ stop, onReplace, replacing }: Props) {
  const isFirstStop = stop.travel_from_prev_min === 0 && (!stop.travel_mode_from_prev || stop.travel_from_prev_km === 0)

  return (
    <div className="stop-card card">
      <div className="stop-time">{stop.start}–{stop.end}</div>

      <div className="stop-main">
        <div className="stop-headline">
          <h4>{stop.name}</h4>
          <span className="score-pill">Оценка {stop.score.toFixed(1)}</span>
        </div>

        <p>{stop.address}</p>

        <div className="meta">
          <span>{categoryLabel(stop.category)}</span>

          {isFirstStop ? (
            <>
              <span>Стартовая точка дня</span>
              <span>0 мин в пути</span>
              <span>0 км</span>
            </>
          ) : (
            <>
              <span>{stop.travel_from_prev_min} мин в пути</span>
              <span>{distanceLabel(stop.travel_from_prev_km)}</span>
              {stop.travel_mode_label && <span>{stop.travel_mode_label}</span>}
            </>
          )}

          <span>{stop.visit_duration_min} мин на месте</span>
          <span className={`badge ${stop.open_status}`}>{openStatusLabel(stop.open_status)}</span>
          <span>≈ {stop.price_estimate_rub.toLocaleString('ru-RU')} ₽</span>
          {typeof stop.rating === 'number' && <span>★ {stop.rating.toFixed(1)}</span>}
          {typeof stop.reviews_count === 'number' && <span>{stop.reviews_count} отзывов</span>}
        </div>

        {stop.why_selected.length > 0 && (
          <div className="why-box">
            <div className="why-title">Почему выбрано</div>
            <ul className="why-list">
              {stop.why_selected.map((reason, idx) => <li key={idx}>{reason}</li>)}
            </ul>
          </div>
        )}
      </div>

      <div className="stop-side-actions">
        <a href={stop.route_to_url} target="_blank" rel="noreferrer">Маршрут сюда</a>
        {onReplace && (
          <button type="button" className="secondary-button" disabled={replacing} onClick={() => onReplace(stop)}>
            {replacing ? 'Меняем...' : 'Заменить точку'}
          </button>
        )}
      </div>
    </div>
  )
}
