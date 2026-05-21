import type { ForecastCardStatus } from "../types";

export type PipKind = "plan" | "learning" | "actuals" | "watchout";

export type PipTier = "small" | "medium" | "hero";
export type PipLabelMap = Record<PipKind, Record<string, string>>;

type StatusPipProps = {
  kind: PipKind;
  variant: string;
  showLabel?: boolean;
  size?: number;
  labels?: PipLabelMap;
};

const FALLBACK_PIP_LABELS: PipLabelMap = {
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
};

export function pipLabel(kind: PipKind, variant: string, labels: PipLabelMap = FALLBACK_PIP_LABELS): string {
  return labels[kind]?.[variant] ?? "";
}

/**
 * A single status pip. Each kind has a distinct shape so the pip is
 * legible without color (color reinforces, doesn't carry).
 *
 *   plan      → square
 *   learning  → dot
 *   actuals   → circle
 *   watchout  → triangle
 */
export function StatusPip({ kind, variant, showLabel = false, size = 10, labels = FALLBACK_PIP_LABELS }: StatusPipProps) {
  const label = pipLabel(kind, variant, labels);
  const stateClass = `is-${variant}`;
  return (
    <span
      className={`status-pip status-pip-${kind} ${stateClass}`}
      title={label || undefined}
    >
      <span className="status-pip-mark" aria-hidden>
        {renderShape(kind, variant, size)}
      </span>
      {showLabel && label ? <span className="status-pip-label">{label}</span> : null}
    </span>
  );
}

function renderShape(kind: PipKind, variant: string, size: number) {
  const stroke = 1.4;
  const view = 16;
  const common = {
    width: size,
    height: size,
    viewBox: `0 0 ${view} ${view}`,
    fill: "none" as const,
    stroke: "currentColor" as const,
    strokeWidth: stroke,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
  };

  switch (kind) {
    case "plan": {
      const filled = variant === "submitted";
      const stale = variant === "stale";
      return (
        <svg {...common}>
          <rect
            x={2.5}
            y={2.5}
            width={11}
            height={11}
            rx={1.5}
            fill={filled || stale ? "currentColor" : "none"}
          />
          {stale ? (
            <line
              x1={4}
              y1={12}
              x2={12}
              y2={4}
              stroke="var(--ink-0, #0E1218)"
              strokeWidth={stroke}
            />
          ) : null}
        </svg>
      );
    }
    case "learning": {
      const overdue = variant === "overdue_question";
      return (
        <svg {...common}>
          <circle cx={8} cy={8} r={3.2} fill="currentColor" stroke="none" />
          {overdue ? (
            <>
              <line x1={13} y1={3} x2={13} y2={8} />
              <circle cx={13} cy={11} r={0.7} fill="currentColor" stroke="none" />
            </>
          ) : null}
        </svg>
      );
    }
    case "actuals": {
      const filled = variant === "recorded";
      const overdue = variant === "overdue";
      return (
        <svg {...common}>
          <circle cx={8} cy={8} r={5} fill={filled ? "currentColor" : "none"} />
          {overdue ? (
            <>
              <line x1={13.5} y1={2.5} x2={13.5} y2={7.5} />
              <circle cx={13.5} cy={10.5} r={0.7} fill="currentColor" stroke="none" />
            </>
          ) : null}
        </svg>
      );
    }
    case "watchout": {
      return (
        <svg {...common}>
          <path d="M8 2.5 L14 13 L2 13 Z" fill="currentColor" stroke="currentColor" />
          <line x1={8} y1={6.5} x2={8} y2={9.5} stroke="var(--ink-0, #0E1218)" strokeWidth={1.6} />
          <circle cx={8} cy={11.4} r={0.7} fill="var(--ink-0, #0E1218)" stroke="none" />
        </svg>
      );
    }
    default:
      return null;
  }
}

type StatusPipRowProps = {
  status: ForecastCardStatus;
  tier: PipTier;
  /** Whether the card represents today or a past service date. Controls actuals visibility. */
  actualsRelevant?: boolean;
  className?: string;
  labels?: PipLabelMap;
};

/**
 * Renders the right combination of pips for a given card tier.
 *
 * Priority order (small tier shows highest only):
 *   watchout > learning > plan
 *
 * Actuals are never shown unless `actualsRelevant` (today / historical).
 */
export function StatusPipRow({ status, tier, actualsRelevant = false, className, labels = FALLBACK_PIP_LABELS }: StatusPipRowProps) {
  const pips = collectPips(status, actualsRelevant);
  if (pips.length === 0) {
    return null;
  }

  if (tier === "small") {
    const top = pips[0];
    return (
      <span className={`status-pip-row status-pip-row-small ${className ?? ""}`}>
        <StatusPip kind={top.kind} variant={top.variant} size={9} labels={labels} />
      </span>
    );
  }

  const showLabels = tier === "hero";
  return (
    <span className={`status-pip-row status-pip-row-${tier} ${className ?? ""}`}>
      {pips.map((pip) => (
        <StatusPip
          key={pip.kind}
          kind={pip.kind}
          variant={pip.variant}
          showLabel={showLabels}
          size={tier === "hero" ? 12 : 10}
          labels={labels}
        />
      ))}
    </span>
  );
}

type CollectedPip = { kind: PipKind; variant: string; weight: number };

function collectPips(status: ForecastCardStatus, actualsRelevant: boolean): CollectedPip[] {
  const out: CollectedPip[] = [];

  if (status.watchStatus !== "none") {
    out.push({ kind: "watchout", variant: status.watchStatus, weight: 4 });
  }
  if (status.learningStatus !== "none") {
    out.push({ kind: "learning", variant: status.learningStatus, weight: 3 });
  }
  if (status.planStatus !== "not_required" && status.planStatus !== "submitted") {
    out.push({ kind: "plan", variant: status.planStatus, weight: 2 });
  } else if (status.planStatus === "submitted") {
    // Submitted plan still shown on hero/medium tiers as a positive confirmation,
    // but with low weight so it never wins the small-tier slot.
    out.push({ kind: "plan", variant: "submitted", weight: 1 });
  }
  if (actualsRelevant && status.actualsStatus !== "not_due") {
    const weight =
      status.actualsStatus === "overdue" ? 3.5 : status.actualsStatus === "due" ? 2.5 : 1.5;
    out.push({ kind: "actuals", variant: status.actualsStatus, weight });
  }

  out.sort((a, b) => b.weight - a.weight);
  return out;
}
