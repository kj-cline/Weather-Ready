export type OperatorSummary = {
  operatorId: string;
  restaurantName: string;
  forecastReady: boolean;
  onboardingState: string;
};

export type OnboardingOption = {
  value: string;
  label: string;
};

export type OnboardingDescribedOption = OnboardingOption & {
  description?: string;
};

export type OperatorTextContract = {
  serviceStateOptions: {
    actual: OnboardingOption[];
    plan: OnboardingOption[];
  };
  statusPipLabels: Record<string, Record<string, string>>;
  attentionLabels: {
    sectionLabels: Record<string, string>;
    focusSectionLabels: Record<string, string>;
    defaultMomentLabel: string;
  };
  forecastLabels: {
    heroEyebrows: Record<string, string>;
  };
  workflow: {
    servicePlan: Record<string, string>;
    selectedNightPlan: Record<string, string>;
    actuals: Record<string, string>;
  };
};

export type OnboardingOptions = {
  timezones: string[];
  demandMixOptions: OnboardingOption[];
  neighborhoodOptions: OnboardingOption[];
  patioSeasonModes: string[];
  forecastInputModes: OnboardingDescribedOption[];
  historicalUploadRequirements: string[];
  historicalUploadAcceptedExtensions: string[];
};

export type HistoricalUploadReview = {
  accepted: boolean;
  summary: string;
  upload_token: string;
  file_name: string;
  usable_rows: number;
  skipped_rows: number;
  normal_service_rows: number;
  distinct_months: number;
  seasons_covered: string[];
  baseline_values: Record<string, number>;
  format_confidence: string;
  warnings: string[];
  ai_summary?: string | null;
  ai_warnings: string[];
  requirement_failures: string[];
  mapping: Record<string, string>;
  first_service_date?: string | null;
  last_service_date?: string | null;
};

export type OnboardingDraft = {
  restaurantName: string;
  canonicalAddress: string;
  city: string;
  timezone: string;
  forecastInputMode: string;
  historicalUploadToken?: string | null;
  historicalUploadReview?: HistoricalUploadReview | null;
  monThu: number;
  fri: number;
  sat: number;
  sun: number;
  demandMix: string;
  neighborhoodType: string;
  patioEnabled: boolean;
  patioSeatCapacity: number;
  patioSeasonMode: string;
  transitRelevance: boolean;
  venueRelevance: boolean;
  hotelTravelRelevance: boolean;
};

export type ActualEntryDraft = {
  serviceDate: string;
  realizedTotalCovers: number;
  realizedReservedCovers: number | null;
  realizedWalkInCovers: number | null;
  outsideCovers: number | null;
  serviceState: string;
  note: string;
};

export type ServicePlanEntryDraft = {
  serviceDate: string;
  serviceState: string;
  plannedTotalCovers: number | null;
  estimatedReductionPct: number | null;
  note: string;
  reviewWindowStart?: string | null;
  reviewWindowEnd?: string | null;
};

export type OnboardingState = {
  forecastReady: boolean;
  summary: {
    improvements?: string[];
  } | null;
  draft: OnboardingDraft;
  options: OnboardingOptions;
};

export type SetupBootstrapState = {
  operatorId: string;
  status: string;
  message: string;
  steps: string[];
  startedAt?: string | null;
  updatedAt?: string | null;
  completedAt?: string | null;
  failedAt?: string | null;
  failureReason?: string | null;
} | null;

export type ChatMessage = {
  messageId: number | null;
  role: string;
  content: string;
  kind: string;
  createdAt: string;
};

export type SuggestedMessage = {
  label: string;
  value: string;
  category: string;
};

export type Driver = {
  id: string;
  label: string;
};

export type ForecastWeather = {
  conditionCode:
    | "clear"
    | "partly_cloudy"
    | "cloudy"
    | "overcast"
    | "rain_light"
    | "rain_heavy"
    | "storm"
    | "snow_light"
    | "snow_heavy"
    | "sleet"
    | "fog"
    | "wind_high"
    | "heat"
    | "cold"
    | "unknown";
  temperatureHigh: number | null;
  temperatureLow: number | null;
  temperatureUnit: "F";
  apparentTemp7pm: number | null;
  precipChance: number | null;
  precipDinnerMax: number | null;
  windSpeedMph: number | null;
  cloudCoverBin: number | null;
  sunrise: string | null;
  sunset: string | null;
  weatherEffectPct: number | null;
};

