import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import type {
  BootstrapPayload,
  ForecastCard,
  OnboardingDraft,
  OperatorTextContract,
  SetupBootstrapState,
  Workspace,
} from "./types";

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function buildDraft(): OnboardingDraft {
  return {
    restaurantName: "",
    canonicalAddress: "",
    city: "",
    timezone: "America/New_York",
    forecastInputMode: "manual_baselines",
    historicalUploadToken: null,
    historicalUploadReview: null,
    monThu: 0,
    fri: 0,
    sat: 0,
    sun: 0,
    demandMix: "mixed",
    neighborhoodType: "mixed_urban",
    patioEnabled: false,
    patioSeatCapacity: 0,
    patioSeasonMode: "seasonal",
    transitRelevance: false,
    venueRelevance: false,
    hotelTravelRelevance: false,
  };
}

function buildOperatorText(): OperatorTextContract {
  return {
    serviceStateOptions: {
      plan: [
        { value: "normal_service", label: "Normal service" },
        { value: "partial_service", label: "Partial or early close" },
        { value: "patio_closed_or_constrained", label: "Patio closed or constrained" },
        { value: "private_event_or_buyout", label: "Private event or buyout" },
        { value: "holiday_modified_service", label: "Holiday-modified service" },
        { value: "closed", label: "Closed" },
      ],
      actual: [
        { value: "normal_service", label: "Normal service" },
        { value: "partial_service", label: "Partial service" },
        { value: "patio_closed_or_constrained", label: "Patio constrained" },
        { value: "private_event_or_buyout", label: "Private event or buyout" },
        { value: "holiday_modified_service", label: "Holiday-modified service" },
        { value: "weather_disruption_service", label: "Weather disruption" },
        { value: "closed", label: "Closed" },
      ],
    },
    statusPipLabels: {
      plan: {
        submitted: "Plan saved",
        pending: "Plan due",
        stale: "Review plan",
        not_required: "",
      },
      learning: {
        none: "",
        open_question: "Need your answer",
        overdue_question: "Still need your answer",
      },
      actuals: {
        not_due: "",
        due: "Actuals due",
        recorded: "Actuals saved",
        overdue: "Actuals due",
      },
      watchout: {
        none: "",
        low_confidence: "Needs attention",
        service_state_risk: "Confirm service",
        material_uncertainty: "Check again",
      },
    },
    attentionLabels: {
      sectionLabels: {
        latest_material_change: "What changed",
        current_operational_watchout: "Needs attention",
        pending_operator_action: "What I need from you",
        current_uncertainty: "Still in play",
        best_next_question: "Need your answer",
      },
      focusSectionLabels: {
        pending_operator_action: "Tonight focus",
        current_operational_watchout: "Needs attention tonight",
        current_uncertainty: "Still in play tonight",
        latest_material_change: "What changed",
        best_next_question: "Need your answer",
      },
      defaultMomentLabel: "Right now",
    },
    forecastLabels: {
      heroEyebrows: {
        tonight: "Tonight",
        tomorrow: "Tomorrow",
      },
    },
    workflow: {
      servicePlan: {
        header_kicker: "Upcoming nights",
        header_title: "Review service plans",
        header_sub: "Save known closures, buyouts, early closes, patio limits, or confirm a night is running normally.",
        due_banner: "{count} night{plural} still need a saved plan.",
        reviewed_banner: "These nights are already planned. You can still revise any date below.",
        tab_aria_label: "Plan dates",
        reviewed_status: "Reviewed",
        due_status: "Plan due",
        field_service_state: "Planned service state",
        field_planned_total_covers: "Planned total covers",
        field_estimated_reduction_pct: "Estimated reduction %",
        field_note: "Plan note",
        planned_total_placeholder_optional: "Optional if you know it",
        planned_total_placeholder_default: "Optional",
        reduction_placeholder_optional: "Optional",
        reduction_placeholder_locked: "Use abnormal service state first",
        note_placeholder: "Examples: buyout at 7 PM, patio closed for repairs, closing one hour early, wedding group expected.",
        helper_normal: "Saving normal service marks this night ready.",
        helper_adjusted: "If you know the total covers or likely reduction, add it here. Otherwise save the service change and note.",
        save_button: "Save Plan",
        saving_button: "Saving plan...",
      },
      selectedNightPlan: {
        header_kicker: "Selected night",
        header_sub: "Use this any time for a known closure, buyout, early close, patio constraint, or other confirmed operating change.",
        helper_normal: "Saving normal service confirms the night as planned.",
        helper_adjusted: "If you know the total covers or likely reduction, add it here. Otherwise save the service change and note.",
        close_button: "Close",
        save_button: "Save Plan",
        saving_button: "Saving plan...",
      },
      actuals: {
        header_kicker: "Dinner actuals",
        header_title: "Log the missing totals",
        header_sub_singular: "This recent dinner service still needs actual covers. Logging it tightens the next forecasts.",
        header_sub_plural: "These recent dinner services still need actual covers. Logging them tightens the next forecasts.",
        tab_aria_label: "Dinner dates",
        forecast_status: "Forecast {covers} covers",
        entry_banner: "Logging {date}. We forecast about {covers} covers.",
        field_total_covers: "Total covers",
        field_service_state: "Service state",
        field_reserved_covers: "Reserved covers",
        field_walk_in_covers: "Walk-in covers",
        field_outside_covers: "Outside covers",
        field_note: "Operator note",
        note_placeholder: "Anything unusual about service, patio, weather, staffing, or traffic?",
        validation_reserved_walkins: "Reserved plus walk-ins cannot exceed total covers.",
        validation_outside: "Outside covers cannot exceed total covers.",
        helper_default: "Save the real totals here. Use the note only for extra context that may matter later.",
        save_button: "Save Actuals",
        saving_button: "Saving actuals...",
      },
    },
  };
}

