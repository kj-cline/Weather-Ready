import type {
  ActualEntryDraft,
  BootstrapPayload,
  HistoricalUploadReview,
  OnboardingDraft,
  ServicePlanEntryDraft,
  SetupBootstrapState,
  SuggestedMessage,
  Workspace,
} from "./types";

async function request<T>(input: string, init?: RequestInit): Promise<T> {
  const response = await fetch(input, {
    headers: {
      "Content-Type": "application/json"
    },
    ...init
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(String(error.detail ?? `Request failed with ${response.status}`));
  }
  return response.json() as Promise<T>;
}

export function getBootstrap(): Promise<BootstrapPayload> {
  return request<BootstrapPayload>("/api/bootstrap");
}

export function getWorkspace(operatorId: string, referenceDate?: string | null): Promise<Workspace> {
  const params = new URLSearchParams();
  if (referenceDate) {
    params.set("referenceDate", referenceDate);
  }
  const suffix = params.size ? `?${params.toString()}` : "";
  return request<Workspace>(`/api/operators/${operatorId}/workspace${suffix}`);
}

export function completeOnboarding(payload: OnboardingDraft & { operatorId?: string | null }): Promise<{
  operatorId: string;
  bootstrap?: SetupBootstrapState;
  workspace: Workspace;
}> {
  return request("/api/onboarding/complete", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function reviewHistoricalUpload(fileName: string, content: string): Promise<HistoricalUploadReview> {
  return request("/api/onboarding/review-history-upload", {
    method: "POST",
    body: JSON.stringify({ fileName, content })
  });
}

export function sendChatMessage(
  operatorId: string,
  message: string,
  referenceDate?: string | null,
  learningAgendaKey?: string | null,
): Promise<{
  operatorId: string;
  assistantMessage: string;
  phase: string;
  suggestedMessages: SuggestedMessage[];
  workspace: Workspace;
}> {
  return request(`/api/operators/${operatorId}/chat`, {
    method: "POST",
    body: JSON.stringify({ message, referenceDate: referenceDate ?? null, learningAgendaKey: learningAgendaKey ?? null })
  });
}

export function getChatHistory(operatorId: string, beforeId?: number | null, limit = 30): Promise<{
  messages: Workspace["chat"]["messages"];
  hasMore: boolean;
}> {
  const params = new URLSearchParams();
  if (beforeId != null) {
    params.set("beforeId", String(beforeId));
  }
  params.set("limit", String(limit));
  return request(`/api/operators/${operatorId}/chat-history?${params.toString()}`);
}

export function submitActualEntry(operatorId: string, payload: ActualEntryDraft, referenceDate?: string | null): Promise<{
  result: {
    success: boolean;
    message: string;
    data: {
      serviceDate: string;
      realizedTotalCovers: number;
      learned: boolean;
      evaluated: boolean;
      noteCaptured?: boolean;
      correctionStaged?: boolean;
    };
  };
  workspace: Workspace;
}> {
  return request(`/api/operators/${operatorId}/actuals`, {
    method: "POST",
    body: JSON.stringify({ ...payload, referenceDate: referenceDate ?? null })
  });
}

export function submitServicePlan(operatorId: string, payload: ServicePlanEntryDraft, referenceDate?: string | null): Promise<{
  result: {
    success: boolean;
    message: string;
    data: {
      serviceDate: string;
      serviceState: string;
      plannedTotalCovers: number | null;
      estimatedReductionPct: number | null;
      ranRefresh: boolean;
    };
  };
  workspace: Workspace;
}> {
  return request(`/api/operators/${operatorId}/service-plan`, {
    method: "POST",
    body: JSON.stringify({ ...payload, referenceDate: referenceDate ?? null })
  });
}

export function refreshWorkspace(operatorId: string, reason?: string, referenceDate?: string | null): Promise<{
  result: {
    success: boolean;
    message: string;
  };
  workspace: Workspace;
}> {
  return request(`/api/operators/${operatorId}/refresh`, {
    method: "POST",
    body: JSON.stringify({ reason, referenceDate: referenceDate ?? null })
  });
}

export function retrySetupBootstrap(operatorId: string, referenceDate?: string | null): Promise<{
  setupBootstrap: SetupBootstrapState;
  workspace: Workspace;
}> {
  const params = new URLSearchParams();
  if (referenceDate) {
    params.set("referenceDate", referenceDate);
  }
  const suffix = params.size ? `?${params.toString()}` : "";
  return request(`/api/operators/${operatorId}/setup-bootstrap${suffix}`, {
    method: "POST"
  });
}

export function deleteOperator(operatorId: string): Promise<{ deleted: boolean; operatorId: string }> {
  return request(`/api/operators/${operatorId}`, {
    method: "DELETE"
  });
}
