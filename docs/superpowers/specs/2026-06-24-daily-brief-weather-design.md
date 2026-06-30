# Daily Brief Weather Design

## Goal

Add weather as an optional, user-facing context input to the daily brief so it sharpens the existing `practical_briefing` segment without creating a standalone weather segment or making brief generation dependent on a weather API.

## Scope

In scope:
- weather as context for `practical_briefing` only
- per-user weather location derived from existing profile data
- cached weather coordinates on the user record
- Open-Meteo forecast and geocoding integration behind a provider adapter
- graceful degradation when weather is missing or the provider fails

Out of scope for this slice:
- a new dedicated weather audio segment
- weather in `reflective_grounding` or `meditative_close`
- new dashboard or onboarding UI for weather setup
- background music fade-in/fade-out tuning
- multi-provider weather switching UI

## Product Decision

Weather should feel woven into the practical orientation of the day, not bolted on as a forecast readout.

Phase 1 behavior:
- Memnon uses weather to shape the tone and practical framing of `practical_briefing`
- Memnon does not add a new `weather_today` segment
- Memnon does not speak weather if the source data is absent, unreliable, or too weak to add value

The target output is not “high 78, low 52” by itself. The target is a short orientation such as:
- afternoon storms mean transitions may feel heavier later in the day
- unusual heat means energy may drop faster than usual
- a clear cool morning supports an earlier start or outdoor window

## Setup Path

Phase 1 must not require a new setup step.

Weather anchor rules:
- use the existing `school_name` profile field as the default location anchor
- include existing school-context fields already stored in user data when helpful for geocoding disambiguation, especially `school_state`
- cache the resolved weather fields on the user record after a successful lookup
- if `school_name` is empty or geocoding is not reliable enough, skip weather for that user and continue generating the brief

New user-record fields:
- `weather_location_label`: human-readable resolved location used for forecast lookups
- `weather_latitude`
- `weather_longitude`
- `weather_timezone`
- `weather_geocoded_from`: source string used to produce the cached location
- `weather_location_updated_at`

These fields are internal data fields, not new Phase 1 UI fields.

## Architecture

Use a small weather adapter inside the Cloud Functions daily-feed path.

Components:
- profile-to-location resolver
  - derives a geocoding query from existing user profile fields
  - decides whether cached weather coordinates can be reused
- weather provider adapter
  - wraps Open-Meteo geocoding and forecast calls
  - isolates provider-specific URLs, params, and response parsing
- daily brief weather summarizer
  - converts raw forecast data into a compact orientation summary for prompt input
- prompt integration
  - adds weather summary into the context for `practical_briefing`
  - leaves the segment schema unchanged

The provider adapter should be thin and replaceable so later migration to a licensed endpoint or another provider is a config-level change instead of a prompt/runtime refactor.

## Data Flow

1. `/_build_daily_feed_episode()` loads the user record and recent notes as it does now.
2. Before prompt construction, Memnon attempts to load weather context.
3. Weather context load sequence:
   - if cached `weather_latitude` and `weather_longitude` exist and still match the current `school_name` anchor, use them
   - otherwise geocode the school anchor via Open-Meteo geocoding and cache the result on success
   - request the same-day forecast from Open-Meteo forecast API using cached or newly resolved coordinates
4. Summarize the forecast into a compact internal weather context.
5. Inject that context into the daily brief prompt as supporting information for `practical_briefing`.
6. Generate the episode as usual.
7. If any weather step fails, continue with normal brief generation and record that weather enrichment was skipped.

## Forecast Shape

Phase 1 weather context should be based on standard Open-Meteo forecast fields that support practical orientation:
- current or near-current apparent conditions when available
- daily max temperature
- daily min temperature
- daily precipitation probability max
- precipitation sum or a closely related rain signal when helpful
- weather code / condition classification for simple day-type labeling

The summarizer should turn raw forecast data into a small structure such as:
- `day_type`: clear | hot | rainy | stormy | cold | mixed
- `temperature_summary`
- `precipitation_summary`
- `orientation_cue`

The cue should remain advisory and grounded, not lifestyle-guru copy.

## Prompt Design

Prompt behavior changes:
- retain the current five-segment schema
- keep `calendar_today` reserved for true calendar context
- add a weather context block only when weather data is available and coherent
- explicitly instruct the model to use weather only when it helps orient the day practically
- explicitly prevent the model from turning weather into a standalone mini-forecast unless it materially clarifies the day

If weather context is absent:
- the prompt should behave exactly as it does now

## Error Handling

Weather is a non-blocking enrichment, not a dependency.

Required failure behavior:
- geocoding failure: skip weather
- forecast HTTP failure: skip weather
- timeout: skip weather
- malformed provider payload: skip weather
- ambiguous location result: skip weather unless the top geocoding result includes a usable timezone and the resolved name clearly matches the requested school anchor string

Required system behavior:
- daily brief generation must still succeed without weather
- weather failures must not suppress audio generation
- logs should make it clear whether weather was used, skipped, or failed

## Testing

Add regression coverage for:
- weather skipped when no school anchor exists
- weather context loaded from cached coordinates
- geocoding path when cache is absent
- forecast parsing into orientation summary
- prompt construction with and without weather
- graceful degradation when geocoding or forecast calls fail

Testing should stay mostly at helper-level and route-free unless a route is added later for manual regeneration/debugging.

## Open-Meteo Fit

Phase 1 uses Open-Meteo because it is simple to integrate and does not require key management for early rollout. The relevant Open-Meteo docs confirm:
- forecast API supports local timezone handling and standard daily forecast fields
- geocoding API resolves place names to coordinates and timezone

Commercial caveat:
- the free Open-Meteo endpoint is acceptable for evaluation/prototyping
- the integration must stay provider-isolated so Memnon can move to a licensed endpoint or another provider later without redesigning the brief system

Reference docs:
- https://open-meteo.com/en/docs
- https://open-meteo.com/en/docs/geocoding-api
- https://open-meteo.com/en/pricing

## Follow-Up Work

Keep these out of the weather implementation plan unless explicitly pulled in later:
- background music fade-in/fade-out tuning for the daily brief mix
- user-editable weather override UI
- richer weather caching or retry infrastructure
- historical weather-aware prompt tuning