function buildBootstrapPayload(): BootstrapPayload {
  return {
    operators: [],
    defaultOperatorId: null,
    operatorText: buildOperatorText(),
    onboarding: {
      draft: buildDraft(),
      options: {
        timezones: ["America/New_York"],
        demandMixOptions: [
          { value: "mixed", label: "Mixed" },
          { value: "walk_in_led", label: "Mostly walk-ins" },
        ],
        neighborhoodOptions: [
          { value: "mixed_urban", label: "Mixed urban" },
          { value: "residential", label: "Residential" },
        ],
        patioSeasonModes: ["seasonal", "year_round"],
        forecastInputModes: [
          { value: "manual_baselines", label: "Enter baselines", description: "Enter typical dinner covers by day group." },
          { value: "historical_upload", label: "Upload 12 months", description: "Upload history and derive the baselines automatically." },
        ],
        historicalUploadRequirements: [
          "CSV file with service date and total covers.",
          "At least 12 months of usable dinner history.",
        ],
        historicalUploadAcceptedExtensions: [".csv", ".txt"],
      },
    },
  };
}

function buildCards(): ForecastCard[] {
  return Array.from({ length: 14 }, (_, index) => ({
    serviceDate: `2026-04-${String(index + 5).padStart(2, "0")}`,
    dayLabel: ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][index % 7],
    dateLabel: `Apr ${index + 5}`,
    forecastExpected: 120 + index,
    forecastLow: 108 + index,
    forecastHigh: 138 + index,
    planningRangeLabel: `${112 + index}-${142 + index}`,
    posture: index % 3 === 0 ? "soft" : "normal",
    serviceState: "normal_service",
    headline: `Night ${index + 1}`,
    summary: `Dinner is expected around ${120 + index} covers.`,
    topDrivers: [{ id: "weather", label: "Weather" }],
    majorUncertainties: [],
    baselineComparison: {
      baselineCovers: 112 + index,
      deltaPct: index % 3 === 0 ? -6 : 8,
      deltaCovers: index % 3 === 0 ? -7 : 9,
      badgeText: index % 3 === 0 ? "-6%" : "+8%",
      heroText: index % 3 === 0 ? "-6% vs usual" : "+8% vs usual",
    },
    recentChange: index < 5
      ? {
          previousExpected: 114 + index,
          currentExpected: 120 + index,
          deltaCovers: 6,
          snapshotReason: "material_change",
          text: `Revised up from ${114 + index} to ${120 + index} since last publish.`,
          compactText: "Revised +6 since last publish",
        }
      : null,
    status: {
      planStatus: "not_required",
      learningStatus: "none",
      actualsStatus: "not_due",
      watchStatus: "none",
    },
  }));
}

