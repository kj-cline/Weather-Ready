import type { ForecastWeather } from "../types";

type ConditionCode = ForecastWeather["conditionCode"];

type GlyphProps = {
  code: ConditionCode;
  size?: number;
  title?: string;
  className?: string;
};

const LABELS: Record<ConditionCode, string> = {
  clear: "Clear",
  partly_cloudy: "Partly cloudy",
  cloudy: "Cloudy",
  overcast: "Overcast",
  rain_light: "Light rain",
  rain_heavy: "Heavy rain",
  storm: "Thunderstorm",
  snow_light: "Light snow",
  snow_heavy: "Heavy snow",
  sleet: "Sleet",
  fog: "Fog",
  wind_high: "High wind",
  heat: "Heat",
  cold: "Cold",
  unknown: "Unknown",
};

export function weatherLabel(code: ConditionCode): string {
  return LABELS[code] ?? "Unknown";
}

export const ALL_CONDITION_CODES: ConditionCode[] = [
  "clear",
  "partly_cloudy",
  "cloudy",
  "overcast",
  "rain_light",
  "rain_heavy",
  "storm",
  "snow_light",
  "snow_heavy",
  "sleet",
  "fog",
  "wind_high",
  "heat",
  "cold",
  "unknown",
];

/**
 * Synoptic-chart inspired glyphs. 24x24 viewBox, 1.5 stroke, currentColor.
 * Slightly simplified versus METAR for legibility at small sizes.
 */
export function WeatherGlyph({ code, size = 20, title, className }: GlyphProps) {
  return (
    <svg
      className={`weather-glyph weather-glyph-${code} ${className ?? ""}`}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      role={title ? "img" : "presentation"}
      aria-label={title ?? undefined}
    >
      {title ? <title>{title}</title> : null}
      {renderGlyph(code)}
    </svg>
  );
}

