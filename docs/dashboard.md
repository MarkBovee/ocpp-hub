# Dashboard for charge sessions

Replace `charger1` with your charger id (the slug in your entity ids, for example `sensor.ocpp_hub_charger1_power`). Paste the cards into a dashboard in the raw configuration editor.

```yaml
type: vertical-stack
cards:
  - type: entities
    title: Charging now
    entities:
      - entity: sensor.ocpp_hub_charger1_status
      - entity: sensor.ocpp_hub_charger1_power
      - entity: sensor.ocpp_hub_charger1_session_energy
      - entity: sensor.ocpp_hub_charger1_session_duration
      - entity: sensor.ocpp_hub_charger1_session_started
  - type: history-graph
    title: Power and current
    hours_to_show: 6
    entities:
      - entity: sensor.ocpp_hub_charger1_power
      - entity: sensor.ocpp_hub_charger1_current_l1
      - entity: sensor.ocpp_hub_charger1_current_l2
      - entity: sensor.ocpp_hub_charger1_current_l3
  - type: markdown
    title: Recent charges
    content: |-
      | Started | kWh | Minutes | Peak kW | Stopped by |
      |---|---|---|---|---|
      {% for s in state_attr('sensor.ocpp_hub_charger1_last_session', 'recent_sessions') or [] -%}
      | {{ as_timestamp(s.started_at) | timestamp_custom('%d-%m %H:%M') }} | {{ s.energy_kwh }} | {{ s.duration_s // 60 }} | {{ ((s.max_power_w or 0) / 1000) | round(1) }} | {{ s.stop_reason or '-' }} |
      {% endfor %}
```

The table lists the 20 newest charges. The whole history is available from the service `ocpp_hub.get_sessions` (Developer tools, Actions), which returns every stored charge, optionally for one charger and limited to the newest ones.