export type ForecastCardStatus = {
  planStatus: "submitted" | "pending" | "stale" | "not_required";
  learningStatus: "none" | "open_question" | "overdue_question";
  actualsStatus: "not_due" | "due" | "recorded" | "overdue";
  watchStatus: "none" | "low_confidence" | "service_state_risk" | "material_uncertainty";
};

export type ForecastBaselineComparison = {
  baselineCovers?: number | null;
  deltaPct?: number | null;
  deltaCovers?: number | null;
  badgeText?: string | null;
  heroText?: string | null;
};

export type ForecastRecentChange = {
  previousExpected?: number | null;
  currentExpected?: number | null;
  deltaCovers?: number | null;
  snapshotReason?: string | null;
  text?: string | null;
  compactText?: string | null;
};

export type ForecastCard = {
  serviceDate: string;
  dayLabel: string;
  dateLabel: string;
  forecastExpected: number;
  forecastLow: number;
  forecastHigh: number;
  planningRangeLabel: string;
  posture: string;
  serviceState: string;
  headline: string;
  summary: string;
  topDrivers: Driver[];
  majorUncertainties: string[];
  baselineComparison?: ForecastBaselineComparison | null;
  recentChange?: ForecastRecentChange | null;
  status: ForecastCardStatus;
  weather?: ForecastWeather | null;
};

export type DashboardState = {
  referenceDate: string;
  cards: ForecastCard[];
  contextLine: string;
  latestRefresh: Record<string, unknown> | null;
  pendingNotificationCount: number;
  openServiceStateSuggestions: Array<Record<string, unknown>>;
  operatorAttentionSummary: OperatorAttentionSummary | null;
  missingActuals: Array<{
    serviceDate: string;
    serviceWindow: string;
    forecastExpected: number;
  }>;
  servicePlanWindow: {
    promptDate: string;
    windowStart: string;
    windowEnd: string;
    windowLabel: string;
    dueCount: number;
    pendingDates: string[];
    entries: Array<{
      serviceDate: string;
      serviceState: string;
      plannedTotalCovers: number | null;
      estimatedReductionPct: number | null;
      note: string;
      reviewed: boolean;
      updatedAt?: string | null;
    }>;
  } | null;
  learningAgenda: LearningAgendaItem[];
};

export type CommunicationPayload = {
  category?: string | null;
  what_is_true_now?: string | null;
  why_it_matters?: string | null;
  what_i_need_from_you?: string | null;
  what_is_still_uncertain?: string | null;
  one_question?: string | null;
  facts?: Record<string, unknown> | null;
};

export type OperatorAttentionSection = {
  communicationPayload?: CommunicationPayload | null;
  service_date?: string | null;
  service_window?: string | null;
  [key: string]: unknown;
};

export type OperatorAttentionSummary = {
  moment_label?: string | null;
  primary_focus_key?: string | null;
  ordered_section_keys?: string[] | null;
  latest_material_change: OperatorAttentionSection | null;
  current_operational_watchout: OperatorAttentionSection | null;
  pending_operator_action: OperatorAttentionSection | null;
  current_uncertainty: OperatorAttentionSection | null;
  best_next_question: OperatorAttentionSection | null;
};

export type LearningAgendaItem = {
  agenda_key: string;
  status: string;
  question_kind: string;
  communicationPayload?: CommunicationPayload | null;
  service_date: string | null;
};

export type OperatorRecord = {
  operatorId: string;
  restaurantName: string;
  canonicalAddress?: string | null;
  city?: string | null;
  timezone?: string | null;
  lat?: number | null;
  lon?: number | null;
  neighborhoodType: string;
  demandMix: string;
  patioEnabled: boolean;
  patioSeatCapacity?: number | null;
  patioSeasonMode?: string | null;
  onboardingState: string;
};

export type Workspace = {
  mode: "onboarding" | "operations";
  operators: OperatorSummary[];
  operator: OperatorRecord | null;
  setupBootstrap: SetupBootstrapState;
  operatorText: OperatorTextContract;
  onboarding: OnboardingState;
  dashboard: DashboardState | null;
  chat: {
    phase: string;
    messages: ChatMessage[];
    placeholder: string;
  };
};

export type BootstrapPayload = {
  operators: OperatorSummary[];
  defaultOperatorId: string | null;
  operatorText: OperatorTextContract;
  onboarding: {
    options: OnboardingOptions;
    draft: OnboardingDraft;
  };
};
