import {
  ChangeEvent,
  Fragment,
  FormEvent,
  KeyboardEvent,
  ReactNode,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import ReactMarkdown from "react-markdown";

import {
  completeOnboarding,
  deleteOperator,
  getBootstrap,
  getChatHistory,
  getWorkspace,
  refreshWorkspace,
  reviewHistoricalUpload,
  retrySetupBootstrap,
  sendChatMessage,
  submitActualEntry,
  submitServicePlan,
} from "./api";
import type {
  ActualEntryDraft,
  BootstrapPayload,
  ChatMessage,
  CommunicationPayload,
  DashboardState,
  ForecastCard,
  HistoricalUploadReview,
  LearningAgendaItem,
  OnboardingDraft,
  OperatorAttentionSection,
  OperatorAttentionSummary,
  OperatorTextContract,
  OperatorSummary,
  ServicePlanEntryDraft,
  SetupBootstrapState,
  SuggestedMessage,
  Workspace,
} from "./types";
import { WeatherGlyph, weatherLabel } from "./components/WeatherGlyph";
import { StatusPipRow, type PipLabelMap } from "./components/StatusPip";
import { GlyphLegend } from "./components/GlyphLegend";

type Page = "chat" | "forecast" | "plans";
type DashboardMode = "detail" | "table";
type Notice = { tone: "success" | "warning" | "error" | "info"; text: string } | null;

const SELECTED_OPERATOR_STORAGE_KEY = "stormready:selected-operator";
const SIDEBAR_COLLAPSED_STORAGE_KEY = "stormready:sidebar-collapsed";

const PAGE_LABELS: Record<Page, string> = {
  chat: "Chat",
  forecast: "Forecast",
  plans: "Plans & Actuals",
};

function renderCommunicationPayload(
  payload: CommunicationPayload | null | undefined,
  includeQuestion = true
): string {
  if (!payload) return "";
  const parts = [
    payload.what_is_true_now,
    payload.why_it_matters,
    payload.what_i_need_from_you,
    payload.what_is_still_uncertain,
    includeQuestion ? payload.one_question : null
  ];
  return parts
    .map((part) => String(part ?? "").trim())
    .filter(Boolean)
    .map((part) => (/^[A-Z0-9].*[.!?]$/.test(part) ? part : `${part}${part.endsWith("?") ? "" : "."}`))
    .join(" ");
}

function sectionText(section: OperatorAttentionSection | null | undefined, includeQuestion = true): string {
  return renderCommunicationPayload(section?.communicationPayload ?? null, includeQuestion).trim();
}

function learningQuestionText(item: LearningAgendaItem): string {
  return renderCommunicationPayload(item.communicationPayload ?? null, true).trim();
}

function formatCopy(template: string | undefined, values: Record<string, string | number>): string {
  let text = template ?? "";
  for (const [key, value] of Object.entries(values)) {
    text = text.split(`{${key}}`).join(String(value));
  }
  return text;
}

function statusPipLabels(operatorText: OperatorTextContract): PipLabelMap {
  return operatorText.statusPipLabels as PipLabelMap;
}

function comparisonTone(deltaPct: number | null | undefined): "up" | "down" | "neutral" {
  if (typeof deltaPct !== "number" || Number.isNaN(deltaPct) || deltaPct === 0) {
    return "neutral";
  }
  return deltaPct > 0 ? "up" : "down";
}

function App() {
  const [referenceDate] = useState<string | null>(() => new URLSearchParams(window.location.search).get("referenceDate"));
  const [bootstrap, setBootstrap] = useState<BootstrapPayload | null>(null);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [selectedOperatorId, setSelectedOperatorId] = useState<string | null>(null);
  const [creatingOperator, setCreatingOperator] = useState(false);
  const [page, setPage] = useState<Page>("chat");
  const [initialRoutedOperator, setInitialRoutedOperator] = useState<string | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY) === "1";
  });
  const [dashboardMode, setDashboardMode] = useState<DashboardMode>("detail");
  const [activeServiceDate, setActiveServiceDate] = useState<string | null>(null);
  const [showManualPlanEditor, setShowManualPlanEditor] = useState(false);
  const [draft, setDraft] = useState<OnboardingDraft | null>(null);
  const [onboardingStep, setOnboardingStep] = useState(1);
  const [composerValue, setComposerValue] = useState("");
  const [suggestedMessages, setSuggestedMessages] = useState<SuggestedMessage[]>([]);
  const [pendingUserMessage, setPendingUserMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [error, setError] = useState<string | null>(null);
  const [bootstrapLogs, setBootstrapLogs] = useState<string[]>([]);
  const [olderChatMessages, setOlderChatMessages] = useState<ChatMessage[]>([]);
  const [chatHasMore, setChatHasMore] = useState<boolean>(true);
  const [chatLoadingOlder, setChatLoadingOlder] = useState<boolean>(false);
  const [reviewingUpload, setReviewingUpload] = useState(false);

  useEffect(() => {
    void initialize();
  }, []);

  useEffect(() => {
    if (!selectedOperatorId || !workspace?.setupBootstrap || !isSetupBootstrapActive(workspace.setupBootstrap.status)) {
      return;
    }
    const pollHandle = window.setInterval(() => {
      void loadWorkspace(selectedOperatorId, { silent: true });
    }, 5000);
    return () => window.clearInterval(pollHandle);
  }, [selectedOperatorId, workspace?.setupBootstrap?.status]);

  useEffect(() => {
    if (!selectedOperatorId || workspace?.mode !== "operations") {
      return;
    }
    const pollHandle = window.setInterval(() => {
      void loadWorkspace(selectedOperatorId, { silent: true });
    }, 60000);
    return () => window.clearInterval(pollHandle);
  }, [selectedOperatorId, workspace?.mode]);

  // Persist sidebar collapse state
  useEffect(() => {
    window.localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, sidebarCollapsed ? "1" : "0");
  }, [sidebarCollapsed]);

  // Cmd/Ctrl + \ toggles sidebar
  useEffect(() => {
    function onKey(e: globalThis.KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === "\\") {
        e.preventDefault();
        setSidebarCollapsed((c) => !c);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Ambient weather tinting: set data-weather on <html> from the selected night's
  // conditionCode. Drives :root[data-weather="..."] CSS overrides in styles.css.
  useEffect(() => {
    const cards = workspace?.dashboard?.cards;
    if (!cards || cards.length === 0) {
      document.documentElement.removeAttribute("data-weather");
      return;
    }
    const card = cards.find((c) => c.serviceDate === activeServiceDate) ?? cards[0];
    const code = card?.weather?.conditionCode;
    if (code && code !== "unknown") {
      document.documentElement.setAttribute("data-weather", code);
    } else {
      document.documentElement.removeAttribute("data-weather");
    }
  }, [workspace?.dashboard?.cards, activeServiceDate]);

  // Reset chat pagination state when switching operators
  useEffect(() => {
    setOlderChatMessages([]);
    setChatHasMore(true);
    setChatLoadingOlder(false);
  }, [selectedOperatorId]);

  // Merge prepended history with the workspace's current chat tail.
  // De-dupe by messageId in case the workspace refresh overlaps with what
  // we've already loaded into the older buffer.
  const allChatMessages = useMemo<ChatMessage[]>(() => {
    const tail = workspace?.chat.messages ?? [];
    if (olderChatMessages.length === 0) return tail;
    const seen = new Set<number>();
    const merged: ChatMessage[] = [];
    for (const m of [...olderChatMessages, ...tail]) {
      if (m.messageId != null) {
        if (seen.has(m.messageId)) continue;
        seen.add(m.messageId);
      }
      merged.push(m);
    }
    return merged;
  }, [olderChatMessages, workspace?.chat.messages]);

  const oldestKnownMessageId = useMemo<number | null>(() => {
    for (const m of allChatMessages) {
      if (m.messageId != null) return m.messageId;
    }
    return null;
  }, [allChatMessages]);

  async function handleLoadOlderChat() {
    if (
      !selectedOperatorId ||
      chatLoadingOlder ||
      !chatHasMore ||
      oldestKnownMessageId == null
    ) {
      return;
    }
    setChatLoadingOlder(true);
    try {
      const response = await getChatHistory(selectedOperatorId, oldestKnownMessageId, 30);
      // The new messages should already be in chronological order per backend.
      setOlderChatMessages((prev) => [...response.messages, ...prev]);
      setChatHasMore(response.hasMore);
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setChatLoadingOlder(false);
    }
  }

  async function initialize() {
    setLoading(true);
    setError(null);
    try {
      const boot = await getBootstrap();
      setBootstrap(boot);
      const persistedOperator = window.localStorage.getItem(SELECTED_OPERATOR_STORAGE_KEY);
      const available = new Set(boot.operators.map((operator) => operator.operatorId));
      const nextOperatorId =
        persistedOperator && available.has(persistedOperator)
          ? persistedOperator
          : boot.defaultOperatorId;
      if (nextOperatorId) {
        await loadWorkspace(nextOperatorId);
      } else {
        setSelectedOperatorId(null);
        setCreatingOperator(true);
        setDraft(boot.onboarding.draft);
        setWorkspace(null);
      }
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setLoading(false);
    }
  }

  async function loadWorkspace(operatorId: string, options: { silent?: boolean } = {}) {
    const silent = options.silent ?? false;
    const previousSetupStatus = workspace?.setupBootstrap?.status;
    if (!silent) {
      setLoading(true);
    }
    setError(null);
    try {
      const nextWorkspace = await getWorkspace(operatorId, referenceDate);
      setWorkspace(nextWorkspace);
      setSuggestedMessages([]);
      setSelectedOperatorId(operatorId);
      window.localStorage.setItem(SELECTED_OPERATOR_STORAGE_KEY, operatorId);
      setCreatingOperator(false);
      setDraft(nextWorkspace.onboarding.draft);
      setBootstrapLogs(nextWorkspace.setupBootstrap?.steps ?? []);
      setActiveServiceDate((current) => current ?? nextWorkspace.dashboard?.cards[0]?.serviceDate ?? null);
      setShowManualPlanEditor(false);
      // Smart default routing: land on Plans if there are pending plans or due actuals,
      // otherwise Chat. Runs only once per operator load (not on silent polls).
      if (!silent && initialRoutedOperator !== operatorId && nextWorkspace.mode === "operations") {
        const dash = nextWorkspace.dashboard;
        const hasPendingPlan = !!dash?.servicePlanWindow;
        const hasMissingActuals = (dash?.missingActuals?.length ?? 0) > 0;
        setPage(hasPendingPlan || hasMissingActuals ? "plans" : "chat");
        setInitialRoutedOperator(operatorId);
      }
      if (previousSetupStatus && isSetupBootstrapActive(previousSetupStatus)) {
        if (nextWorkspace.setupBootstrap?.status === "completed") {
          setNotice({ tone: "success", text: nextWorkspace.setupBootstrap.message });
        } else if (nextWorkspace.setupBootstrap?.status === "failed") {
          setNotice({ tone: "warning", text: nextWorkspace.setupBootstrap.message });
        }
      }
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      if (!silent) {
        setLoading(false);
      }
    }
  }

  function beginNewOperator() {
    setCreatingOperator(true);
    setWorkspace(null);
    setSuggestedMessages([]);
    setSelectedOperatorId(null);
    setDraft(bootstrap?.onboarding.draft ?? null);
    setOnboardingStep(1);
    setBootstrapLogs([]);
    setNotice(null);
  }

  function handleDraftChange<K extends keyof OnboardingDraft>(key: K, value: OnboardingDraft[K]) {
    setDraft((current) => (current ? { ...current, [key]: value } : current));
  }

  async function handleReviewHistoricalUpload(file: File) {
    if (!draft) {
      return;
    }
    setReviewingUpload(true);
    setError(null);
    setNotice(null);
    try {
      const content = await file.text();
      const review: HistoricalUploadReview = await reviewHistoricalUpload(file.name, content);
      setDraft((current) => {
        if (!current) {
          return current;
        }
        return {
          ...current,
          forecastInputMode: "historical_upload",
          historicalUploadToken: review.upload_token,
          historicalUploadReview: review,
          monThu: review.baseline_values.mon_thu ?? current.monThu,
          fri: review.baseline_values.fri ?? current.fri,
          sat: review.baseline_values.sat ?? current.sat,
          sun: review.baseline_values.sun ?? current.sun,
        };
      });
      setNotice({
        tone: review.accepted ? "info" : "warning",
        text: review.summary,
      });
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setReviewingUpload(false);
    }
  }

  async function handleSubmitOnboarding(event: FormEvent) {
    event.preventDefault();
    if (!draft) {
      return;
    }
    setSubmitting(true);
    setError(null);
    setNotice(null);
    try {
      const payload = {
        ...draft,
        operatorId: creatingOperator ? null : workspace?.operator?.operatorId ?? null,
      };
      const response = await completeOnboarding(payload);
      setWorkspace(response.workspace);
      setSelectedOperatorId(response.operatorId);
      window.localStorage.setItem(SELECTED_OPERATOR_STORAGE_KEY, response.operatorId);
      setCreatingOperator(false);
      setActiveServiceDate(response.workspace.dashboard?.cards[0]?.serviceDate ?? null);
      if (response.bootstrap?.message) {
        setNotice({
          tone:
            response.bootstrap.status === "failed"
              ? "warning"
              : response.bootstrap.status === "completed"
                ? "success"
                : "info",
          text: response.bootstrap.message,
        });
        setBootstrapLogs(response.bootstrap.steps ?? []);
      } else {
        setNotice({ tone: "success", text: "Restaurant saved." });
      }
      const refreshedBootstrap = await getBootstrap();
      setBootstrap(refreshedBootstrap);
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleSendMessage(message: string, learningAgendaKey?: string | null) {
    const trimmed = message.trim();
    if (!selectedOperatorId || !trimmed) {
      return;
    }
    // Clear the composer and stage the optimistic bubble immediately so the
    // operator gets visible acknowledgement before the round-trip resolves.
    setComposerValue("");
    setPendingUserMessage(trimmed);
    setSuggestedMessages([]);
    setSubmitting(true);
    setError(null);
    try {
      const response = await sendChatMessage(selectedOperatorId, trimmed, referenceDate, learningAgendaKey);
      setWorkspace(response.workspace);
      setSuggestedMessages(response.suggestedMessages ?? []);
      if (response.operatorId !== selectedOperatorId) {
        setSelectedOperatorId(response.operatorId);
        window.localStorage.setItem(SELECTED_OPERATOR_STORAGE_KEY, response.operatorId);
      }
    } catch (caught) {
      setError(getErrorMessage(caught));
      // Restore the unsent text so the operator can edit and retry.
      setComposerValue(trimmed);
    } finally {
      setPendingUserMessage(null);
      setSubmitting(false);
    }
  }

  async function handleRefresh() {
    if (!selectedOperatorId) {
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const response = await refreshWorkspace(selectedOperatorId, "operator requested", referenceDate);
      setWorkspace(response.workspace);
      setNotice({
        tone: response.result.success ? "success" : "warning",
        text: response.result.message,
      });
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleSubmitActualEntry(payload: ActualEntryDraft) {
    if (!selectedOperatorId) {
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const response = await submitActualEntry(selectedOperatorId, payload, referenceDate);
      setWorkspace(response.workspace);
      setNotice({
        tone: response.result.success ? "success" : "warning",
        text: response.result.message,
      });
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleSubmitServicePlan(payload: ServicePlanEntryDraft) {
    if (!selectedOperatorId) {
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const response = await submitServicePlan(selectedOperatorId, payload, referenceDate);
      setWorkspace(response.workspace);
      setNotice({
        tone: response.result.success ? "success" : "warning",
        text: response.result.message,
      });
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleRetrySetup() {
    if (!selectedOperatorId) {
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const response = await retrySetupBootstrap(selectedOperatorId, referenceDate);
      setWorkspace(response.workspace);
      setBootstrapLogs(response.setupBootstrap?.steps ?? []);
      if (response.setupBootstrap?.message) {
        setNotice({ tone: "info", text: response.setupBootstrap.message });
      }
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDeleteCurrentOperator() {
    if (!selectedOperatorId) {
      return;
    }
    const confirmed = window.confirm("Delete this restaurant profile and all local forecast state?");
    if (!confirmed) {
      return;
    }
    setDeleting(true);
    setError(null);
    try {
      await deleteOperator(selectedOperatorId);
      window.localStorage.removeItem(SELECTED_OPERATOR_STORAGE_KEY);
      setNotice({ tone: "success", text: "Restaurant profile deleted." });
      await initialize();
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setDeleting(false);
    }
  }

  const operatorList = workspace?.operators ?? bootstrap?.operators ?? [];
  const onboardingState = workspace?.onboarding ?? (bootstrap ? { ...bootstrap.onboarding, forecastReady: false, summary: null } : null);
  const onboardingDraft = draft ?? onboardingState?.draft ?? null;
  const setupBootstrap = workspace?.setupBootstrap ?? null;
  const showSetupBootstrapScreen = shouldShowSetupBootstrapScreen(workspace);
  const selectedCard =
    workspace?.dashboard?.cards.find((card) => card.serviceDate === activeServiceDate) ??
    workspace?.dashboard?.cards[0] ??
    null;

  function scrollToPanel(panelId: string) {
    window.setTimeout(() => {
      document.getElementById(panelId)?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 0);
  }

  function handleOpenSelectedNightPlan() {
    setShowManualPlanEditor(true);
    scrollToPanel("selected-night-plan-panel");
  }

  function handleOpenPlanningSurface() {
    if (workspace?.dashboard?.servicePlanWindow) {
      scrollToPanel("service-plan-panel");
      return;
    }
    handleOpenSelectedNightPlan();
  }

  function handleOpenActualsPanel() {
    if (!workspace?.dashboard?.missingActuals.length) {
      setNotice({ tone: "info", text: "No missing dinner actuals are due right now." });
      return;
    }
    scrollToPanel("actuals-due-panel");
  }

  function handleSeedComposerNote() {
    setPage("chat");
    setComposerValue((current) => {
      if (current.trim()) {
        return current;
      }
      if (selectedCard) {
        return `Note for ${selectedCard.serviceDate}: `;
      }
      return "Note: ";
    });
    window.setTimeout(() => {
      document.getElementById("chat-composer-input")?.focus();
    }, 0);
  }

  const inOperations = !loading && !showSetupBootstrapScreen && workspace?.mode === "operations" && !!workspace.dashboard;
  const dashboard = workspace?.dashboard ?? null;
  const operatorText = workspace?.operatorText ?? bootstrap?.operatorText ?? null;
  const chatBadge = dashboard?.learningAgenda?.filter((item) => item.status === "open").length ?? 0;
  const plansBadge =
    (dashboard?.servicePlanWindow?.dueCount ?? 0) + (dashboard?.missingActuals?.length ?? 0);
  const momentLabel = dashboard?.operatorAttentionSummary?.moment_label ?? null;

  return (
    <div className={`app-shell ${sidebarCollapsed ? "is-sidebar-collapsed" : ""}`}>
      <div className="app-backdrop" />
      <aside className="app-sidebar" aria-label="Primary navigation">
        <div className="sidebar-brand">
          <div className="brand-mark">SR</div>
          {!sidebarCollapsed ? (
            <div className="sidebar-brand-meta">
              <div className="brand-kicker">StormReady</div>
              <div className="sidebar-brand-tagline">Weather-aware dinner forecasting</div>
            </div>
          ) : null}
        </div>

        {workspace?.operator && !sidebarCollapsed ? (
          <div className="sidebar-restaurant">
            <div className="sidebar-section-label">Current restaurant</div>
            <h1 className="sidebar-restaurant-name">{workspace.operator.restaurantName}</h1>
            {workspace.operator.city ? (
              <div className="sidebar-restaurant-city">{workspace.operator.city}</div>
            ) : null}
            {dashboard?.contextLine ? (
              <p className="sidebar-context-line">{dashboard.contextLine}</p>
            ) : null}
            {referenceDate ? (
              <div className="sidebar-reference-date">Viewing: {dashboard?.referenceDate}</div>
            ) : null}
          </div>
        ) : null}

        {inOperations ? (
          <nav className="sidebar-nav" aria-label="Pages">
            <SidebarNavLink
              page="chat"
              current={page}
              onClick={setPage}
              label={PAGE_LABELS.chat}
              badge={chatBadge}
              icon={<ChatIcon />}
              collapsed={sidebarCollapsed}
            />
            <SidebarNavLink
              page="forecast"
              current={page}
              onClick={setPage}
              label={PAGE_LABELS.forecast}
              icon={<DashboardIcon />}
              collapsed={sidebarCollapsed}
            />
            <SidebarNavLink
              page="plans"
              current={page}
              onClick={setPage}
              label={PAGE_LABELS.plans}
              badge={plansBadge}
              icon={<PlanIcon />}
              collapsed={sidebarCollapsed}
            />
          </nav>
        ) : null}

        {!sidebarCollapsed ? (
          <div className="sidebar-section sidebar-restaurants-section">
            <div className="sidebar-section-label-row">
              <span className="sidebar-section-label">Restaurants</span>
              <button className="text-button" onClick={beginNewOperator}>
                + Add
              </button>
            </div>
            <div className="operator-list">
              {operatorList.length === 0 ? (
                <div className="sidebar-empty">No restaurants yet. Start with the onboarding wizard.</div>
              ) : (
                operatorList.map((operator) => (
                  <button
                    key={operator.operatorId}
                    className={`operator-pill ${selectedOperatorId === operator.operatorId && !creatingOperator ? "is-active" : ""}`}
                    onClick={() => void loadWorkspace(operator.operatorId)}
                  >
                    <span>{operator.restaurantName}</span>
                    <span className={`operator-status ${operator.forecastReady ? "ready" : "pending"}`}>
                      {operator.forecastReady ? "Ready" : "Setup"}
                    </span>
                  </button>
                ))
              )}
            </div>
          </div>
        ) : null}

        <div className="sidebar-footer">
          {workspace?.operator && !sidebarCollapsed ? (
            <button
              className="ghost-button danger"
              onClick={() => void handleDeleteCurrentOperator()}
              disabled={deleting}
            >
              {deleting ? "Deleting…" : "Delete current"}
            </button>
          ) : null}
          <button
            type="button"
            className="sidebar-collapse-toggle"
            onClick={() => setSidebarCollapsed((c) => !c)}
            aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            title={sidebarCollapsed ? "Expand sidebar (⌘\\)" : "Collapse sidebar (⌘\\)"}
          >
            {sidebarCollapsed ? "›" : "‹"}
          </button>
        </div>
      </aside>

      <main className="app-main">
        {inOperations ? (
          <header className="page-header">
            <div className="page-header-titles">
              {momentLabel ? <div className="page-header-eyebrow">{momentLabel}</div> : null}
              <div className="page-header-title">{PAGE_LABELS[page]}</div>
            </div>
            <div className="page-header-actions">
              <button
                className="primary-button"
                onClick={() => void handleRefresh()}
                disabled={submitting}
              >
                {submitting ? "Working…" : "Refresh"}
              </button>
            </div>
          </header>
        ) : null}

        {loading ? <div className="hero-card">Loading StormReady…</div> : null}
        {error ? <NoticeBanner tone="error" text={error} /> : null}
        {notice ? <NoticeBanner tone={notice.tone} text={notice.text} /> : null}

        {!loading && showSetupBootstrapScreen ? (
          <SetupBootstrapPanel
            operatorName={workspace?.operator?.restaurantName ?? "your restaurant"}
            bootstrap={setupBootstrap}
            submitting={submitting}
            onRetry={() => void handleRetrySetup()}
          />
        ) : null}

        {!loading && !showSetupBootstrapScreen && onboardingDraft && (creatingOperator || workspace?.mode === "onboarding" || !workspace) ? (
          <OnboardingWizard
            draft={onboardingDraft}
            onboarding={onboardingState}
            currentStep={onboardingStep}
            submitting={submitting}
            reviewingUpload={reviewingUpload}
            bootstrapLogs={bootstrapLogs}
            onStepChange={setOnboardingStep}
            onChange={handleDraftChange}
            onReviewUpload={handleReviewHistoricalUpload}
            onSubmit={handleSubmitOnboarding}
          />
        ) : null}

        {inOperations && dashboard && operatorText ? (
          <>
            {page === "chat" ? (
              <>
                <ForecastStrip
                  cards={dashboard.cards}
                  operatorText={operatorText}
                  activeServiceDate={activeServiceDate}
                  onSelect={setActiveServiceDate}
                />
                <ChatWorkspace
                  card={selectedCard}
                  dashboard={dashboard}
                  operatorText={operatorText}
                  messages={allChatMessages}
                  hasMoreHistory={chatHasMore}
                  loadingOlder={chatLoadingOlder}
                  onLoadOlder={() => void handleLoadOlderChat()}
                  composerValue={composerValue}
                  submitting={submitting}
                  pendingUserMessage={pendingUserMessage}
                  suggestedMessages={suggestedMessages}
                  placeholder={workspace.chat.placeholder}
                  onOpenPlanEditor={() => {
                    setPage("plans");
                    handleOpenPlanningSurface();
                  }}
                  onOpenActualsPanel={() => {
                    setPage("plans");
                    handleOpenActualsPanel();
                  }}
                  onSeedComposerNote={handleSeedComposerNote}
                  onComposerChange={setComposerValue}
                  onSubmit={() => void handleSendMessage(composerValue)}
                  onSendMessage={(msg, learningAgendaKey) => void handleSendMessage(msg, learningAgendaKey)}
                  onAskAboutNight={(serviceDate) => void handleSendMessage(`Tell me about ${serviceDate}`)}
                />
              </>
            ) : null}

            {page === "forecast" ? (
              <>
                <div className="dashboard-toolbar">
                  <div className="section-kicker">View</div>
                  <div className="icon-switch" aria-label="Forecast view selector">
                    <button
                      className={`icon-tab ${dashboardMode === "detail" ? "is-active" : ""}`}
                      onClick={() => setDashboardMode("detail")}
                      type="button"
                      aria-label="Detail view"
                    >
                      <DetailIcon />
                    </button>
                    <button
                      className={`icon-tab ${dashboardMode === "table" ? "is-active" : ""}`}
                      onClick={() => setDashboardMode("table")}
                      type="button"
                      aria-label="Table view"
                    >
                      <TableIcon />
                    </button>
                  </div>
                  <div className="dashboard-toolbar-spacer" />
                  <GlyphLegend />
                </div>

                {dashboardMode === "detail" ? (
                  <PyramidWorkspace
                    cards={dashboard.cards}
                    attentionSummary={dashboard.operatorAttentionSummary}
                    operatorText={operatorText}
                    activeServiceDate={activeServiceDate}
                    onSelect={setActiveServiceDate}
                  />
                ) : null}

                {dashboardMode === "table" ? <TableWorkspace cards={dashboard.cards} /> : null}
              </>
            ) : null}

            {page === "plans" ? (
              <>
                <header className="plans-header">
                  <div className="plans-header-mark" aria-hidden>§</div>
                  <div className="plans-header-text">
                    <div className="plans-header-kicker">Operations Desk</div>
                    <h1 className="plans-header-title">Plans &amp; Actuals</h1>
                    <p className="plans-header-sub">
                      Viewing {formatPlansHeaderDate(dashboard.referenceDate)} &middot; save known service changes and log recent dinners.
                    </p>
                  </div>
                </header>
                <ForecastStrip
                  cards={dashboard.cards}
                  operatorText={operatorText}
                  activeServiceDate={activeServiceDate}
                  onSelect={setActiveServiceDate}
                />
                <OperationsQueue
                  dashboard={dashboard}
                  onOpenPlan={handleOpenPlanningSurface}
                  onOpenActuals={handleOpenActualsPanel}
                  onSwitchToChat={() => setPage("chat")}
                />
                {dashboard.servicePlanWindow ? (
                  <div id="service-plan-panel">
                    <ServicePlanPanel
                      window={dashboard.servicePlanWindow}
                      operatorText={operatorText}
                      submitting={submitting}
                      onSubmit={(payload) => void handleSubmitServicePlan(payload)}
                    />
                  </div>
                ) : null}
                {showManualPlanEditor && selectedCard ? (
                  <div id="selected-night-plan-panel">
                    <SelectedNightPlanPanel
                      card={selectedCard}
                      operatorText={operatorText}
                      submitting={submitting}
                      onClose={() => setShowManualPlanEditor(false)}
                      onSubmit={(payload) => void handleSubmitServicePlan(payload)}
                    />
                  </div>
                ) : null}
                {dashboard.missingActuals.length ? (
                  <div id="actuals-due-panel">
                    <ActualsDuePanel
                      missingActuals={dashboard.missingActuals}
                      operatorText={operatorText}
                      submitting={submitting}
                      onSubmit={(payload) => void handleSubmitActualEntry(payload)}
                    />
                  </div>
                ) : null}
              </>
            ) : null}
          </>
        ) : null}
      </main>
    </div>
  );
}

function SidebarNavLink(props: {
  page: Page;
  current: Page;
  onClick: (page: Page) => void;
  label: string;
  icon: ReactNode;
  badge?: number;
  collapsed: boolean;
}) {
  const isActive = props.current === props.page;
  return (
    <button
      type="button"
      className={`sidebar-nav-link ${isActive ? "is-active" : ""}`}
      onClick={() => props.onClick(props.page)}
      aria-current={isActive ? "page" : undefined}
      title={props.collapsed ? props.label : undefined}
    >
      <span className="sidebar-nav-link-icon">{props.icon}</span>
      {!props.collapsed ? <span className="sidebar-nav-link-label">{props.label}</span> : null}
      {props.badge && props.badge > 0 ? (
        <span className="sidebar-nav-link-badge">{props.badge}</span>
      ) : null}
    </button>
  );
}

function OnboardingWizard(props: {
  draft: OnboardingDraft;
  onboarding: Workspace["onboarding"] | null;
  currentStep: number;
  submitting: boolean;
  reviewingUpload: boolean;
  bootstrapLogs: string[];
  onStepChange: (step: number) => void;
  onChange: <K extends keyof OnboardingDraft>(key: K, value: OnboardingDraft[K]) => void;
  onReviewUpload: (file: File) => Promise<void>;
  onSubmit: (event: FormEvent) => void;
}) {
  const {
    draft,
    onboarding,
    currentStep,
    submitting,
    reviewingUpload,
    bootstrapLogs,
    onStepChange,
    onChange,
    onReviewUpload,
    onSubmit,
  } = props;
  const options = onboarding?.options;
  if (!options) {
    return null;
  }

  const steps = [
    { id: 1, label: "Basics" },
    { id: 2, label: "Forecast Inputs" },
    { id: 3, label: "Optional Context" },
  ];
  const usingHistoricalUpload = draft.forecastInputMode === "historical_upload";
  const historicalReview = draft.historicalUploadReview;

  const stepValid =
    currentStep === 1
      ? Boolean(draft.restaurantName.trim() && draft.canonicalAddress.trim())
      : currentStep === 2
        ? usingHistoricalUpload
          ? Boolean(draft.historicalUploadToken && historicalReview?.accepted)
          : draft.monThu > 0 && draft.fri > 0 && draft.sat > 0 && draft.sun > 0
        : true;

  function handleWizardSubmit(event: FormEvent) {
    event.preventDefault();
    if (currentStep < 3) {
      if (stepValid) {
        onStepChange(currentStep + 1);
      }
      return;
    }
    onSubmit(event);
  }

  function handleWizardKeyDown(event: KeyboardEvent<HTMLFormElement>) {
    if (event.key !== "Enter" || event.shiftKey) {
      return;
    }
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    if (target.tagName === "TEXTAREA") {
      return;
    }
    if (target.tagName === "BUTTON") {
      return;
    }
    event.preventDefault();
    if (!stepValid) {
      return;
    }
    if (currentStep < 3) {
      onStepChange(currentStep + 1);
      return;
    }
    event.currentTarget.requestSubmit();
  }

  function selectForecastInputMode(mode: string) {
    onChange("forecastInputMode", mode);
  }

  async function handleUploadFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }
    await onReviewUpload(file);
  }

  return (
    <section className="panel editor-panel wizard-panel" data-tone="onboarding">
      <header className="editor-panel-header">
        <div className="editor-panel-mark" aria-hidden>§</div>
        <div className="editor-panel-text">
          <div className="editor-panel-kicker">Structured setup</div>
          <h2 className="editor-panel-title">Get your restaurant forecast-ready</h2>
          <p className="editor-panel-sub">
            Start with your weekly dinner demand anchor, then add the context that shapes weather impact and explanations.
          </p>
        </div>
      </header>

      <ol className="wizard-stepper" aria-label="Setup steps">
        {steps.map((step) => {
          const state =
            currentStep === step.id ? "current" : currentStep > step.id ? "done" : "todo";
          return (
            <li key={step.id} className={`wizard-step is-${state}`}>
              <button type="button" onClick={() => onStepChange(step.id)} className="wizard-step-button">
                <span className="wizard-step-num" aria-hidden>{String(step.id).padStart(2, "0")}</span>
                <span className="wizard-step-label">
                  <span className="wizard-step-kicker">Step {step.id}</span>
                  <strong>{step.label}</strong>
                </span>
              </button>
            </li>
          );
        })}
      </ol>

      {onboarding.summary?.improvements?.[0] ? (
        <div className="editor-banner">
          <span className="editor-banner-pip" aria-hidden />
          <span>{onboarding.summary.improvements[0]}</span>
        </div>
      ) : null}

      <form className="actuals-form" onSubmit={handleWizardSubmit} onKeyDown={handleWizardKeyDown}>
        {currentStep === 1 ? (
          <div className="form-grid">
            <label>
              <span>Restaurant name</span>
              <input value={draft.restaurantName} onChange={(event) => onChange("restaurantName", event.target.value)} />
            </label>
            <label className="span-2">
              <span>Street address</span>
              <input value={draft.canonicalAddress} onChange={(event) => onChange("canonicalAddress", event.target.value)} />
            </label>
            <label>
              <span>City</span>
              <input value={draft.city} onChange={(event) => onChange("city", event.target.value)} />
            </label>
            <label>
              <span>Timezone</span>
              <select value={draft.timezone} onChange={(event) => onChange("timezone", event.target.value)}>
                {options.timezones.map((timezone) => (
                  <option key={timezone} value={timezone}>
                    {timezone}
                  </option>
                ))}
              </select>
            </label>
          </div>
        ) : null}

        {currentStep === 2 ? (
          <div className="form-grid">
            <div className="subtle-banner span-2">
              Choose whether you want to enter the dinner baseline by hand or derive it from a 12-month history upload.
            </div>
            <div className="input-mode-grid span-2" role="group" aria-label="Forecast input mode">
              {options.forecastInputModes.map((option) => {
                const active = draft.forecastInputMode === option.value;
                return (
                  <button
                    key={option.value}
                    type="button"
                    className={`input-mode-card ${active ? "is-active" : ""}`}
                    aria-pressed={active}
                    onClick={() => selectForecastInputMode(option.value)}
                  >
                    <strong>{option.label}</strong>
                    {option.description ? <span>{option.description}</span> : null}
                  </button>
                );
              })}
            </div>

            {usingHistoricalUpload ? (
              <>
                <div className="status-card is-info span-2">
                  <strong>Upload history instead of typing baselines</strong>
                  <div className="status-line">We will review the file, derive Mon-Thu / Fri / Sat / Sun, and use the same upload to prepare a local weather reference.</div>
                </div>

                <div className="checkbox-cluster span-2 onboarding-requirements">
                  <span>Upload requirements</span>
                  {options.historicalUploadRequirements.map((item) => (
                    <div key={item} className="status-line">{item}</div>
                  ))}
                </div>

                <label className="span-2">
                  <span>Historical cover file</span>
                  <input
                    type="file"
                    accept={options.historicalUploadAcceptedExtensions.join(",")}
                    onChange={(event) => void handleUploadFileChange(event)}
                    disabled={reviewingUpload}
                  />
                </label>

                {reviewingUpload ? (
                  <div className="status-card is-info span-2">
                    <strong>Reviewing upload</strong>
                    <div className="status-line">Checking the format, season coverage, and derived baselines.</div>
                  </div>
                ) : null}

                {historicalReview ? (
                  <div className={`status-card span-2 ${historicalReview.accepted ? "is-info" : "is-warning"}`}>
                    <strong>{historicalReview.file_name}</strong>
                    <div className="status-line">{historicalReview.summary}</div>
                    <div className="upload-review-grid">
                      <div>Usable rows: {historicalReview.usable_rows}</div>
                      <div>Months covered: {historicalReview.distinct_months}</div>
                      <div>Date range: {historicalReview.first_service_date ?? "?"} to {historicalReview.last_service_date ?? "?"}</div>
                      <div>Seasons: {(historicalReview.seasons_covered ?? []).join(", ") || "Not enough coverage yet"}</div>
                      <div>Derived Mon-Thu: {historicalReview.baseline_values.mon_thu ?? "—"}</div>
                      <div>Derived Fri: {historicalReview.baseline_values.fri ?? "—"}</div>
                      <div>Derived Sat: {historicalReview.baseline_values.sat ?? "—"}</div>
                      <div>Derived Sun: {historicalReview.baseline_values.sun ?? "—"}</div>
                    </div>
                    {historicalReview.ai_summary ? (
                      <div className="status-line">{historicalReview.ai_summary}</div>
                    ) : null}
                    {(historicalReview.warnings ?? []).map((warning) => (
                      <div key={warning} className="status-line">{warning}</div>
                    ))}
                    {(historicalReview.requirement_failures ?? []).map((failure) => (
                      <div key={failure} className="status-line">{failure}</div>
                    ))}
                  </div>
                ) : null}
              </>
            ) : (
              <>
                <div className="status-card is-info span-2">
                  <strong>Manual baseline entry</strong>
                  <div className="status-line">Enter your typical dinner covers for each day group.</div>
                </div>

                <label>
                  <span>Mon-Thu</span>
                  <input type="number" min={0} value={draft.monThu} onChange={(event) => onChange("monThu", Number(event.target.value))} />
                </label>
                <label>
                  <span>Fri</span>
                  <input type="number" min={0} value={draft.fri} onChange={(event) => onChange("fri", Number(event.target.value))} />
                </label>
                <label>
                  <span>Sat</span>
                  <input type="number" min={0} value={draft.sat} onChange={(event) => onChange("sat", Number(event.target.value))} />
                </label>
                <label>
                  <span>Sun</span>
                  <input type="number" min={0} value={draft.sun} onChange={(event) => onChange("sun", Number(event.target.value))} />
                </label>
              </>
            )}
          </div>
        ) : null}

        {currentStep === 3 ? (
          <div className="form-grid">
            <label>
              <span>Guest mix</span>
              <select value={draft.demandMix} onChange={(event) => onChange("demandMix", event.target.value)}>
                {options.demandMixOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>

            <label>
              <span>Neighborhood type</span>
              <select value={draft.neighborhoodType} onChange={(event) => onChange("neighborhoodType", event.target.value)}>
                {options.neighborhoodOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>

            <label className="checkbox-row span-2">
              <input type="checkbox" checked={draft.patioEnabled} onChange={(event) => onChange("patioEnabled", event.target.checked)} />
              <span>We have patio or outdoor seating</span>
            </label>

            {draft.patioEnabled ? (
              <>
                <label>
                  <span>Patio seat count</span>
                  <input
                    type="number"
                    min={0}
                    value={draft.patioSeatCapacity}
                    onChange={(event) => onChange("patioSeatCapacity", Number(event.target.value))}
                  />
                </label>
                <label>
                  <span>Patio availability</span>
                  <select value={draft.patioSeasonMode} onChange={(event) => onChange("patioSeasonMode", event.target.value)}>
                    {options.patioSeasonModes.map((mode) => (
                      <option key={mode} value={mode}>
                        {mode}
                      </option>
                    ))}
                  </select>
                </label>
              </>
            ) : null}

            <div className="checkbox-cluster span-2">
              <span>Nearby demand drivers</span>
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={draft.transitRelevance}
                  onChange={(event) => onChange("transitRelevance", event.target.checked)}
                />
                <span>Transit nearby</span>
              </label>
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={draft.venueRelevance}
                  onChange={(event) => onChange("venueRelevance", event.target.checked)}
                />
                <span>Event venue nearby</span>
              </label>
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={draft.hotelTravelRelevance}
                  onChange={(event) => onChange("hotelTravelRelevance", event.target.checked)}
                />
                <span>Hotels or travel nearby</span>
              </label>
            </div>
          </div>
        ) : null}

        <div className="form-actions">
          <button className="ghost-button" type="button" onClick={() => onStepChange(Math.max(1, currentStep - 1))} disabled={currentStep === 1}>
            Back
          </button>
          {currentStep < 3 ? (
            <button className="primary-button" type="button" onClick={() => onStepChange(currentStep + 1)} disabled={!stepValid}>
              Continue
            </button>
          ) : (
            <button className="primary-button" type="submit" disabled={submitting || !stepValid}>
              {submitting ? "Setting up your account..." : "Finish Setup"}
            </button>
          )}
        </div>
      </form>

      {bootstrapLogs.length > 0 ? (
        <div className="status-card">
          <strong>Setup actions</strong>
          {bootstrapLogs.map((line) => (
            <div key={line} className="status-line">
              {line}
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function SetupBootstrapPanel(props: {
  operatorName: string;
  bootstrap: SetupBootstrapState;
  submitting: boolean;
  onRetry: () => void;
}) {
  const bootstrap = props.bootstrap;
  if (!bootstrap) {
    return null;
  }
  const failed = bootstrap.status === "failed";
  return (
    <section className="panel bootstrap-panel editor-panel" data-tone="onboarding">
      <header className="editor-panel-header">
        <div className="editor-panel-mark" aria-hidden>{failed ? "!" : "·"}</div>
        <div className="editor-panel-text">
          <div className="editor-panel-kicker">Account setup</div>
          <h2 className="editor-panel-title">
            {failed ? "Setup needs one more pass" : "Setting up your account"}
          </h2>
          <p className="editor-panel-sub">
            {failed
              ? `StormReady saved ${props.operatorName}, but the weather setup or first refresh did not finish.`
              : `StormReady saved ${props.operatorName}. Weather setup and the first forecast refresh are running now.`}
          </p>
        </div>
      </header>

      <div className={`bootstrap-status ${failed ? "is-warning" : "is-info"}`}>
        <div className="bootstrap-status-head">
          <span className="bootstrap-status-pip" aria-hidden />
          <strong>{bootstrap.message}</strong>
        </div>
        {bootstrap.steps.length > 0 ? (
          <ol className="bootstrap-status-steps">
            {bootstrap.steps.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ol>
        ) : null}
      </div>

      <div className="form-actions">
        <div className="muted-copy">
          {failed ? "Retry setup to finish the remaining weather setup and first refresh." : "This screen updates automatically every few seconds."}
        </div>
        {failed ? (
          <button className="primary-button" type="button" onClick={props.onRetry} disabled={props.submitting}>
            {props.submitting ? "Retrying..." : "Retry Setup"}
          </button>
        ) : null}
      </div>
    </section>
  );
}

function OperationsQueue(props: {
  dashboard: NonNullable<Workspace["dashboard"]>;
  onOpenPlan: () => void;
  onOpenActuals: () => void;
  onSwitchToChat: () => void;
}) {
  const planDueCount = props.dashboard.servicePlanWindow?.dueCount ?? 0;
  const actualsDueCount = props.dashboard.missingActuals.length;
  const pendingSuggestions = props.dashboard.openServiceStateSuggestions.length;

  const items: Array<{
    index: string;
    kicker: string;
    title: string;
    body: string;
    count: number;
    status: "due" | "clear";
    Icon: () => JSX.Element;
    onClick: () => void;
  }> = [
    {
      index: "01",
      kicker: "Upcoming",
      title: "Service plan",
      body:
        planDueCount > 0
          ? `${planDueCount} night${planDueCount === 1 ? "" : "s"} still need a saved plan.`
          : "No plan review is due. Open this if you need to note a closure, buyout, or service change.",
      count: planDueCount,
      status: planDueCount > 0 ? "due" : "clear",
      Icon: PlanIcon,
      onClick: props.onOpenPlan
    },
    {
      index: "02",
      kicker: "Yesterday",
      title: "Dinner actuals",
      body:
        actualsDueCount > 0
          ? `${actualsDueCount} recent dinner${actualsDueCount === 1 ? "" : "s"} still missing covers.`
          : "All recent dinners are logged. Nothing waiting on you.",
      count: actualsDueCount,
      status: actualsDueCount > 0 ? "due" : "clear",
      Icon: LogIcon,
      onClick: props.onOpenActuals
    },
    {
      index: "03",
      kicker: "Chat",
      title: "Conversation",
      body:
        pendingSuggestions > 0
          ? `${pendingSuggestions} night${pendingSuggestions === 1 ? "" : "s"} may need a closer look.`
          : "Open chat for explanations, notes, and guided decisions.",
      count: pendingSuggestions,
      status: pendingSuggestions > 0 ? "due" : "clear",
      Icon: ChatIcon,
      onClick: props.onSwitchToChat
    }
  ];

  return (
    <section className="ops-queue" aria-label="Operations queue">
      {items.map((item) => (
        <button
          key={item.index}
          className={`queue-card is-${item.status}`}
          type="button"
          onClick={item.onClick}
          data-count={item.count}
        >
          <div className="queue-card-rule" aria-hidden />
          <div className="queue-card-index" aria-hidden>{item.index}</div>
          <div className="queue-card-body">
            <div className="queue-card-head">
              <div className="queue-card-kicker">{item.kicker}</div>
              <span
                className={`queue-card-pip ${item.status === "due" ? "is-due" : "is-clear"}`}
                aria-hidden
              />
            </div>
            <strong className="queue-card-title">{item.title}</strong>
            <p className="queue-card-text">{item.body}</p>
          </div>
          <div className="queue-card-count" aria-hidden>
            {item.count > 0 ? <span>{item.count}</span> : <item.Icon />}
          </div>
        </button>
      ))}
    </section>
  );
}

function NightChip(props: {
  iso: string;
  active: boolean;
  status: "due" | "done";
  statusLabel: string;
  onSelect: () => void;
}) {
  const parsed = new Date(`${props.iso}T00:00:00`);
  const valid = !Number.isNaN(parsed.getTime());
  const day = valid ? parsed.toLocaleDateString("en-US", { weekday: "short" }).toUpperCase() : "";
  const dayNum = valid ? parsed.getDate() : props.iso;
  const month = valid ? parsed.toLocaleDateString("en-US", { month: "short" }).toUpperCase() : "";

  return (
    <button
      type="button"
      role="tab"
      aria-selected={props.active}
      className={`night-chip ${props.active ? "is-active" : ""} is-${props.status}`}
      onClick={props.onSelect}
    >
      <div className="night-chip-cal" aria-hidden>
        <span className="night-chip-month">{month}</span>
        <span className="night-chip-num">{dayNum}</span>
        <span className="night-chip-day">{day}</span>
      </div>
      <div className="night-chip-meta">
        <span className="night-chip-status">{props.statusLabel}</span>
        <span className={`night-chip-pip is-${props.status}`} aria-hidden />
      </div>
    </button>
  );
}

function ServicePlanPanel(props: {
  window: NonNullable<NonNullable<Workspace["dashboard"]>["servicePlanWindow"]>;
  operatorText: OperatorTextContract;
  submitting: boolean;
  onSubmit: (payload: ServicePlanEntryDraft) => void;
}) {
  const copy = props.operatorText.workflow.servicePlan;
  const planOptions = props.operatorText.serviceStateOptions.plan;
  const initialServiceDate = props.window.entries[0]?.serviceDate ?? "";
  const [selectedServiceDate, setSelectedServiceDate] = useState(initialServiceDate);
  const selectedEntry = props.window.entries.find((entry) => entry.serviceDate === selectedServiceDate) ?? props.window.entries[0] ?? null;
  const [serviceState, setServiceState] = useState(selectedEntry?.serviceState ?? "normal_service");
  const [plannedTotalCovers, setPlannedTotalCovers] = useState(selectedEntry?.plannedTotalCovers?.toString() ?? "");
  const [estimatedReductionPct, setEstimatedReductionPct] = useState(selectedEntry?.estimatedReductionPct?.toString() ?? "");
  const [note, setNote] = useState(selectedEntry?.note ?? "");

  useEffect(() => {
    const availableDates = new Set(props.window.entries.map((item) => item.serviceDate));
    if (!selectedServiceDate || !availableDates.has(selectedServiceDate)) {
      setSelectedServiceDate(props.window.entries[0]?.serviceDate ?? "");
    }
  }, [props.window.entries, selectedServiceDate]);

  useEffect(() => {
    const nextEntry = props.window.entries.find((entry) => entry.serviceDate === selectedServiceDate) ?? props.window.entries[0] ?? null;
    setServiceState(nextEntry?.serviceState ?? "normal_service");
    setPlannedTotalCovers(nextEntry?.plannedTotalCovers != null ? String(nextEntry.plannedTotalCovers) : "");
    setEstimatedReductionPct(nextEntry?.estimatedReductionPct != null ? String(nextEntry.estimatedReductionPct) : "");
    setNote(nextEntry?.note ?? "");
  }, [props.window.entries, selectedServiceDate]);

  if (!selectedEntry) {
    return null;
  }

  const parsedTotal = parseOptionalNumber(plannedTotalCovers);
  const parsedReduction = parseOptionalNumber(estimatedReductionPct);
  const numericFieldsAllowed = serviceState !== "normal_service" && serviceState !== "closed";
  const formValid =
    (parsedTotal === null || parsedTotal >= 0) &&
    (parsedReduction === null || (parsedReduction >= 0 && parsedReduction <= 95));

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!formValid) {
      return;
    }
    props.onSubmit({
      serviceDate: selectedServiceDate,
      serviceState,
      plannedTotalCovers: serviceState === "closed" ? 0 : parsedTotal,
      estimatedReductionPct: numericFieldsAllowed ? parsedReduction : null,
      note: note.trim(),
      reviewWindowStart: props.window.windowStart,
      reviewWindowEnd: props.window.windowEnd,
    });
  }

  return (
    <section className="panel actuals-panel editor-panel" data-tone="plan">
      <header className="editor-panel-header">
        <div className="editor-panel-mark" aria-hidden>1</div>
        <div className="editor-panel-text">
          <div className="editor-panel-kicker">{copy.header_kicker}</div>
          <h2 className="editor-panel-title">{copy.header_title}</h2>
          <p className="editor-panel-sub">{copy.header_sub}</p>
        </div>
      </header>

      <div className="editor-banner" role="status">
        <span className="editor-banner-pip" aria-hidden />
        <span>
          {props.window.dueCount > 0
            ? formatCopy(copy.due_banner, {
                count: props.window.dueCount,
                plural: props.window.dueCount === 1 ? "" : "s",
              })
            : copy.reviewed_banner}
        </span>
      </div>

      <div className="night-chip-row" role="tablist" aria-label={copy.tab_aria_label}>
        {props.window.entries.map((entry) => (
          <NightChip
            key={entry.serviceDate}
            iso={entry.serviceDate}
            active={selectedServiceDate === entry.serviceDate}
            status={entry.reviewed ? "done" : "due"}
              statusLabel={
                entry.reviewed
                ? planOptions.find((option) => option.value === entry.serviceState)?.label ?? copy.reviewed_status
                : copy.due_status
              }
            onSelect={() => setSelectedServiceDate(entry.serviceDate)}
          />
        ))}
      </div>

      <form className="actuals-form" onSubmit={handleSubmit}>
        <div className="form-grid">
          <label>
            <span>{copy.field_service_state}</span>
            <select aria-label={copy.field_service_state} value={serviceState} onChange={(event) => setServiceState(event.target.value)}>
              {planOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>{copy.field_planned_total_covers}</span>
            <input
              aria-label={copy.field_planned_total_covers}
              type="number"
              min={0}
              value={serviceState === "closed" ? "0" : plannedTotalCovers}
              onChange={(event) => setPlannedTotalCovers(event.target.value)}
              disabled={serviceState === "closed"}
              placeholder={numericFieldsAllowed ? copy.planned_total_placeholder_optional : copy.planned_total_placeholder_default}
            />
          </label>
          <label>
            <span>{copy.field_estimated_reduction_pct}</span>
            <input
              aria-label={copy.field_estimated_reduction_pct}
              type="number"
              min={0}
              max={95}
              value={numericFieldsAllowed ? estimatedReductionPct : ""}
              onChange={(event) => setEstimatedReductionPct(event.target.value)}
              disabled={!numericFieldsAllowed}
              placeholder={numericFieldsAllowed ? copy.reduction_placeholder_optional : copy.reduction_placeholder_locked}
            />
          </label>
          <label className="span-2">
            <span>{copy.field_note}</span>
            <textarea
              aria-label={copy.field_note}
              rows={3}
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder={copy.note_placeholder}
            />
          </label>
        </div>

        <div className="form-actions">
          <div className="muted-copy">
            {serviceState === "normal_service"
              ? copy.helper_normal
              : copy.helper_adjusted}
          </div>
          <button className="primary-button" type="submit" disabled={props.submitting || !formValid}>
            {props.submitting ? copy.saving_button : copy.save_button}
          </button>
        </div>
      </form>
    </section>
  );
}

function SelectedNightPlanPanel(props: {
  card: ForecastCard;
  operatorText: OperatorTextContract;
  submitting: boolean;
  onClose: () => void;
  onSubmit: (payload: ServicePlanEntryDraft) => void;
}) {
  const planCopy = props.operatorText.workflow.servicePlan;
  const copy = props.operatorText.workflow.selectedNightPlan;
  const planOptions = props.operatorText.serviceStateOptions.plan;
  const [serviceState, setServiceState] = useState(props.card.serviceState || "normal_service");
  const [plannedTotalCovers, setPlannedTotalCovers] = useState("");
  const [estimatedReductionPct, setEstimatedReductionPct] = useState("");
  const [note, setNote] = useState("");

  useEffect(() => {
    setServiceState(props.card.serviceState || "normal_service");
    setPlannedTotalCovers("");
    setEstimatedReductionPct("");
    setNote("");
  }, [props.card.serviceDate, props.card.serviceState]);

  const parsedTotal = parseOptionalNumber(plannedTotalCovers);
  const parsedReduction = parseOptionalNumber(estimatedReductionPct);
  const numericFieldsAllowed = serviceState !== "normal_service" && serviceState !== "closed";
  const formValid =
    (parsedTotal === null || parsedTotal >= 0) &&
    (parsedReduction === null || (parsedReduction >= 0 && parsedReduction <= 95));

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!formValid) {
      return;
    }
    props.onSubmit({
      serviceDate: props.card.serviceDate,
      serviceState,
      plannedTotalCovers: serviceState === "closed" ? 0 : parsedTotal,
      estimatedReductionPct: numericFieldsAllowed ? parsedReduction : null,
      note: note.trim(),
      reviewWindowStart: null,
      reviewWindowEnd: null,
    });
  }

  return (
    <section className="panel actuals-panel editor-panel" data-tone="plan">
      <header className="editor-panel-header">
        <div className="editor-panel-mark" aria-hidden>2</div>
        <div className="editor-panel-text">
          <div className="editor-panel-kicker">{copy.header_kicker}</div>
          <h2 className="editor-panel-title">
            {props.card.dayLabel} <span className="editor-panel-title-num">{props.card.dateLabel}</span>
          </h2>
          <p className="editor-panel-sub">{copy.header_sub}</p>
        </div>
      </header>

      <form className="actuals-form" onSubmit={handleSubmit}>
        <div className="form-grid">
          <label>
            <span>{planCopy.field_service_state}</span>
            <select aria-label={planCopy.field_service_state} value={serviceState} onChange={(event) => setServiceState(event.target.value)}>
              {planOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>{planCopy.field_planned_total_covers}</span>
            <input
              aria-label={planCopy.field_planned_total_covers}
              type="number"
              min={0}
              value={serviceState === "closed" ? "0" : plannedTotalCovers}
              onChange={(event) => setPlannedTotalCovers(event.target.value)}
              disabled={serviceState === "closed"}
              placeholder={numericFieldsAllowed ? planCopy.planned_total_placeholder_optional : planCopy.planned_total_placeholder_default}
            />
          </label>
          <label>
            <span>{planCopy.field_estimated_reduction_pct}</span>
            <input
              aria-label={planCopy.field_estimated_reduction_pct}
              type="number"
              min={0}
              max={95}
              value={numericFieldsAllowed ? estimatedReductionPct : ""}
              onChange={(event) => setEstimatedReductionPct(event.target.value)}
              disabled={!numericFieldsAllowed}
              placeholder={numericFieldsAllowed ? planCopy.reduction_placeholder_optional : planCopy.reduction_placeholder_locked}
            />
          </label>
          <label className="span-2">
            <span>{planCopy.field_note}</span>
            <textarea
              aria-label={planCopy.field_note}
              rows={3}
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder={planCopy.note_placeholder}
            />
          </label>
        </div>

        <div className="form-actions">
          <div className="muted-copy">
            {serviceState === "normal_service"
              ? copy.helper_normal
              : copy.helper_adjusted}
          </div>
          <div className="inline-actions">
            <button className="ghost-button" type="button" onClick={props.onClose}>
              {copy.close_button}
            </button>
            <button className="primary-button" type="submit" disabled={props.submitting || !formValid}>
              {props.submitting ? copy.saving_button : copy.save_button}
            </button>
          </div>
        </div>
      </form>
    </section>
  );
}

function ActualsDuePanel(props: {
  missingActuals: NonNullable<Workspace["dashboard"]>["missingActuals"];
  operatorText: OperatorTextContract;
  submitting: boolean;
  onSubmit: (payload: ActualEntryDraft) => void;
}) {
  const copy = props.operatorText.workflow.actuals;
  const actualOptions = props.operatorText.serviceStateOptions.actual;
  const initialServiceDate = props.missingActuals[0]?.serviceDate ?? "";
  const [selectedServiceDate, setSelectedServiceDate] = useState(initialServiceDate);
  const [totalCovers, setTotalCovers] = useState("");
  const [reservedCovers, setReservedCovers] = useState("");
  const [walkInCovers, setWalkInCovers] = useState("");
  const [outsideCovers, setOutsideCovers] = useState("");
  const [serviceState, setServiceState] = useState("normal_service");
  const [note, setNote] = useState("");
  const selectedMissingActual =
    props.missingActuals.find((item) => item.serviceDate === selectedServiceDate) ?? props.missingActuals[0] ?? null;

  useEffect(() => {
    const availableDates = new Set(props.missingActuals.map((item) => item.serviceDate));
    if (!selectedServiceDate || !availableDates.has(selectedServiceDate)) {
      setSelectedServiceDate(props.missingActuals[0]?.serviceDate ?? "");
    }
  }, [props.missingActuals, selectedServiceDate]);

  useEffect(() => {
    setTotalCovers("");
    setReservedCovers("");
    setWalkInCovers("");
    setOutsideCovers("");
    setServiceState("normal_service");
    setNote("");
  }, [selectedServiceDate]);

  if (!selectedMissingActual) {
    return null;
  }

  const parsedTotal = parseOptionalNumber(totalCovers);
  const parsedReserved = parseOptionalNumber(reservedCovers);
  const parsedWalkIns = parseOptionalNumber(walkInCovers);
  const parsedOutside = parseOptionalNumber(outsideCovers);
  const formValid =
    parsedTotal !== null &&
    parsedTotal >= 0 &&
    (parsedReserved === null || parsedReserved >= 0) &&
    (parsedWalkIns === null || parsedWalkIns >= 0) &&
    (parsedOutside === null || parsedOutside >= 0) &&
    (parsedOutside === null || parsedOutside <= parsedTotal) &&
    (parsedReserved === null || parsedWalkIns === null || parsedReserved + parsedWalkIns <= parsedTotal);

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!formValid || parsedTotal === null) {
      return;
    }
    props.onSubmit({
      serviceDate: selectedServiceDate,
      realizedTotalCovers: parsedTotal,
      realizedReservedCovers: parsedReserved,
      realizedWalkInCovers: parsedWalkIns,
      outsideCovers: parsedOutside,
      serviceState,
      note: note.trim(),
    });
  }

  return (
    <section className="panel actuals-panel editor-panel" data-tone="actuals">
      <header className="editor-panel-header">
        <div className="editor-panel-mark" aria-hidden>3</div>
        <div className="editor-panel-text">
          <div className="editor-panel-kicker">{copy.header_kicker}</div>
          <h2 className="editor-panel-title">{copy.header_title}</h2>
          <p className="editor-panel-sub">
            {props.missingActuals.length === 1 ? copy.header_sub_singular : copy.header_sub_plural}
          </p>
        </div>
      </header>

      <div className="night-chip-row" role="tablist" aria-label={copy.tab_aria_label}>
        {props.missingActuals.map((item) => (
          <NightChip
            key={item.serviceDate}
            iso={item.serviceDate}
            active={selectedServiceDate === item.serviceDate}
            status="due"
            statusLabel={formatCopy(copy.forecast_status, { covers: item.forecastExpected })}
            onSelect={() => setSelectedServiceDate(item.serviceDate)}
          />
        ))}
      </div>

      <form className="actuals-form" onSubmit={handleSubmit}>
        <div className="editor-banner" role="status">
          <span className="editor-banner-pip" aria-hidden />
          <span>
            {formatCopy(copy.entry_banner, {
              date: selectedMissingActual.serviceDate,
              covers: selectedMissingActual.forecastExpected,
            })}
          </span>
        </div>

        <div className="form-grid">
          <label>
            <span>{copy.field_total_covers}</span>
            <input
              aria-label={copy.field_total_covers}
              type="number"
              min={0}
              value={totalCovers}
              onChange={(event) => setTotalCovers(event.target.value)}
            />
          </label>
          <label>
            <span>{copy.field_service_state}</span>
            <select aria-label={copy.field_service_state} value={serviceState} onChange={(event) => setServiceState(event.target.value)}>
              {actualOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>{copy.field_reserved_covers}</span>
            <input
              aria-label={copy.field_reserved_covers}
              type="number"
              min={0}
              value={reservedCovers}
              onChange={(event) => setReservedCovers(event.target.value)}
            />
          </label>
          <label>
            <span>{copy.field_walk_in_covers}</span>
            <input
              aria-label={copy.field_walk_in_covers}
              type="number"
              min={0}
              value={walkInCovers}
              onChange={(event) => setWalkInCovers(event.target.value)}
            />
          </label>
          <label>
            <span>{copy.field_outside_covers}</span>
            <input
              aria-label={copy.field_outside_covers}
              type="number"
              min={0}
              value={outsideCovers}
              onChange={(event) => setOutsideCovers(event.target.value)}
            />
          </label>
          <label className="span-2">
            <span>{copy.field_note}</span>
            <textarea
              aria-label={copy.field_note}
              rows={3}
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder={copy.note_placeholder}
            />
          </label>
        </div>

        <div className="form-actions">
          <div className="muted-copy">
            {parsedReserved !== null && parsedWalkIns !== null && parsedTotal !== null && parsedReserved + parsedWalkIns > parsedTotal
              ? copy.validation_reserved_walkins
              : parsedOutside !== null && parsedTotal !== null && parsedOutside > parsedTotal
                ? copy.validation_outside
              : copy.helper_default}
          </div>
          <button className="primary-button" type="submit" disabled={props.submitting || !formValid}>
            {props.submitting ? copy.saving_button : copy.save_button}
          </button>
        </div>
      </form>
    </section>
  );
}

function ForecastStrip(props: {
  cards: ForecastCard[];
  operatorText: OperatorTextContract;
  activeServiceDate: string | null;
  onSelect: (serviceDate: string) => void;
}) {
  const today = new Date().toISOString().slice(0, 10);
  return (
    <section className="panel strip-panel">
      <div className="strip-row">
        {props.cards.map((card) => {
          const weatherCode = card.weather?.conditionCode ?? null;
          const actualsRelevant = card.serviceDate <= today;
          const baselineComparison = card.baselineComparison ?? null;
          return (
            <button
              key={card.serviceDate}
              className={`forecast-card posture-${card.posture} ${props.activeServiceDate === card.serviceDate ? "is-active" : ""}`}
              data-weather={weatherCode ?? undefined}
              onClick={() => props.onSelect(card.serviceDate)}
            >
              <div className="card-topline">
                <span>{card.dayLabel}</span>
                {baselineComparison?.badgeText ? (
                  <span className={`comparison-badge ${comparisonTone(baselineComparison.deltaPct)}`}>
                    {baselineComparison.badgeText}
                  </span>
                ) : null}
              </div>
              <div className="card-date">{card.dateLabel}</div>
              {weatherCode ? (
                <div className="card-weather">
                  <WeatherGlyph code={weatherCode} size={22} title={weatherLabel(weatherCode)} />
                  {card.weather?.temperatureHigh != null ? (
                    <span className="card-weather-temp">{Math.round(card.weather.temperatureHigh)}&deg;</span>
                  ) : null}
                </div>
              ) : null}
              <div className="card-center">{card.forecastExpected}</div>
              <div className="card-range">{card.planningRangeLabel}</div>
              <StatusPipRow
                status={card.status}
                tier="medium"
                actualsRelevant={actualsRelevant}
                className="card-pips"
                labels={statusPipLabels(props.operatorText)}
              />
            </button>
          );
        })}
      </div>
    </section>
  );
}

function ChatWorkspace(props: {
  card: ForecastCard | null;
  dashboard: DashboardState | null;
  operatorText: OperatorTextContract;
  messages: ChatMessage[];
  hasMoreHistory: boolean;
  loadingOlder: boolean;
  onLoadOlder: () => void;
  composerValue: string;
  submitting: boolean;
  pendingUserMessage: string | null;
  suggestedMessages: SuggestedMessage[];
  placeholder: string;
  onOpenPlanEditor: () => void;
  onOpenActualsPanel: () => void;
  onSeedComposerNote: () => void;
  onComposerChange: (value: string) => void;
  onSubmit: () => void;
  onSendMessage: (message: string, learningAgendaKey?: string | null) => void;
  onAskAboutNight: (serviceDate: string) => void;
}) {
  const selectedCard = props.card;

  const agenda = props.dashboard?.learningAgenda ?? [];
  const attentionSummary = props.dashboard?.operatorAttentionSummary ?? null;

  const activeQuestion = agenda.find(
    (item) => item.status === "open" && (item.question_kind === "yes_no" || item.question_kind === "free_text")
  ) ?? null;
  const reminders = agenda.filter(
    (item) => item.status === "open" && item.question_kind === "reminder"
  ).slice(0, 3);

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (!props.submitting && props.composerValue.trim()) {
        props.onSubmit();
      }
    }
  }

  function handleLearningAnswer(item: LearningAgendaItem, answer: "yes" | "no" | "not_sure") {
    const message = answer === "yes" ? "Yes" : answer === "no" ? "No" : "Not sure";
    props.onSendMessage(message, item.agenda_key);
  }

  // Lazy scroll-back: observe a sentinel above the message list and call
  // onLoadOlder when it enters view.
  const loadMoreRef = useRef<HTMLDivElement>(null);
  const onLoadOlder = props.onLoadOlder;
  const hasMoreHistory = props.hasMoreHistory;
  const loadingOlder = props.loadingOlder;
  useEffect(() => {
    if (!hasMoreHistory) return;
    if (typeof IntersectionObserver === "undefined") return;
    const target = loadMoreRef.current;
    if (!target) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting && !loadingOlder) {
          onLoadOlder();
        }
      },
      { rootMargin: "120px 0px 0px 0px" }
    );
    observer.observe(target);
    return () => observer.disconnect();
  }, [hasMoreHistory, loadingOlder, onLoadOlder]);

  // Group messages by ISO week (week starts Monday).
  const messageGroups = useMemo(() => groupMessagesByWeek(props.messages), [props.messages]);

  return (
    <div className="chat-layout">
      <section className="panel chat-stage">
        <div className="chat-thread">
          <div ref={loadMoreRef} className="chat-thread-top-sentinel" aria-hidden />
          {props.loadingOlder ? (
            <div className="chat-thread-loading">Loading older messages&hellip;</div>
          ) : null}
          {!props.hasMoreHistory && props.messages.length > 0 ? (
            <div className="chat-thread-end">Beginning of conversation</div>
          ) : null}
          {messageGroups.map((group) => (
            <Fragment key={group.key}>
              <div className="chat-week-divider" role="separator">
                <span>{group.label}</span>
              </div>
              {group.messages.map((message, index) => (
                <article
                  key={message.messageId != null ? `m-${message.messageId}` : `${group.key}-${index}`}
                  className={`message-bubble ${message.role === "operator" ? "user" : "assistant"}`}
                >
                  <div className="message-role">
                    {message.role === "operator" ? "You" : "StormReady"}
                  </div>
                  <ReactMarkdown>{message.content}</ReactMarkdown>
                </article>
              ))}
            </Fragment>
          ))}
          {props.pendingUserMessage ? (
            <>
              <article className="message-bubble user is-pending" aria-live="polite">
                <div className="message-role">You</div>
                <ReactMarkdown>{props.pendingUserMessage}</ReactMarkdown>
              </article>
              <article className="message-bubble assistant is-thinking" aria-live="polite">
                <div className="message-role">StormReady</div>
                <div className="thinking-indicator" aria-label="StormReady is thinking">
                  <svg
                    className="thinking-bolt"
                    viewBox="0 0 24 24"
                    aria-hidden
                    focusable="false"
                  >
                    <path
                      d="M13.5 2L4 14h6l-1.5 8L20 9h-6.5L15 2z"
                      fill="currentColor"
                    />
                  </svg>
                  <span className="thinking-label">Reading the storm</span>
                  <span className="thinking-dots" aria-hidden>
                    <i />
                    <i />
                    <i />
                  </span>
                </div>
              </article>
            </>
          ) : null}
        </div>

      {activeQuestion ? (
        <LearningQuestionCard
          item={activeQuestion}
          submitting={props.submitting}
          onAnswer={handleLearningAnswer}
          onFreeTextReply={(item, text) => props.onSendMessage(text, item.agenda_key)}
        />
      ) : null}

      <div className="composer-dock">
        {props.suggestedMessages.length ? (
          <div className="quick-replies" aria-label="Suggested replies">
            {props.suggestedMessages.map((item) => (
              <button
                key={`${item.category}:${item.value}`}
                className="quick-reply"
                type="button"
                disabled={props.submitting}
                onClick={() => props.onSendMessage(item.value)}
              >
                {item.label}
              </button>
            ))}
          </div>
        ) : null}
        <div className="composer-toolbar">
          <button className="composer-tool" type="button" onClick={props.onSeedComposerNote}>
            <NoteIcon />
            <span>Note</span>
          </button>
          <button className="composer-tool" type="button" onClick={props.onOpenPlanEditor}>
            <PlanIcon />
            <span>Plan</span>
          </button>
          <button className="composer-tool" type="button" onClick={props.onOpenActualsPanel}>
            <LogIcon />
            <span>Log</span>
          </button>
          <div className="composer-hint">Enter to send. Shift+Enter for a new line.</div>
        </div>

        <div className="composer">
          <textarea
            id="chat-composer-input"
            value={props.composerValue}
            placeholder={props.placeholder}
            onChange={(event) => props.onComposerChange(event.target.value)}
            onKeyDown={handleComposerKeyDown}
            rows={3}
          />
        </div>
      </div>
      </section>

      <aside className="chat-rail">
        {selectedCard ? (
          <SelectedNightRailCard
            card={selectedCard}
            operatorText={props.operatorText}
            onOpenPlanEditor={props.onOpenPlanEditor}
            onAsk={() => props.onAskAboutNight(selectedCard.serviceDate)}
          />
        ) : (
          <section className="rail-section rail-night-empty">
            <div className="rail-section-label">Selected night</div>
            <p>Pick a night from the forecast above to pin its context here.</p>
          </section>
        )}

        {attentionSummary ? (
          <RailAttentionPanel
            summary={attentionSummary}
            operatorText={props.operatorText}
            hideNextQuestion={Boolean(activeQuestion)}
          />
        ) : null}

        {reminders.length > 0 ? (
          <section className="rail-section rail-reminders">
            <div className="rail-section-label">Still to do</div>
            <ul>
              {reminders.map((item) => (
                <li key={item.agenda_key}>{learningQuestionText(item)}</li>
              ))}
            </ul>
          </section>
        ) : null}
      </aside>
    </div>
  );
}

function SelectedNightRailCard(props: {
  card: ForecastCard;
  operatorText: OperatorTextContract;
  onOpenPlanEditor: () => void;
  onAsk: () => void;
}) {
  const card = props.card;
  const weather = card.weather ?? null;
  const today = new Date().toISOString().slice(0, 10);
  const actualsRelevant = card.serviceDate <= today;
  return (
    <section
      className={`rail-section rail-night posture-${card.posture}`}
      data-weather={weather?.conditionCode ?? undefined}
    >
      <div className="rail-night-head">
        <div className="rail-section-label">Selected night</div>
        <h3 className="rail-night-title">
          {card.dayLabel} <span className="rail-night-date">{card.dateLabel}</span>
        </h3>
        {weather ? (
          <div className="rail-night-weather">
            <WeatherGlyph
              code={weather.conditionCode}
              size={28}
              title={weatherLabel(weather.conditionCode)}
            />
            <div className="rail-night-weather-text">
              <span className="rail-night-weather-label">{weatherLabel(weather.conditionCode)}</span>
              {weather.temperatureHigh != null || weather.temperatureLow != null ? (
                <span className="rail-night-weather-temp">
                  {weather.temperatureHigh != null
                    ? `${Math.round(weather.temperatureHigh)}\u00B0`
                    : "\u2014"}
                  {" / "}
                  {weather.temperatureLow != null
                    ? `${Math.round(weather.temperatureLow)}\u00B0`
                    : "\u2014"}
                </span>
              ) : null}
            </div>
          </div>
        ) : null}
      </div>

      <p className="rail-night-summary">{card.summary}</p>

      <div className="rail-night-metrics">
        <div className="rail-metric">
          <span className="metric-label">Forecast</span>
          <strong>{card.forecastExpected}</strong>
        </div>
        <div className="rail-metric">
          <span className="metric-label">Planning range</span>
          <strong>{card.planningRangeLabel}</strong>
        </div>
      </div>

      <StatusPipRow
        status={card.status}
        tier="hero"
        actualsRelevant={actualsRelevant}
        className="rail-night-pips"
        labels={statusPipLabels(props.operatorText)}
      />

      {card.topDrivers && card.topDrivers.length > 0 ? (
        <div className="rail-night-drivers">
          <div className="rail-section-label">What's driving this</div>
          <ul>
            {card.topDrivers.slice(0, 3).map((d) => (
              <li key={d.id}>{d.label}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="rail-night-actions">
        <button className="ghost-button" onClick={props.onOpenPlanEditor}>
          Update Plan
        </button>
        <button className="ghost-button" onClick={props.onAsk}>
          Explain This Night
        </button>
      </div>
    </section>
  );
}

function RailAttentionPanel(props: {
  summary: OperatorAttentionSummary;
  operatorText: OperatorTextContract;
  hideNextQuestion?: boolean;
}) {
  const sectionLabels = props.operatorText.attentionLabels.sectionLabels;
  const defaultOrder = [
    "pending_operator_action",
    "current_operational_watchout",
    "current_uncertainty",
    "latest_material_change",
    "best_next_question",
  ];
  const orderedKeys = (props.summary.ordered_section_keys ?? defaultOrder).filter(
    (key, index, array) => array.indexOf(key) === index
  );
  const items = orderedKeys
    .filter((key) => !(props.hideNextQuestion && key === "best_next_question"))
    .map((key) => ({
      key,
      label: sectionLabels[key] ?? key.replace(/_/g, " "),
      value: props.summary[key as keyof OperatorAttentionSummary] as OperatorAttentionSection | null,
    }))
    .filter((item) => {
      const section = item.value;
      if (!section) return false;
      return Boolean(sectionText(section, item.key === "best_next_question"));
    });

  if (items.length === 0) {
    return null;
  }

  return (
    <section className="rail-section rail-attention">
      <div className="rail-section-label">
        {props.summary.moment_label || props.operatorText.attentionLabels.defaultMomentLabel}
      </div>
      <div className="rail-attention-stack">
        {items.map((item) => {
          const section = item.value!;
          const text = sectionText(section, item.key === "best_next_question");
          return (
            <article
              key={item.key}
              className={`rail-attention-item ${
                props.summary.primary_focus_key === item.key ? "is-primary" : ""
              }`}
            >
              <div className="rail-attention-label">{item.label}</div>
              <p className="rail-attention-text">{text}</p>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function LearningQuestionCard(props: {
  item: LearningAgendaItem;
  submitting: boolean;
  onAnswer: (item: LearningAgendaItem, answer: "yes" | "no" | "not_sure") => void;
  onFreeTextReply: (item: LearningAgendaItem, text: string) => void;
}) {
  const { item } = props;
  const [freeText, setFreeText] = useState("");
  const isYesNo = item.question_kind === "yes_no";

  return (
    <div className="learning-question-card">
      <div className="learning-question-header">
        <span className="learning-question-badge">One quick question</span>
        {item.service_date ? <span className="learning-question-date">{item.service_date}</span> : null}
      </div>
      <p className="learning-question-text">{learningQuestionText(item)}</p>
      {isYesNo ? (
        <div className="learning-question-actions">
          <button
            className="learning-btn learning-btn-yes"
            type="button"
            disabled={props.submitting}
            onClick={() => props.onAnswer(item, "yes")}
          >
            Yes
          </button>
          <button
            className="learning-btn learning-btn-no"
            type="button"
            disabled={props.submitting}
            onClick={() => props.onAnswer(item, "no")}
          >
            No
          </button>
          <button
            className="learning-btn learning-btn-unsure"
            type="button"
            disabled={props.submitting}
            onClick={() => props.onAnswer(item, "not_sure")}
          >
            Not sure
          </button>
        </div>
      ) : (
        <div className="learning-question-freetext">
          <input
            type="text"
            value={freeText}
            onChange={(e) => setFreeText(e.target.value)}
            placeholder="Type your answer..."
            onKeyDown={(e) => {
              if (e.key === "Enter" && freeText.trim()) {
                props.onFreeTextReply(item, freeText.trim());
                setFreeText("");
              }
            }}
          />
          <button
            className="ghost-button"
            type="button"
            disabled={props.submitting || !freeText.trim()}
            onClick={() => {
              if (freeText.trim()) {
                props.onFreeTextReply(item, freeText.trim());
                setFreeText("");
              }
            }}
          >
            Reply
          </button>
        </div>
      )}
    </div>
  );
}

function PyramidWorkspace(props: {
  cards: ForecastCard[];
  attentionSummary: OperatorAttentionSummary | null;
  operatorText: OperatorTextContract;
  activeServiceDate: string | null;
  onSelect: (serviceDate: string) => void;
}) {
  const today = new Date().toISOString().slice(0, 10);
  const heroCards = props.cards.slice(0, 2);
  const mediumCards = props.cards.slice(2, 5);
  const smallCards = props.cards.slice(5, 14);
  const activeCard =
    props.cards.find((c) => c.serviceDate === props.activeServiceDate) ?? null;

  const tonightFocus = pickTonightFocus(props.attentionSummary, props.operatorText);

  return (
    <div className="pyramid-workspace">
      {heroCards.length > 0 ? (
        <div className="pyramid-row pyramid-row-hero">
          {heroCards.map((card, idx) => (
            <PyramidHeroCard
              key={card.serviceDate}
              card={card}
              operatorText={props.operatorText}
              eyebrow={
                idx === 0
                  ? props.operatorText.forecastLabels.heroEyebrows.tonight
                  : props.operatorText.forecastLabels.heroEyebrows.tomorrow
              }
              focus={idx === 0 ? tonightFocus : null}
              isActive={props.activeServiceDate === card.serviceDate}
              actualsRelevant={card.serviceDate <= today}
              onSelect={() => props.onSelect(card.serviceDate)}
            />
          ))}
        </div>
      ) : null}

      {mediumCards.length > 0 ? (
        <div className="pyramid-row pyramid-row-medium">
          {mediumCards.map((card) => (
            <PyramidMediumCard
              key={card.serviceDate}
              card={card}
              operatorText={props.operatorText}
              isActive={props.activeServiceDate === card.serviceDate}
              actualsRelevant={card.serviceDate <= today}
              onSelect={() => props.onSelect(card.serviceDate)}
            />
          ))}
        </div>
      ) : null}

      {smallCards.length > 0 ? (
        <div className="pyramid-row pyramid-row-small">
          {smallCards.map((card) => (
            <PyramidSmallCard
              key={card.serviceDate}
              card={card}
              operatorText={props.operatorText}
              isActive={props.activeServiceDate === card.serviceDate}
              actualsRelevant={card.serviceDate <= today}
              onSelect={() => props.onSelect(card.serviceDate)}
            />
          ))}
        </div>
      ) : null}

      {activeCard ? <PyramidDetailPanel card={activeCard} operatorText={props.operatorText} /> : null}
    </div>
  );
}

type TonightFocus = {
  label: string;
  text: string;
  isPrimary: boolean;
};

const FOCUS_PRIORITY: Array<keyof OperatorAttentionSummary> = [
  "pending_operator_action",
  "current_operational_watchout",
  "current_uncertainty",
  "latest_material_change",
  "best_next_question",
];

function pickTonightFocus(summary: OperatorAttentionSummary | null, operatorText: OperatorTextContract): TonightFocus | null {
  if (!summary) return null;
  const primaryKey = summary.primary_focus_key;
  const candidates: Array<keyof OperatorAttentionSummary> = primaryKey
    ? [primaryKey as keyof OperatorAttentionSummary, ...FOCUS_PRIORITY]
    : FOCUS_PRIORITY;
  for (const key of candidates) {
    const section = summary[key] as OperatorAttentionSection | null | undefined;
    if (!section) continue;
    const text = sectionText(section, key === "best_next_question");
    if (!text) continue;
    return {
      label: operatorText.attentionLabels.focusSectionLabels[key as string] ?? "Tonight focus",
      text,
      isPrimary: primaryKey === key,
    };
  }
  return null;
}

function PyramidHeroCard(props: {
  card: ForecastCard;
  operatorText: OperatorTextContract;
  eyebrow: string;
  focus: TonightFocus | null;
  isActive: boolean;
  actualsRelevant: boolean;
  onSelect: () => void;
}) {
  const card = props.card;
  const weather = card.weather ?? null;
  const baselineComparison = card.baselineComparison ?? null;
  const recentChange = card.recentChange ?? null;
  return (
    <button
      type="button"
      onClick={props.onSelect}
      className={`pyramid-card pyramid-hero posture-${card.posture} ${props.isActive ? "is-active" : ""}`}
      data-weather={weather?.conditionCode ?? undefined}
    >
      <div className="pyramid-hero-grid">
        <div className="pyramid-hero-meta">
          <div className="pyramid-hero-eyebrow">{props.eyebrow}</div>
          <div className="pyramid-hero-day">
            {card.dayLabel}
            <span className="pyramid-hero-date">{card.dateLabel}</span>
          </div>
          {weather ? (
            <div className="pyramid-hero-weather">
              <WeatherGlyph
                code={weather.conditionCode}
                size={56}
                title={weatherLabel(weather.conditionCode)}
              />
              <div className="pyramid-hero-weather-text">
                <span className="pyramid-hero-weather-label">
                  {weatherLabel(weather.conditionCode)}
                </span>
                {weather.temperatureHigh != null || weather.temperatureLow != null ? (
                  <span className="pyramid-hero-weather-temp">
                    {weather.temperatureHigh != null
                      ? `${Math.round(weather.temperatureHigh)}\u00B0`
                      : "\u2014"}
                    {" / "}
                    {weather.temperatureLow != null
                      ? `${Math.round(weather.temperatureLow)}\u00B0`
                      : "\u2014"}
                  </span>
                ) : null}
              </div>
            </div>
          ) : null}
        </div>

        <div className="pyramid-hero-figure">
          <div className="pyramid-hero-number">{card.forecastExpected}</div>
          <div className="pyramid-hero-range">{card.planningRangeLabel}</div>
          {baselineComparison?.heroText ? (
            <div className={`pyramid-hero-comparison ${comparisonTone(baselineComparison.deltaPct)}`}>
              {baselineComparison.heroText}
            </div>
          ) : null}
        </div>
      </div>

      <div className="pyramid-hero-headline">{card.headline}</div>
      {card.summary ? <p className="pyramid-hero-summary">{card.summary}</p> : null}
      {recentChange?.text ? <p className="pyramid-hero-change">{recentChange.text}</p> : null}

      {props.focus ? (
        <div className={`pyramid-hero-focus ${props.focus.isPrimary ? "is-primary" : ""}`}>
          <div className="pyramid-hero-focus-label">{props.focus.label}</div>
          <p className="pyramid-hero-focus-text">{props.focus.text}</p>
        </div>
      ) : null}

      <StatusPipRow
        status={card.status}
        tier="hero"
        actualsRelevant={props.actualsRelevant}
        className="pyramid-hero-pips"
        labels={statusPipLabels(props.operatorText)}
      />
    </button>
  );
}

function PyramidMediumCard(props: {
  card: ForecastCard;
  operatorText: OperatorTextContract;
  isActive: boolean;
  actualsRelevant: boolean;
  onSelect: () => void;
}) {
  const card = props.card;
  const weather = card.weather ?? null;
  const baselineComparison = card.baselineComparison ?? null;
  const recentChange = card.recentChange ?? null;
  return (
    <button
      type="button"
      onClick={props.onSelect}
      className={`pyramid-card pyramid-medium posture-${card.posture} ${props.isActive ? "is-active" : ""}`}
      data-weather={weather?.conditionCode ?? undefined}
    >
      <div className="pyramid-medium-head">
        <div className="pyramid-medium-day">
          {card.dayLabel}
          <span className="pyramid-medium-date">{card.dateLabel}</span>
        </div>
        {weather ? (
          <WeatherGlyph
            code={weather.conditionCode}
            size={32}
            title={weatherLabel(weather.conditionCode)}
          />
        ) : null}
      </div>

      <div className="pyramid-medium-number">{card.forecastExpected}</div>
      <div className="pyramid-medium-range">{card.planningRangeLabel}</div>
      {baselineComparison?.badgeText ? (
        <div className={`pyramid-medium-comparison ${comparisonTone(baselineComparison.deltaPct)}`}>
          {baselineComparison.badgeText}
        </div>
      ) : null}
      {recentChange?.compactText ? <div className="pyramid-medium-change">{recentChange.compactText}</div> : null}

      <StatusPipRow
        status={card.status}
        tier="medium"
        actualsRelevant={props.actualsRelevant}
        className="pyramid-medium-pips"
        labels={statusPipLabels(props.operatorText)}
      />
    </button>
  );
}

function PyramidSmallCard(props: {
  card: ForecastCard;
  operatorText: OperatorTextContract;
  isActive: boolean;
  actualsRelevant: boolean;
  onSelect: () => void;
}) {
  const card = props.card;
  const weather = card.weather ?? null;
  const baselineComparison = card.baselineComparison ?? null;
  return (
    <button
      type="button"
      onClick={props.onSelect}
      className={`pyramid-card pyramid-small posture-${card.posture} ${props.isActive ? "is-active" : ""}`}
      data-weather={weather?.conditionCode ?? undefined}
    >
      <div className="pyramid-small-day">{card.dayLabel}</div>
      {weather ? (
        <WeatherGlyph
          code={weather.conditionCode}
          size={20}
          title={weatherLabel(weather.conditionCode)}
        />
      ) : (
        <div className="pyramid-small-glyph-placeholder" />
      )}
      <div className="pyramid-small-number">{card.forecastExpected}</div>
      {baselineComparison?.badgeText ? (
        <div className={`pyramid-small-comparison ${comparisonTone(baselineComparison.deltaPct)}`}>
          {baselineComparison.badgeText}
        </div>
      ) : null}
      <StatusPipRow
        status={card.status}
        tier="small"
        actualsRelevant={props.actualsRelevant}
        className="pyramid-small-pips"
        labels={statusPipLabels(props.operatorText)}
      />
    </button>
  );
}

function PyramidDetailPanel(props: { card: ForecastCard; operatorText: OperatorTextContract }) {
  const card = props.card;
  const weather = card.weather ?? null;
  const today = new Date().toISOString().slice(0, 10);
  const actualsRelevant = card.serviceDate <= today;

  return (
    <section
      className={`pyramid-detail-panel posture-${card.posture}`}
      data-weather={weather?.conditionCode ?? undefined}
    >
      <header className="pyramid-detail-head">
        <div>
          <div className="section-kicker">Selected night</div>
          <h3 className="pyramid-detail-title">
            {card.dayLabel} <span>{card.dateLabel}</span>
          </h3>
          <p className="pyramid-detail-headline">{card.headline}</p>
        </div>
      </header>

      <div className="pyramid-detail-body">
        {card.summary ? <p className="pyramid-detail-summary">{card.summary}</p> : null}

        <StatusPipRow
          status={card.status}
          tier="hero"
          actualsRelevant={actualsRelevant}
          className="pyramid-detail-pips"
          labels={statusPipLabels(props.operatorText)}
        />

        {weather ? (
          <div className="pyramid-detail-weather">
            <div className="pyramid-detail-weather-icon">
              <WeatherGlyph
                code={weather.conditionCode}
                size={44}
                title={weatherLabel(weather.conditionCode)}
              />
              <span>{weatherLabel(weather.conditionCode)}</span>
            </div>
            <dl className="pyramid-detail-weather-stats">
              {weather.temperatureHigh != null || weather.temperatureLow != null ? (
                <div>
                  <dt>High / low</dt>
                  <dd>
                    {weather.temperatureHigh != null
                      ? `${Math.round(weather.temperatureHigh)}\u00B0`
                      : "\u2014"}
                    {" / "}
                    {weather.temperatureLow != null
                      ? `${Math.round(weather.temperatureLow)}\u00B0`
                      : "\u2014"}
                  </dd>
                </div>
              ) : null}
              {weather.apparentTemp7pm != null ? (
                <div>
                  <dt>Apparent 7pm</dt>
                  <dd>{Math.round(weather.apparentTemp7pm)}&deg;</dd>
                </div>
              ) : null}
              {weather.precipChance != null ? (
                <div>
                  <dt>Rain chance</dt>
                  <dd>{Math.round(weather.precipChance * 100)}%</dd>
                </div>
              ) : null}
              {weather.windSpeedMph != null ? (
                <div>
                  <dt>Wind</dt>
                  <dd>{Math.round(weather.windSpeedMph)} mph</dd>
                </div>
              ) : null}
            </dl>
          </div>
        ) : null}

        {card.topDrivers && card.topDrivers.length > 0 ? (
          <div className="pyramid-detail-drivers">
            <div className="section-kicker">What's driving this</div>
            <div className="pyramid-detail-driver-list">
              {card.topDrivers.slice(0, 6).map((d) => (
                <span key={d.id} className="driver-pill">
                  {d.label}
                </span>
              ))}
            </div>
          </div>
        ) : null}

        {card.majorUncertainties && card.majorUncertainties.length > 0 ? (
          <div className="pyramid-detail-uncertainties">
            <div className="section-kicker">Still in play</div>
            <ul>
              {card.majorUncertainties.slice(0, 4).map((u, idx) => (
                <li key={idx}>{u}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </section>
  );
}

function TableWorkspace(props: { cards: ForecastCard[] }) {
  return (
    <section className="panel table-panel">
      <div className="table-panel-head">
        <div className="table-panel-kicker">Forecast almanac</div>
        <div className="table-panel-meta">
          <span>{props.cards.length} nights</span>
          <span aria-hidden>·</span>
          <span>read across the row</span>
        </div>
      </div>
      <table className="forecast-table">
        <thead>
          <tr>
            <th scope="col" className="col-date">Night</th>
            <th scope="col" className="col-sky">Sky</th>
            <th scope="col" className="col-num">Forecast</th>
            <th scope="col" className="col-band">Planning band</th>
            <th scope="col" className="col-change">% Covers Change</th>
          </tr>
        </thead>
        <tbody>
          {props.cards.map((card) => {
            const code = card.weather?.conditionCode ?? null;
            const baselineComparison = card.baselineComparison ?? null;
            return (
              <tr key={card.serviceDate} data-weather={code ?? undefined}>
                <td className="col-date">
                  <div className="forecast-table-date">
                    <span className="forecast-table-day">{card.dayLabel}</span>
                    <span className="forecast-table-num">{card.dateLabel}</span>
                  </div>
                </td>
                <td className="col-sky">
                  {code ? (
                    <span className="forecast-table-sky" title={weatherLabel(code)}>
                      <WeatherGlyph code={code} size={18} title={weatherLabel(code)} />
                    </span>
                  ) : (
                    <span className="forecast-table-sky is-empty" aria-hidden>—</span>
                  )}
                </td>
                <td className="col-num">
                  <span className="forecast-table-center">{card.forecastExpected}</span>
                </td>
                <td className="col-band">{card.planningRangeLabel}</td>
                <td className="col-change">
                  {baselineComparison?.badgeText ? (
                    <span className={`comparison-badge ${comparisonTone(baselineComparison.deltaPct)}`}>
                      {baselineComparison.badgeText}
                    </span>
                  ) : (
                    <span className="forecast-table-muted">—</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}

function ChatIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M5 6.5h14a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H9l-4 3v-3H5a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function DashboardIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M4 4h7v7H4zM13 4h7v5h-7zM13 11h7v9h-7zM4 13h7v7H4z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function DetailIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M5 7h14M5 12h14M5 17h9" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="18" cy="17" r="1.5" fill="currentColor" />
    </svg>
  );
}

function TableIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M4 6h16v12H4zM4 10h16M9 6v12M15 6v12"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function NoteIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M8 7.5h8M8 12h8M8 16.5h5M6 4h12a2 2 0 0 1 2 2v12l-4-2.5H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function PlanIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M7 3v3M17 3v3M4 8h16M6 5h12a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path d="M9 12h6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

function LogIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M8 4h8l3 3v13H5V4h3Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path d="M9 11h6M9 15h4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

function isSetupBootstrapActive(status: string | undefined): boolean {
  return status === "pending" || status === "running";
}

function shouldShowSetupBootstrapScreen(workspace: Workspace | null): boolean {
  if (!workspace?.setupBootstrap) {
    return false;
  }
  const visibleCardCount = workspace.dashboard?.cards.length ?? 0;
  if (isSetupBootstrapActive(workspace.setupBootstrap.status)) {
    return visibleCardCount < 14;
  }
  return workspace.setupBootstrap.status === "failed" && visibleCardCount === 0;
}

function NoticeBanner(props: { tone: "success" | "warning" | "error" | "info"; text: string }) {
  return <div className={`notice-banner ${props.tone}`}>{props.text}</div>;
}

function parseOptionalNumber(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) {
    return null;
  }
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatPlansHeaderDate(iso: string | null | undefined): string {
  if (!iso) return "today";
  const parsed = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric"
  });
}

function getErrorMessage(caught: unknown): string {
  if (caught instanceof Error) {
    return caught.message;
  }
  return "Unexpected error.";
}

type ChatMessageGroup = { key: string; label: string; messages: ChatMessage[] };

function startOfIsoWeek(date: Date): Date {
  const result = new Date(date);
  result.setHours(0, 0, 0, 0);
  // Monday = 1, Sunday = 0 → shift Sunday to be 7 so Monday-start works.
  const day = result.getDay() || 7;
  if (day !== 1) {
    result.setDate(result.getDate() - (day - 1));
  }
  return result;
}

function formatWeekLabel(monday: Date, now: Date): string {
  const thisMonday = startOfIsoWeek(now);
  const lastMonday = new Date(thisMonday);
  lastMonday.setDate(lastMonday.getDate() - 7);

  if (monday.getTime() === thisMonday.getTime()) return "This week";
  if (monday.getTime() === lastMonday.getTime()) return "Last week";

  const sameYear = monday.getFullYear() === now.getFullYear();
  return `Week of ${monday.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    ...(sameYear ? {} : { year: "numeric" })
  })}`;
}

function groupMessagesByWeek(messages: ChatMessage[]): ChatMessageGroup[] {
  if (messages.length === 0) return [];
  const now = new Date();
  const groups: ChatMessageGroup[] = [];
  let current: ChatMessageGroup | null = null;

  for (const message of messages) {
    let key: string;
    let label: string;
    const parsed = message.createdAt ? new Date(message.createdAt) : null;
    if (parsed && !Number.isNaN(parsed.getTime())) {
      const monday = startOfIsoWeek(parsed);
      key = `w-${monday.getFullYear()}-${monday.getMonth() + 1}-${monday.getDate()}`;
      label = formatWeekLabel(monday, now);
    } else {
      key = "w-earlier";
      label = "Earlier";
    }

    if (!current || current.key !== key) {
      current = { key, label, messages: [] };
      groups.push(current);
    }
    current.messages.push(message);
  }

  return groups;
}

export default App;