function renderGlyph(code: ConditionCode) {
  switch (code) {
    case "clear":
      return (
        <>
          <circle cx={12} cy={12} r={4} />
          <line x1={12} y1={2} x2={12} y2={5} />
          <line x1={12} y1={19} x2={12} y2={22} />
          <line x1={2} y1={12} x2={5} y2={12} />
          <line x1={19} y1={12} x2={22} y2={12} />
          <line x1={4.9} y1={4.9} x2={7} y2={7} />
          <line x1={17} y1={17} x2={19.1} y2={19.1} />
          <line x1={4.9} y1={19.1} x2={7} y2={17} />
          <line x1={17} y1={7} x2={19.1} y2={4.9} />
        </>
      );
    case "partly_cloudy":
      return (
        <>
          <circle cx={8} cy={9} r={3} />
          <line x1={8} y1={3} x2={8} y2={4.5} />
          <line x1={2.5} y1={9} x2={4} y2={9} />
          <line x1={3.8} y1={4.8} x2={4.8} y2={5.8} />
          <line x1={11.2} y1={4.8} x2={12.2} y2={5.8} />
          <path d="M9 17h9a3 3 0 0 0 0-6 4 4 0 0 0-7.5-1A3.5 3.5 0 0 0 9 17z" />
        </>
      );
    case "cloudy":
      return <path d="M7 18h11a4 4 0 0 0 0-8 5 5 0 0 0-9.6-1A4 4 0 0 0 7 18z" />;
    case "overcast":
      return (
        <>
          <path d="M7 15h11a4 4 0 0 0 0-8 5 5 0 0 0-9.6-1A4 4 0 0 0 7 15z" />
          <line x1={5} y1={20} x2={19} y2={20} />
        </>
      );
    case "rain_light":
      return (
        <>
          <path d="M7 14h11a4 4 0 0 0 0-8 5 5 0 0 0-9.6-1A4 4 0 0 0 7 14z" />
          <line x1={12} y1={17} x2={11} y2={21} />
        </>
      );
    case "rain_heavy":
      return (
        <>
          <path d="M7 14h11a4 4 0 0 0 0-8 5 5 0 0 0-9.6-1A4 4 0 0 0 7 14z" />
          <line x1={9} y1={17} x2={8} y2={21} />
          <line x1={13} y1={17} x2={12} y2={21} />
          <line x1={17} y1={17} x2={16} y2={21} />
        </>
      );
    case "storm":
      return (
        <>
          <path d="M7 13h11a4 4 0 0 0 0-8 5 5 0 0 0-9.6-1A4 4 0 0 0 7 13z" />
          <path d="M13 15l-3 4h3l-2 4" />
        </>
      );
    case "snow_light":
      return (
        <>
          <path d="M7 14h11a4 4 0 0 0 0-8 5 5 0 0 0-9.6-1A4 4 0 0 0 7 14z" />
          <line x1={12} y1={17} x2={12} y2={21} />
          <line x1={10} y1={19} x2={14} y2={19} />
          <line x1={10.5} y1={17.5} x2={13.5} y2={20.5} />
          <line x1={13.5} y1={17.5} x2={10.5} y2={20.5} />
        </>
      );
    case "snow_heavy":
      return (
        <>
          <path d="M7 14h11a4 4 0 0 0 0-8 5 5 0 0 0-9.6-1A4 4 0 0 0 7 14z" />
          <g>
            <line x1={8} y1={17} x2={8} y2={21} />
            <line x1={6.5} y1={19} x2={9.5} y2={19} />
          </g>
          <g>
            <line x1={12} y1={17} x2={12} y2={21} />
            <line x1={10.5} y1={19} x2={13.5} y2={19} />
          </g>
          <g>
            <line x1={16} y1={17} x2={16} y2={21} />
            <line x1={14.5} y1={19} x2={17.5} y2={19} />
          </g>
        </>
      );
    case "sleet":
      return (
        <>
          <path d="M7 14h11a4 4 0 0 0 0-8 5 5 0 0 0-9.6-1A4 4 0 0 0 7 14z" />
          <line x1={9} y1={17} x2={8} y2={21} />
          <g>
            <line x1={14} y1={17} x2={14} y2={21} />
            <line x1={12.5} y1={19} x2={15.5} y2={19} />
          </g>
        </>
      );
    case "fog":
      return (
        <>
          <path d="M3 8h18" />
          <path d="M3 12h18" />
          <path d="M3 16h18" />
          <path d="M3 20h18" />
        </>
      );
    case "wind_high":
      return (
        <>
          <path d="M3 8h13a3 3 0 1 0-3-3" />
          <path d="M3 14h17a2.5 2.5 0 1 1-2.5 2.5" />
          <path d="M3 20h9" />
        </>
      );
    case "heat":
      return (
        <>
          <circle cx={12} cy={10} r={3.5} />
          <line x1={12} y1={2} x2={12} y2={4} />
          <line x1={4.6} y1={10} x2={6.6} y2={10} />
          <line x1={17.4} y1={10} x2={19.4} y2={10} />
          <line x1={5.6} y1={3.6} x2={7} y2={5} />
          <line x1={17} y1={5} x2={18.4} y2={3.6} />
          <path d="M5 18c2-2 4 2 7 0s5 2 7 0" />
        </>
      );
    case "cold":
      return (
        <>
          <line x1={12} y1={3} x2={12} y2={21} />
          <line x1={3} y1={12} x2={21} y2={12} />
          <line x1={5.5} y1={5.5} x2={18.5} y2={18.5} />
          <line x1={18.5} y1={5.5} x2={5.5} y2={18.5} />
          <path d="M10 5l2 2 2-2M10 19l2-2 2 2M5 10l2 2-2 2M19 10l-2 2 2 2" />
        </>
      );
    case "unknown":
    default:
      return (
        <>
          <circle cx={12} cy={12} r={9} />
          <path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.8.4-1 1-1 1.7" />
          <line x1={12} y1={17} x2={12} y2={17.5} />
        </>
      );
  }
}
