from __future__ import annotations

import json
from typing import Any

from stormready_v3.ai.contracts import AgentModelProvider, ExternalSourceGovernanceItem, LocationProfilingResult


def _extract_json_object(raw_text: str) -> dict[str, Any] | None:
    raw_text = raw_text.strip()
    if not raw_text:
        return None
    candidates = [_strip_code_fences(raw_text), raw_text]
    seen: set[str] = set()
    for candidate in candidates:
        normalized = candidate.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        parsed = _parse_json_dict(normalized)
        if parsed is not None:
            return parsed
        for fragment in _json_object_fragments(normalized):
            fragment = fragment.strip()
            if not fragment or fragment in seen:
                continue
            seen.add(fragment)
            parsed = _parse_json_dict(fragment)
            if parsed is not None:
                return parsed
    return None


def _strip_code_fences(raw_text: str) -> str:
    if not raw_text.startswith("```"):
        return raw_text
    lines = raw_text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _parse_json_dict(raw_text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _json_object_fragments(raw_text: str) -> list[str]:
    fragments: list[str] = []
    start_indexes = [index for index, char in enumerate(raw_text) if char == "{"]
    for start_index in start_indexes[:12]:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start_index, len(raw_text)):
            char = raw_text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    fragments.append(raw_text[start_index:index + 1])
                    break
    return fragments


