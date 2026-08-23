# Sanctuary OS v1.2 Commissioning

This is the production gate for the Sanctuary state machine. The software is
deployed with scheduled transitions disabled. Manual scenes and calibration
tests remain available while the apartment is tuned.

## 1. Normalize the Home Assistant registry

Use **Settings → Areas, labels & zones** and **Settings → Devices & services →
Entities**. Do not edit `.storage` files directly.

- Rename the display name `Dinning Room` to `Dining Area`. Keep the existing
  area identifier so automations and history remain stable.
- Assign `Office Light 1` to the Office Area.
- Standardize the visible names `Dining Area Light`, `Office Light 1`,
  `Office Light 2`, `Entry Light`, and the remaining `Room Light N` entities.
- Confirm the eleven registered Tuya lights respond from Home Assistant.
- Pair Bedroom Light 2 when it is available. Until then, Jarvis reports it as
  `not_commissioned` and does not treat that as a failure.
- Pair Sir Cleans-A-Lot separately when available. No Sanctuary lighting mode
  depends on the vacuum.

The Entry internet-power switch is protected infrastructure. Never expose or
target it as a controllable switch.

## 2. Calibrate after dark

Open **Jarvis → Wall+ → Spatial Command Center**. Select one room at a time and
use **Test calibration**. Begin at 1%, wait several minutes for dark adaptation,
then increase in one-point steps only when needed.

Record and verify:

- Bedroom Thunderstorm: 1–2% target, remains active until weekday Sunrise.
- Bathroom Light 5 night beacon: 1–3% target.
- Hallway, Dining Area, and Entry navigation: 1–3% only where required.
- Welcome: 20–50%, provisional starting point 35%.
- Office Work: provisional 40%, adjusted for comfortable task visibility.
- Bathroom Grooming: provisional 80%, adjusted for accurate visibility.

Calibration is complete only when the normal occupied position and adjacent
travel path both feel correct. A replacement bulb requires retesting.

## 3. Exercise manual modes

With scheduled automations still disabled, test Home Base, Welcome, Work,
Focus, Create, Studio, Shower, Wind Down, Thunderstorm, Date Night, Cleaning,
Guest, Cinema, Away, Vacation, and Emergency.

For every mode verify:

1. Only intended rooms change.
2. An unavailable light does not stop the remaining lights.
3. Media or vacuum absence does not block lighting.
4. Manual Hold prevents scheduled transitions and Resume restores the prior mode.
5. Emergency lighting overrides atmosphere but never calls switches, locks,
   covers, alarms, climate, or vacuum services.
6. The LifeOS Command Center records the reason and source.

## 4. Enable schedules in stages

Turn on `input_boolean.sanctuary_calibration_ready` only after every required
room level has passed an after-dark test. Then turn on
`input_boolean.sanctuary_automations_enabled` only after calibration and
manual testing pass.

1. Observe Morning, Welcome, Wind Down, and Thunderstorm for three normal days.
2. Verify late arrival, a non-workday, Guest/Date Night, active recording, and
   Manual Hold exceptions.
3. Enable real use of Away and Sunday Cleaning only after presence reliably
   reflects Giovanni's location.
4. Keep vacuum, media, climate, locks, cameras, and perimeter features visibly
   uncommissioned until their actual Home Assistant entities exist.

## 5. Recovery and rollback

- Disable scheduled behavior with `input_boolean.sanctuary_automations_enabled`.
- Select **Manual Hold** to protect an immediate manual lighting state.
- Select **Home Base** for the reversible normal-lighting baseline.
- If Home Assistant, LifeOS, internet, or one bulb is unavailable, physical and
  native-app lighting controls remain valid. Sanctuary must not fight them.
- Restore the pre-deployment Home Assistant backup if configuration validation
  or reboot recovery fails.

Production readiness requires a clean Home Assistant configuration check,
passing behavior simulations, a cold restart, internet-loss and LifeOS-loss
tests, manual override verification, and a successful restore drill.
