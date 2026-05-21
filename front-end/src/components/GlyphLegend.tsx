import { useEffect, useRef, useState } from "react";
import { ALL_CONDITION_CODES, WeatherGlyph, weatherLabel } from "./WeatherGlyph";

/**
 * "?" affordance that opens a popover listing all 14 synoptic-inspired
 * weather glyphs with their labels. Closes on outside click or Escape.
 */
export function GlyphLegend() {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;

    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
      }
    }
    function onClick(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }

    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, [open]);

  return (
    <div className="glyph-legend" ref={containerRef}>
      <button
        type="button"
        className={`glyph-legend-trigger ${open ? "is-open" : ""}`}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-label="Weather glyph legend"
      >
        <span aria-hidden>?</span>
        <span className="glyph-legend-trigger-label">Glyph legend</span>
      </button>

      {open ? (
        <div className="glyph-legend-popover" role="dialog" aria-label="Weather glyph legend">
          <div className="glyph-legend-header">
            <div className="glyph-legend-title">Weather glyphs</div>
            <p className="glyph-legend-subtitle">
              Synoptic-inspired marks. Color follows the night&rsquo;s ambient accent.
            </p>
          </div>
          <div className="glyph-legend-grid">
            {ALL_CONDITION_CODES.map((code) => (
              <div key={code} className="glyph-legend-item" data-weather={code}>
                <div className="glyph-legend-icon">
                  <WeatherGlyph code={code} size={28} title={weatherLabel(code)} />
                </div>
                <span className="glyph-legend-label">{weatherLabel(code)}</span>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