class OpenAIAgentModelProvider(AgentModelProvider):
    def __init__(
        self,
        *,
        api_key: str | None,
        model: str | None,
        base_url: str | None = None,
        azure_api_key: str | None = None,
        azure_endpoint: str | None = None,
        azure_api_version: str | None = None,
        azure_deployment: str | None = None,
        provider_preference: str = "auto",
        reasoning_effort: str = "medium",
        request_timeout_seconds: float = 20.0,
        governance_timeout_seconds: float = 2.5,
        enrichment_timeout_seconds: float = 8.0,
        max_retries: int = 0,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.azure_api_key = azure_api_key
        self.azure_endpoint = azure_endpoint.rstrip("/") if azure_endpoint else None
        self.azure_api_version = azure_api_version
        self.azure_deployment = azure_deployment
        self.provider_preference = provider_preference
        self.reasoning_effort = (reasoning_effort or "medium").strip().lower() or "medium"
        self.request_timeout_seconds = max(0.5, float(request_timeout_seconds or 20.0))
        self.governance_timeout_seconds = max(0.5, float(governance_timeout_seconds or self.request_timeout_seconds))
        self.enrichment_timeout_seconds = max(0.5, float(enrichment_timeout_seconds or self.request_timeout_seconds))
        self.max_retries = max(0, int(max_retries or 0))
        self._client = None
        self._last_failure_reason: str | None = None

    @staticmethod
    def _supported_reasoning_efforts_from_error(error_message: str) -> list[str]:
        marker = "Supported values are:"
        if marker not in error_message:
            return []
        supported_text = error_message.split(marker, 1)[1]
        values: list[str] = []
        for raw_part in supported_text.replace(".", "").split(","):
            cleaned = raw_part.strip().strip("'\"")
            if cleaned:
                values.append(cleaned)
        return values

    def _reasoning_effort_candidates(self) -> list[str]:
        candidates = [self.reasoning_effort]
        for fallback in ("medium", "low", "minimal"):
            if fallback not in candidates:
                candidates.append(fallback)
        return candidates

    def _azure_responses_text(
        self,
        *,
        client: Any,
        deployment: str,
        system_prompt: str,
        user_prompt: str,
        max_output_tokens: int,
        reasoning_effort: str,
        timeout_seconds: float,
    ) -> str | None:
        response = client.responses.create(
            model=str(deployment),
            instructions=system_prompt,
            input=user_prompt,
            max_output_tokens=max_output_tokens,
            reasoning={"effort": reasoning_effort},
            text={"verbosity": "medium"},
            timeout=timeout_seconds,
        )
        return self._response_text(response)

    def configured_provider(self) -> str | None:
        azure_ready = bool(self.azure_api_key and self.azure_endpoint and self.azure_api_version and (self.azure_deployment or self.model))
        openai_ready = bool(self.api_key and self.model)
        preference = (self.provider_preference or "auto").lower()
        if preference == "azure":
            return "azure" if azure_ready else None
        if preference == "openai":
            return "openai" if openai_ready else None
        if azure_ready:
            return "azure"
        if openai_ready:
            return "openai"
        return None

    def is_available(self) -> bool:
        return self.configured_provider() is not None

    def last_failure_reason(self) -> str | None:
        return self._last_failure_reason

    def _set_failure(self, reason: str | None) -> None:
        self._last_failure_reason = reason

    def _client_instance(self):
        if not self.is_available():
            self._set_failure("provider_not_configured")
            return None
        if self._client is not None:
            return self._client
        try:
            from openai import AzureOpenAI, OpenAI
        except Exception as exc:
            self._set_failure(f"client_import_failed:{type(exc).__name__}")
            return None
        provider = self.configured_provider()
        if provider == "azure":
            kwargs: dict[str, Any] = {
                "api_key": self.azure_api_key,
                "azure_endpoint": self.azure_endpoint,
                "api_version": self.azure_api_version,
                "timeout": self.request_timeout_seconds,
                "max_retries": self.max_retries,
            }
            deployment = self.azure_deployment or self.model
            if deployment:
                kwargs["azure_deployment"] = deployment
            self._client = AzureOpenAI(**kwargs)
            self._set_failure(None)
            return self._client
        kwargs = {"api_key": self.api_key, "timeout": self.request_timeout_seconds, "max_retries": self.max_retries}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        self._client = OpenAI(**kwargs)
        self._set_failure(None)
        return self._client

    def _response_text(self, response: Any) -> str | None:
        output_text = getattr(response, "output_text", None)
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()
        for item in getattr(response, "output", []) or []:
            if getattr(item, "type", None) != "message":
                continue
            for content in getattr(item, "content", []) or []:
                text = getattr(content, "text", None)
                if isinstance(text, str) and text.strip():
                    return text.strip()
        return None

    def _azure_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_output_tokens: int = 400,
        timeout_seconds: float | None = None,
    ) -> str | None:
        client = self._client_instance()
        if client is None:
            return None
        deployment = self.azure_deployment or self.model
        if not deployment:
            self._set_failure("azure_missing_deployment")
            return None
        last_error: Exception | None = None
        candidates = self._reasoning_effort_candidates()
        for effort in candidates:
            try:
                text = self._azure_responses_text(
                    client=client,
                    deployment=str(deployment),
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_output_tokens=max_output_tokens,
                    reasoning_effort=effort,
                    timeout_seconds=timeout_seconds or self.request_timeout_seconds,
                )
                self._set_failure(None)
                return text
            except Exception as exc:
                last_error = exc
                message = str(exc)
                supported = self._supported_reasoning_efforts_from_error(message)
                if "reasoning.effort" in message and supported:
                    next_efforts = [item for item in supported if item not in candidates]
                    candidates.extend(next_efforts)
                    continue
                self._set_failure(f"azure_response_error:{type(exc).__name__}")
                return None
        if last_error is not None:
            self._set_failure(f"azure_response_error:{type(last_error).__name__}")
        return None

    def _chat_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        timeout_seconds: float | None = None,
    ) -> str | None:
        client = self._client_instance()
        if client is None:
            return None
        try:
            response = client.chat.completions.create(
                model=str(self.model),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                timeout=timeout_seconds or self.request_timeout_seconds,
            )
        except Exception as exc:
            self._set_failure(f"chat_completion_error:{type(exc).__name__}")
            return None
        content = response.choices[0].message.content if response.choices else None
        if not content:
            self._set_failure("chat_completion_empty")
            return None
        self._set_failure(None)
        return str(content).strip()

    def _model_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_output_tokens: int = 400,
        timeout_seconds: float | None = None,
    ) -> str | None:
        if self.configured_provider() == "azure":
            return self._azure_text(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_output_tokens=max_output_tokens,
                timeout_seconds=timeout_seconds,
            )
        return self._chat_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            timeout_seconds=timeout_seconds,
        )

    def _chat_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_output_tokens: int = 600,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any] | None:
        if self.configured_provider() == "azure":
            raw_text = self._model_text(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_output_tokens=max_output_tokens,
                timeout_seconds=timeout_seconds,
            )
            if raw_text is None:
                return None
            parsed = _extract_json_object(raw_text)
            if parsed is None:
                self._set_failure("azure_json_parse_failed")
                return None
            self._set_failure(None)
            return parsed
        client = self._client_instance()
        if client is None:
            return None
        try:
            response = client.chat.completions.create(
                model=str(self.model),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                timeout=timeout_seconds or self.request_timeout_seconds,
            )
        except Exception as exc:
            self._set_failure(f"chat_json_error:{type(exc).__name__}")
            return None
        content = response.choices[0].message.content if response.choices else None
        if not content:
            self._set_failure("chat_json_empty")
            return None
        parsed = _extract_json_object(str(content).strip())
        if parsed is None:
            self._set_failure("chat_json_parse_failed")
            return None
        self._set_failure(None)
        return parsed

    def structured_json_call(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_output_tokens: int = 800,
    ) -> dict[str, Any] | None:
        return self._chat_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_output_tokens=max_output_tokens,
            timeout_seconds=self.request_timeout_seconds,
        )

    def external_source_governance(
        self,
        *,
        operator_context: dict[str, Any],
        source_candidates: list[dict[str, Any]],
    ) -> list[ExternalSourceGovernanceItem] | None:
        payload = self._chat_json(
            system_prompt=(
                "Rank external data sources for restaurant demand forecasting. Return JSON only. "
                "Schema: {\"items\":[{\"source_name\":\"...\",\"recommended_category\":\"...\","
                "\"recommended_action\":\"...\",\"priority_score\":0.0,\"confidence\":\"low|medium|high\","
                "\"cadence_hint\":\"...\",\"notes\":[\"...\"]}]}. "
                "Categories: traffic_access, events_venues, incidents_safety, neighborhood_demand_proxy, civic_campus, tourism_hospitality. "
                "Actions: keep_curated, promote_candidate, monitor, ignore. "
                "source_name must exactly match input. priority_score 0.0-1.0. "
                "Prioritize sources matching operator location relevance flags. "
                "If source_reliability_history given, weight by usefulness scores. "
                "Favor official, free, structured sources such as GTFS-RT, GBFS, JSON, GeoJSON, ArcGIS, or Socrata feeds. "
                "Penalize generic web pages, broad search ideas, or sources without a clear structured endpoint."
            ),
            user_prompt=json.dumps(
                {
                    "operator_context": operator_context,
                    "source_candidates": source_candidates,
                },
                default=str,
            ),
            max_output_tokens=1600,
            timeout_seconds=self.enrichment_timeout_seconds,
        )
        if payload is None:
            return None
        # Guardrail: build set of valid source names from input
        valid_source_names = {str(c.get("source_name", "")) for c in source_candidates if isinstance(c, dict)}
        valid_actions = {"keep_curated", "promote_candidate", "monitor", "ignore"}
        valid_categories = {"traffic_access", "events_venues", "incidents_safety", "neighborhood_demand_proxy", "civic_campus", "tourism_hospitality"}
        raw_items = payload.get("items") or []
        results: list[ExternalSourceGovernanceItem] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            source_name = item.get("source_name")
            if not source_name:
                continue
            # Guardrail: reject source names not in the input
            if str(source_name) not in valid_source_names:
                continue
            try:
                priority_score = float(item["priority_score"]) if item.get("priority_score") is not None else None
            except (TypeError, ValueError):
                priority_score = None
            if priority_score is not None:
                priority_score = max(0.0, min(1.0, priority_score))
            notes = item.get("notes") or []
            confidence_value = item.get("confidence")
            confidence: str | None
            if isinstance(confidence_value, (int, float)):
                confidence_num = float(confidence_value)
                if confidence_num >= 0.85:
                    confidence = "high"
                elif confidence_num >= 0.55:
                    confidence = "medium"
                else:
                    confidence = "low"
            else:
                confidence = str(confidence_value) if confidence_value else None
            note_list = [str(note) for note in notes if isinstance(note, str)] if isinstance(notes, list) else [str(notes)]
            # Guardrail: clamp action and category to allowed values
            raw_action = str(item.get("recommended_action", "")) if item.get("recommended_action") else None
            raw_category = str(item.get("recommended_category", "")) if item.get("recommended_category") else None
            results.append(
                ExternalSourceGovernanceItem(
                    source_name=str(source_name),
                    recommended_category=raw_category if raw_category in valid_categories else None,
                    recommended_action=raw_action if raw_action in valid_actions else None,
                    priority_score=priority_score,
                    confidence=confidence,
                    cadence_hint=str(item["cadence_hint"]) if item.get("cadence_hint") else None,
                    notes=note_list,
                )
            )
        return results

    def location_profiling(
        self,
        *,
        address: str,
        city: str | None,
        neighborhood_type: str | None,
        lat: float | None,
        lon: float | None,
    ) -> LocationProfilingResult | None:
        location_desc = address
        if city:
            location_desc += f", {city}"
        if lat is not None and lon is not None:
            location_desc += f" (lat={lat:.4f}, lon={lon:.4f})"

        payload = self._chat_json(
            system_prompt=(
                "Restaurant location analyst. Return JSON only. "
                "Schema: {\"transit_relevance\": bool, \"venue_relevance\": bool, "
                "\"hotel_travel_relevance\": bool, \"commuter_intensity\": 0-1, "
                "\"residential_intensity\": 0-1, \"patio_weather_sensitivity\": 0-2, "
                "\"weather_sensitivity_hint\": 0.7-1.5, "
                "\"demand_volatility_hint\": 0.8-1.3, "
                "\"nearby_entities\": [{\"name\": str, \"type\": str, "
                "\"distance_hint\": str, \"demand_category\": str, \"impact_note\": str}], "
                "\"reasoning\": str}. "
                "Estimate how weather-sensitive the dinner business is for this location, and how volatile day-to-day demand is before local actuals exist. "
                "Include 3-8 real nearby entities affecting dinner demand."
            ),
            user_prompt=f"Restaurant location: {location_desc}\nDeclared neighborhood type: {neighborhood_type or 'unknown'}",
            max_output_tokens=1600,
            timeout_seconds=self.enrichment_timeout_seconds,
        )
        if payload is None:
            return None

        def _bool(val: Any) -> bool:
            if isinstance(val, bool):
                return val
            return str(val).lower() in {"true", "1", "yes"}

        def _float_bounded(val: Any, lo: float, hi: float) -> float | None:
            if val is None:
                return None
            try:
                return max(lo, min(hi, float(val)))
            except (TypeError, ValueError):
                return None

        entities: list[dict[str, Any]] = []
        for raw_entity in payload.get("nearby_entities") or []:
            if not isinstance(raw_entity, dict):
                continue
            name = raw_entity.get("name")
            if not name:
                continue
            entities.append({
                "name": str(name),
                "type": str(raw_entity.get("type", "unknown")),
                "distance_hint": str(raw_entity.get("distance_hint", "unknown")),
                "demand_category": str(raw_entity.get("demand_category", "neighborhood_demand_proxy")),
                "impact_note": str(raw_entity.get("impact_note", "")),
            })

        return LocationProfilingResult(
            transit_relevance=_bool(payload.get("transit_relevance")),
            venue_relevance=_bool(payload.get("venue_relevance")),
            hotel_travel_relevance=_bool(payload.get("hotel_travel_relevance")),
            commuter_intensity=_float_bounded(payload.get("commuter_intensity"), 0.0, 1.0),
            residential_intensity=_float_bounded(payload.get("residential_intensity"), 0.0, 1.0),
            patio_sensitivity_hint=_float_bounded(payload.get("patio_weather_sensitivity"), 0.0, 2.0),
            weather_sensitivity_hint=_float_bounded(payload.get("weather_sensitivity_hint"), 0.7, 1.5),
            demand_volatility_hint=_float_bounded(payload.get("demand_volatility_hint"), 0.8, 1.3),
            nearby_entities=entities,
            reasoning=str(payload["reasoning"]) if payload.get("reasoning") else None,
        )
