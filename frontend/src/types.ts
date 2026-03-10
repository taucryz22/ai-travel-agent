export type Mode = 'smart' | 'walking' | 'transit' | 'driving'
export type OpenStatus = 'open' | 'closed' | 'unknown'
export type Category = 'museum' | 'gallery' | 'bar' | 'cafe' | 'park' | 'landmark' | 'other'
export type BudgetStatus = 'ok' | 'near_limit' | 'over_budget'
export type TravelMode = 'walking' | 'transit' | 'driving'
export type UiFilter = 'less_walking' | 'more_art' | 'budget_friendly' | 'more_food' | 'more_walks'

export interface PlanRequest {
  query: string
  days: number
  budget: number
  mode: Mode
  avoid_place_names?: string[]
}

export interface Stop {
  start: string
  end: string
  name: string
  address: string
  lat: number
  lon: number
  travel_from_prev_min: number
  travel_from_prev_km: number
  visit_duration_min: number
  travel_mode_from_prev?: TravelMode | null
  travel_mode_label?: string | null
  open_status: OpenStatus
  route_to_url: string
  category: Category
  price_estimate_rub: number
  score: number
  why_selected: string[]
  rating?: number | null
  reviews_count?: number | null
}

export interface DaySummary {
  stops_count: number
  total_travel_min: number
  total_travel_km: number
  total_visit_min: number
  estimated_day_budget_rub: number
  focus: string
  area_label: string
  style_label: string
  theme_label: string
}

export interface DayPlan {
  title: string
  day_route_url: string
  stops: Stop[]
  summary?: DaySummary | null
}

export interface BudgetSummary {
  budget_total: number
  estimated_total: number
  currency: string
  notes: string
  status: BudgetStatus
  delta_rub: number
}

export interface Violation {
  type: string
  value_min: number
  note: string
}

export interface ItineraryResponse {
  city: string
  request: PlanRequest
  days: DayPlan[]
  budget_summary: BudgetSummary
  metrics: {
    total_travel_min: number
    violations: Violation[]
  }
  sources: {
    wikivoyage_page: string
    rag_snippets: string[]
    generated_search_phrases: string[]
  }
}