function buildWorkspace(
  restaurantName: string,
  setupBootstrap: SetupBootstrapState,
  cards: ForecastCard[],
  missingActuals: NonNullable<Workspace["dashboard"]>["missingActuals"] = [],
  servicePlanWindow: NonNullable<Workspace["dashboard"]>["servicePlanWindow"] = null,
  learningAgenda: NonNullable<Workspace["dashboard"]>["learningAgenda"] = [],
): Workspace {
  return {
    mode: "operations",
    operatorText: buildOperatorText(),
    operators: [
      {
        operatorId: "pw_smoke",
        restaurantName,
        forecastReady: cards.length === 14,
        onboardingState: "cold_start_ready",
      },
    ],
    operator: {
      operatorId: "pw_smoke",
      restaurantName,
      canonicalAddress: "11 W 53rd St, New York, NY 10019",
      city: "New York",
      timezone: "America/New_York",
      neighborhoodType: "mixed_urban",
      demandMix: "mixed",
      patioEnabled: true,
      patioSeatCapacity: 24,
      patioSeasonMode: "seasonal",
      onboardingState: "cold_start_ready",
    },
    setupBootstrap,
    onboarding: {
      forecastReady: true,
      summary: { improvements: [] },
      draft: {
        ...buildDraft(),
        restaurantName,
        canonicalAddress: "11 W 53rd St, New York, NY 10019",
        city: "New York",
        monThu: 120,
        fri: 150,
        sat: 170,
        sun: 110,
        patioEnabled: true,
        patioSeatCapacity: 24,
        transitRelevance: true,
      },
      options: buildBootstrapPayload().onboarding.options,
    },
    dashboard: {
      referenceDate: "2026-04-05",
      cards,
      contextLine: "Tonight looks steady. The next two weeks are loaded.",
      latestRefresh: null,
      pendingNotificationCount: 0,
      openServiceStateSuggestions: [],
      operatorAttentionSummary: {
        moment_label: "Morning planning",
        primary_focus_key: "pending_operator_action",
        ordered_section_keys: [
          "pending_operator_action",
          "current_operational_watchout",
          "current_uncertainty",
          "latest_material_change",
          "best_next_question",
        ],
        latest_material_change: null,
        current_operational_watchout: null,
        pending_operator_action: null,
        current_uncertainty: null,
        best_next_question: null,
      },
      missingActuals,
      servicePlanWindow,
      learningAgenda,
    },
    chat: {
      phase: "operations",
      placeholder: "Ask about the week ahead, or review any actuals due...",
      messages: [
        {
          messageId: 1,
          role: "assistant",
          kind: "message",
          content: "Forecasts are ready.",
          createdAt: "2026-04-05T10:00:00",
        },
      ],
    },
  };
}

describe("App", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.history.pushState({}, "", "/");
    vi.spyOn(window, "confirm").mockReturnValue(true);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("supports onboarding interactions and transitions into the 14-day operations view", async () => {
    const restaurantName = "Vitest Smoke";
    const cards = buildCards();
    const pendingBootstrap: SetupBootstrapState = {
      operatorId: "pw_smoke",
      status: "running",
      message: "Setting up your account. Historical weather and the first refresh are running in the background.",
      steps: ["Setting up your account.", "Refreshing forecasts so the setup is immediately usable."],
      startedAt: "2026-04-05T10:00:00",
      updatedAt: "2026-04-05T10:00:00",
      completedAt: null,
      failedAt: null,
      failureReason: null,
    };
    const completedBootstrap: SetupBootstrapState = {
      ...pendingBootstrap,
      status: "completed",
      message: "Setup finished. Your forecasts are ready.",
      completedAt: "2026-04-05T10:00:10",
    };
    const bootstrapPayload = buildBootstrapPayload();
    const pendingWorkspace = buildWorkspace(restaurantName, pendingBootstrap, []);
    const completedWorkspace = buildWorkspace(restaurantName, completedBootstrap, cards);
    let workspacePolls = 0;

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input.toString();
        const method = init?.method ?? "GET";
        if (url === "/api/bootstrap") {
          if (method === "GET" && workspacePolls > 0) {
            return jsonResponse({
              ...bootstrapPayload,
              operators: [
                {
                  operatorId: "pw_smoke",
                  restaurantName,
                  forecastReady: true,
                  onboardingState: "cold_start_ready",
                },
              ],
              defaultOperatorId: "pw_smoke",
            });
          }
          return jsonResponse(bootstrapPayload);
        }
        if (url === "/api/onboarding/complete" && method === "POST") {
          return jsonResponse({
            operatorId: "pw_smoke",
            bootstrap: pendingBootstrap,
            workspace: pendingWorkspace,
          });
        }
        if (url === "/api/operators/pw_smoke/workspace" && method === "GET") {
          workspacePolls += 1;
          return jsonResponse(completedWorkspace);
        }
        throw new Error(`Unhandled fetch: ${method} ${url}`);
      }),
    );

    const user = userEvent.setup();
    render(<App />);

    await screen.findByRole("heading", { name: "Get your restaurant forecast-ready" });

    await user.type(screen.getByLabelText("Restaurant name"), restaurantName);
    await user.type(screen.getByLabelText("Street address"), "11 W 53rd St, New York, NY 10019");
    await user.type(screen.getByLabelText("City"), "New York");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    await user.type(screen.getByLabelText("Mon-Thu"), "120");
    await user.type(screen.getByLabelText("Fri"), "150");
    await user.type(screen.getByLabelText("Sat"), "170");
    await user.type(screen.getByLabelText("Sun"), "110");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    await user.click(screen.getByRole("checkbox", { name: "We have patio or outdoor seating" }));
    expect(screen.getByLabelText("Patio seat count")).toBeTruthy();
    await user.type(screen.getByLabelText("Patio seat count"), "24");
    await user.click(screen.getByRole("checkbox", { name: "Transit nearby" }));
    await user.click(screen.getByRole("button", { name: "Finish Setup" }));

    await screen.findByRole("heading", { name: "Setting up your account" });

    await screen.findByRole("heading", { name: restaurantName }, { timeout: 7000 });
    await waitFor(() => {
      expect(document.querySelectorAll(".forecast-card")).toHaveLength(14);
    }, { timeout: 7000 });

    await user.click(screen.getByRole("button", { name: /forecast/i }));
    await waitFor(() => {
      expect(document.querySelectorAll(".pyramid-card")).toHaveLength(14);
    }, { timeout: 7000 });
  });

  it("allows a reviewed history upload to drive onboarding baselines", async () => {
    const restaurantName = "Upload Path";
    const bootstrapPayload = buildBootstrapPayload();
    const pendingBootstrap: SetupBootstrapState = {
      operatorId: "upload_path",
      status: "running",
      message: "Setting up your account. Historical weather and the first refresh are running in the background.",
      steps: ["Setting up your account.", "Refreshing forecasts so the setup is immediately usable."],
      startedAt: "2026-04-05T10:00:00",
      updatedAt: "2026-04-05T10:00:00",
      completedAt: null,
      failedAt: null,
      failureReason: null,
    };
    const pendingWorkspace = buildWorkspace(restaurantName, pendingBootstrap, []);

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input.toString();
        const method = init?.method ?? "GET";
        if (url === "/api/bootstrap" && method === "GET") {
          return jsonResponse(bootstrapPayload);
        }
        if (url === "/api/onboarding/review-history-upload" && method === "POST") {
          return jsonResponse({
            accepted: true,
            summary: "The upload is ready to use for onboarding baselines and a local weather reference.",
            upload_token: "hist_demo",
            file_name: "history.csv",
            usable_rows: 365,
            skipped_rows: 0,
            normal_service_rows: 350,
            distinct_months: 12,
            seasons_covered: ["Fall", "Spring", "Summer", "Winter"],
            baseline_values: {
              mon_thu: 118,
              fri: 149,
              sat: 171,
              sun: 112,
            },
            format_confidence: "high",
            warnings: [],
            ai_summary: "The file looks like a clean year of dinner covers with the required columns.",
            ai_warnings: [],
            requirement_failures: [],
            mapping: {
              service_date: "service_date",
              realized_total_covers: "total_covers",
            },
            first_service_date: "2025-04-01",
            last_service_date: "2026-03-31",
          });
        }
        if (url === "/api/onboarding/complete" && method === "POST") {
          const body = JSON.parse(String(init?.body ?? "{}"));
          expect(body.forecastInputMode).toBe("historical_upload");
          expect(body.historicalUploadToken).toBe("hist_demo");
          return jsonResponse({
            operatorId: "upload_path",
            bootstrap: pendingBootstrap,
            workspace: pendingWorkspace,
          });
        }
        throw new Error(`Unhandled fetch: ${method} ${url}`);
      }),
    );

    const user = userEvent.setup();
    render(<App />);

    await screen.findByRole("heading", { name: "Get your restaurant forecast-ready" });
    await user.type(screen.getByLabelText("Restaurant name"), restaurantName);
    await user.type(screen.getByLabelText("Street address"), "11 W 53rd St, New York, NY 10019");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    await user.click(screen.getByRole("button", { name: /Upload 12 months/i }));
    const upload = new File(
      ["service_date,total_covers\n2025-04-01,110\n"],
      "history.csv",
      { type: "text/csv" },
    );
    await user.upload(screen.getByLabelText("Historical cover file"), upload);

    await screen.findAllByText("The upload is ready to use for onboarding baselines and a local weather reference.");
    expect(screen.getByText("Derived Mon-Thu: 118")).toBeTruthy();
    expect(screen.getByText("Derived Sat: 171")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByLabelText("Guest mix");
    await user.click(screen.getByRole("button", { name: "Finish Setup" }));

    await screen.findByRole("heading", { name: "Setting up your account" });
  });

  it("renders baseline comparison metrics and recent change copy on forecast cards", async () => {
    const restaurantName = "Comparison View";
    const cards = buildCards();
    const setupBootstrap: SetupBootstrapState = {
      operatorId: "pw_smoke",
      status: "completed",
      message: "Setup finished. Your forecasts are ready.",
      steps: [],
      startedAt: "2026-04-05T10:00:00",
      updatedAt: "2026-04-05T10:00:10",
      completedAt: "2026-04-05T10:00:10",
      failedAt: null,
      failureReason: null,
    };
    const workspace = buildWorkspace(restaurantName, setupBootstrap, cards);

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input.toString();
        const method = init?.method ?? "GET";
        if (url === "/api/bootstrap" && method === "GET") {
          return jsonResponse({
            ...buildBootstrapPayload(),
            operators: [
              {
                operatorId: "pw_smoke",
                restaurantName,
                forecastReady: true,
                onboardingState: "cold_start_ready",
              },
            ],
            defaultOperatorId: "pw_smoke",
          });
        }
        if (url === "/api/operators/pw_smoke/workspace" && method === "GET") {
          return jsonResponse(workspace);
        }
        throw new Error(`Unhandled fetch: ${method} ${url}`);
      }),
    );

    const user = userEvent.setup();
    render(<App />);

    await screen.findByRole("heading", { name: restaurantName });
    expect(screen.getAllByText("+8%").length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: /forecast/i }));
    await screen.findByText("+8% vs usual");
    await screen.findByText("Revised up from 114 to 120 since last publish.");

    await user.click(screen.getByRole("button", { name: "Table view" }));
    await screen.findByRole("columnheader", { name: "% Covers Change" });
    expect(screen.queryByRole("columnheader", { name: "Full range" })).toBeNull();
  });

  it("logs missing actuals through the actuals flow", async () => {
    const restaurantName = "Actuals Flow";
    const cards = buildCards();
    const setupBootstrap: SetupBootstrapState = {
      operatorId: "pw_smoke",
      status: "completed",
      message: "Setup finished. Your forecasts are ready.",
      steps: [],
      startedAt: "2026-04-05T10:00:00",
      updatedAt: "2026-04-05T10:00:10",
      completedAt: "2026-04-05T10:00:10",
      failedAt: null,
      failureReason: null,
    };
    const missingActuals = [
      {
        serviceDate: "2026-04-04",
        serviceWindow: "dinner",
        forecastExpected: 118,
      },
    ];
    const workspaceWithMissingActual = buildWorkspace(restaurantName, setupBootstrap, cards, missingActuals);
    const workspaceAfterLog = buildWorkspace(restaurantName, setupBootstrap, cards, []);

    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      if (url === "/api/bootstrap" && method === "GET") {
        return jsonResponse({
          ...buildBootstrapPayload(),
          operators: [
            {
              operatorId: "pw_smoke",
              restaurantName,
              forecastReady: true,
              onboardingState: "cold_start_ready",
            },
          ],
          defaultOperatorId: "pw_smoke",
        });
      }
      if (url === "/api/operators/pw_smoke/workspace" && method === "GET") {
        return jsonResponse(workspaceWithMissingActual);
      }
      if (url === "/api/operators/pw_smoke/actuals" && method === "POST") {
        const payload = JSON.parse(String(init?.body ?? "{}"));
        expect(payload.serviceDate).toBe("2026-04-04");
        expect(payload.realizedTotalCovers).toBe(126);
        expect(payload.realizedReservedCovers).toBe(84);
        expect(payload.realizedWalkInCovers).toBe(42);
        return jsonResponse({
          result: {
            success: true,
            message: "Logged 126 covers for 2026-04-04. Learning streams updated.",
            data: {
              serviceDate: "2026-04-04",
              realizedTotalCovers: 126,
              learned: true,
              evaluated: true,
            },
          },
          workspace: workspaceAfterLog,
        });
      }
      throw new Error(`Unhandled fetch: ${method} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(<App />);

    await screen.findByRole("heading", { name: restaurantName });
    await screen.findByRole("heading", { name: "Log the missing totals" });

    await user.type(screen.getByLabelText("Total covers"), "126");
    await user.type(screen.getByLabelText("Reserved covers"), "84");
    await user.type(screen.getByLabelText("Walk-in covers"), "42");
    await user.click(screen.getByRole("button", { name: "Save Actuals" }));

    await screen.findByText("Logged 126 covers for 2026-04-04. Learning streams updated.");
    expect(screen.queryByRole("heading", { name: "Log the missing totals" })).toBeNull();
  });

  it("logs week-ahead operating plans through the plan flow", async () => {
    const restaurantName = "Plan Flow";
    const cards = buildCards();
    const setupBootstrap: SetupBootstrapState = {
      operatorId: "pw_smoke",
      status: "completed",
      message: "Setup finished. Your forecasts are ready.",
      steps: [],
      startedAt: "2026-04-05T10:00:00",
      updatedAt: "2026-04-05T10:00:10",
      completedAt: "2026-04-05T10:00:10",
      failedAt: null,
      failureReason: null,
    };
    const servicePlanWindow = {
      promptDate: "2026-04-08",
      windowStart: "2026-04-10",
      windowEnd: "2026-04-12",
      windowLabel: "Friday-Sunday",
      dueCount: 3,
      pendingDates: ["2026-04-10", "2026-04-11", "2026-04-12"],
      entries: [
        { serviceDate: "2026-04-10", serviceState: "normal_service", plannedTotalCovers: null, estimatedReductionPct: null, note: "", reviewed: false, updatedAt: null },
        { serviceDate: "2026-04-11", serviceState: "normal_service", plannedTotalCovers: null, estimatedReductionPct: null, note: "", reviewed: false, updatedAt: null },
        { serviceDate: "2026-04-12", serviceState: "normal_service", plannedTotalCovers: null, estimatedReductionPct: null, note: "", reviewed: false, updatedAt: null },
      ],
    } satisfies NonNullable<Workspace["dashboard"]>["servicePlanWindow"];
    const updatedServicePlanWindow = {
      ...servicePlanWindow,
      dueCount: 2,
      pendingDates: ["2026-04-11", "2026-04-12"],
      entries: [
        { serviceDate: "2026-04-10", serviceState: "private_event_or_buyout", plannedTotalCovers: 84, estimatedReductionPct: null, note: "Buyout at 7 PM.", reviewed: true, updatedAt: "2026-04-08T10:00:00" },
        servicePlanWindow.entries[1],
        servicePlanWindow.entries[2],
      ],
    } satisfies NonNullable<Workspace["dashboard"]>["servicePlanWindow"];

    const workspaceWithPlanPrompt = buildWorkspace(restaurantName, setupBootstrap, cards, [], servicePlanWindow);
    const workspaceAfterPlanSave = buildWorkspace(restaurantName, setupBootstrap, cards, [], updatedServicePlanWindow);

    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      if (url === "/api/bootstrap" && method === "GET") {
        return jsonResponse({
          ...buildBootstrapPayload(),
          operators: [
            {
              operatorId: "pw_smoke",
              restaurantName,
              forecastReady: true,
              onboardingState: "cold_start_ready",
            },
          ],
          defaultOperatorId: "pw_smoke",
        });
      }
      if (url === "/api/operators/pw_smoke/workspace?referenceDate=2026-04-08" && method === "GET") {
        return jsonResponse(workspaceWithPlanPrompt);
      }
      if (url === "/api/operators/pw_smoke/service-plan" && method === "POST") {
        const payload = JSON.parse(String(init?.body ?? "{}"));
        expect(payload.serviceDate).toBe("2026-04-10");
        expect(payload.serviceState).toBe("private_event_or_buyout");
        expect(payload.plannedTotalCovers).toBe(84);
        expect(payload.note).toBe("Buyout at 7 PM.");
        return jsonResponse({
          result: {
            success: true,
            message: "Saved the operating plan for 2026-04-10 as private event or buyout. Planned around 84 covers.",
            data: {
              serviceDate: "2026-04-10",
              serviceState: "private_event_or_buyout",
              plannedTotalCovers: 84,
              estimatedReductionPct: null,
              ranRefresh: false,
            },
          },
          workspace: workspaceAfterPlanSave,
        });
      }
      throw new Error(`Unhandled fetch: ${method} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    window.history.pushState({}, "", "/?referenceDate=2026-04-08");

    const user = userEvent.setup();
    render(<App />);

    await screen.findByRole("heading", { name: restaurantName });
    await screen.findByRole("heading", { name: "Review service plans" });

    await user.selectOptions(screen.getByLabelText("Planned service state"), "private_event_or_buyout");
    await user.type(screen.getByLabelText("Planned total covers"), "84");
    await user.type(screen.getByLabelText("Plan note"), "Buyout at 7 PM.");
    await user.click(screen.getByRole("button", { name: "Save Plan" }));

    await screen.findByText("Saved the operating plan for 2026-04-10 as private event or buyout. Planned around 84 covers.");
    await screen.findAllByText("2 nights still need a saved plan.");
  });

  it("keeps the optional onboarding step reachable when step two submits early", async () => {
    const bootstrapPayload = buildBootstrapPayload();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      if (url === "/api/bootstrap" && method === "GET") {
        return jsonResponse(bootstrapPayload);
      }
      if (url === "/api/onboarding/complete" && method === "POST") {
        throw new Error("step two should not submit onboarding");
      }
      throw new Error(`Unhandled fetch: ${method} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(<App />);

    await screen.findByRole("heading", { name: "Get your restaurant forecast-ready" });
    await user.type(screen.getByLabelText("Restaurant name"), "Wizard Check");
    await user.type(screen.getByLabelText("Street address"), "11 W 53rd St, New York, NY 10019");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    await user.type(screen.getByLabelText("Mon-Thu"), "120");
    await user.type(screen.getByLabelText("Fri"), "150");
    await user.type(screen.getByLabelText("Sat"), "170");
    await user.type(screen.getByLabelText("Sun"), "110{enter}");

    await screen.findByLabelText("Guest mix");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("sends quick-question replies with the learning agenda key", async () => {
    const restaurantName = "Question Flow";
    const cards = buildCards();
    const setupBootstrap: SetupBootstrapState = {
      operatorId: "pw_smoke",
      status: "completed",
      message: "Setup finished.",
      steps: [],
      startedAt: "2026-04-05T10:00:00",
      updatedAt: "2026-04-05T10:00:10",
      completedAt: "2026-04-05T10:00:10",
      failedAt: null,
      failureReason: null,
    };
    const learningAgenda = [
      {
        agenda_key: "qualitative_pattern::staffing",
        status: "open",
        question_kind: "yes_no",
        service_date: "2026-04-16",
        communicationPayload: {
          category: "open_question",
          what_is_true_now: "Staffing constraints have shown up in a few recent notes.",
          why_it_matters: "That can explain misses and service changes even when demand looks normal.",
          one_question: "Is that a recurring issue I should keep in mind",
        },
      },
    ] satisfies NonNullable<Workspace["dashboard"]>["learningAgenda"];
    const workspaceWithQuestion = buildWorkspace(restaurantName, setupBootstrap, cards, [], null, learningAgenda);
    const workspaceAfterReply = buildWorkspace(restaurantName, setupBootstrap, cards);

    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      if (url === "/api/bootstrap" && method === "GET") {
        return jsonResponse({
          ...buildBootstrapPayload(),
          operators: [
            {
              operatorId: "pw_smoke",
              restaurantName,
              forecastReady: true,
              onboardingState: "cold_start_ready",
            },
          ],
          defaultOperatorId: "pw_smoke",
        });
      }
      if (url === "/api/operators/pw_smoke/workspace" && method === "GET") {
        return jsonResponse(workspaceWithQuestion);
      }
      if (url === "/api/operators/pw_smoke/chat" && method === "POST") {
        const payload = JSON.parse(String(init?.body ?? "{}"));
        expect(payload.message).toBe("Yes");
        expect(payload.learningAgendaKey).toBe("qualitative_pattern::staffing");
        return jsonResponse({
          operatorId: "pw_smoke",
          assistantMessage: "I recorded your answer.",
          phase: "operations",
          workspace: workspaceAfterReply,
        });
      }
      throw new Error(`Unhandled fetch: ${method} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(<App />);

    await screen.findByRole("heading", { name: restaurantName });
    await screen.findByText(/Staffing constraints have shown up/);
    await user.click(screen.getByRole("button", { name: "Yes" }));

    await waitFor(() => {
      expect(screen.queryByText(/Staffing constraints have shown up/)).toBeNull();
    });
  });

  it("hides background outside-source internals from the operator chat surface", async () => {
    const restaurantName = "Hidden Sources";
    const cards = buildCards();
    const setupBootstrap: SetupBootstrapState = {
      operatorId: "pw_smoke",
      status: "completed",
      message: "Setup finished.",
      steps: [],
      startedAt: "2026-04-05T10:00:00",
      updatedAt: "2026-04-05T10:00:10",
      completedAt: "2026-04-05T10:00:10",
      failedAt: null,
      failureReason: null,
    };
    const workspace = buildWorkspace(restaurantName, setupBootstrap, cards);

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input.toString();
        const method = init?.method ?? "GET";
        if (url === "/api/bootstrap" && method === "GET") {
          return jsonResponse({
            ...buildBootstrapPayload(),
            operators: [
              {
                operatorId: "pw_smoke",
                restaurantName,
                forecastReady: true,
                onboardingState: "cold_start_ready",
              },
            ],
            defaultOperatorId: "pw_smoke",
          });
        }
        if (url === "/api/operators/pw_smoke/workspace" && method === "GET") {
          return jsonResponse(workspace);
        }
        throw new Error(`Unhandled fetch: ${method} ${url}`);
      }),
    );

    render(<App />);

    await screen.findByRole("heading", { name: restaurantName });
    expect(screen.queryByText("Tracked outside sources")).toBeNull();
    expect(screen.queryByText("capital_bikeshare_station_pressure")).toBeNull();
  });
});
