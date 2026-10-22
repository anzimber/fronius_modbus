# Load calculation reliability

## Problem and scope

The `load` sensor could alternate between plausible readings, `unknown`, cached
readings and false `0 W` while the inverter continued to operate normally.
This change fixes host-side sampling order and validation, removes fabricated
zeroes and old-value substitution, and makes rejected balances diagnosable.
It cannot guarantee synchronized acquisition inside the inverter firmware.

Affected code is `custom_components/fronius_modbus/hub.py`
(`FroniusCoordinator._async_update_data`, `Hub._async_refresh_optional_data`,
`Hub._apply_modbus_load_data`) and `froniusmodbusclient.py`
(`read_inverter_data`, `read_meter_data`, `_calculate_load_power`,
`start_load_poll_cycle`, `get_load_sample_windows`). Entity identifiers,
configuration migrations and physical write operations are unchanged.

The existing integer + scale-factor SunSpec profile is required. This is not
support for the different float-profile register layout. Supported inverter
families remain Primo GEN24, Symo GEN24 and Verto. Hardware observations below
are from one Symo GEN24 installation; other families are covered by the existing
SunSpec interpretation and unit tests, not new hardware certification.

## Repository baseline

The PR targets `anzimber/fronius_modbus:main` at
`3b543de438a3057cd71b2d717fbdbaba3d9ecee3` (0.3.1). Its existing
`fixes/combined_multiple_fixes` branch at
`e872a60ed532db89758a311642dfda1ba1156736` is an ancestor of `main`.
At investigation time, upstream `callifo/fronius_modbus:main` was
`81a631bffc99c913af68b68a488ea94951551454` (0.3.3), 21 commits ahead
of the fork and zero commits behind. Upstream still contained the same load
poll-order, clamp, heuristic cache and charge-threshold behavior.
The previous load fixes were also reviewed: `7f84d10` (load stabilization),
`f58ab25` (skew/glitch/cache tracking) and `5c5a623` (charging exception).
Their existing protections explain the current behavior but do not address
adjacent polling, request duration or universal rejection of negative grid balances.
The installed HA component included newer upstream changes and a separate
experimental diagnostic patch; it is not byte-identical to the PR base.
This PR deliberately does not bundle an upstream merge or alter existing branches.

## Evidence: proven behavior versus hypotheses

On 2026-10-22, fresh HA logs from the experimental installed component showed
negative balances at only 9–14 ms completion skew. For example:

- Inverter 3227.5 W, meter -4160 W: -932.5 W, skew 0.009 s.
- Inverter 8442 W, meter -9070 W: -628 W, skew 0.012 s.

These messages also prove the experimental code had been loaded by that point.
They disprove the claim that increasing the 500-ms limit alone resolves the
reported problem. They do not identify which device measurement was stale.

Read-only direct Modbus observations confirmed signed W and scale-factor
interpretation. An initial five adjacent pairs produced 196.1–205.1 W of load,
with 1.5–3.7 ms per response. At approximately the same time, the inverter's
Solar API reported `Meter_Location=grid`, AC power 2938.90 W, grid -2732.30 W
and signed `P_Load=-203.58 W`. Separate API calls are not an atomic reference
measurement, but the close agreement supports the formula and demonstrates
that a roughly 200-W load can be real. A subsequent 40-pair observation over
about 20 seconds produced 102.0–225.3 W, no negative balances and a maximum
individual response duration of 5.15 ms. This short quiet interval does not
contradict the earlier failures during other operating conditions.

Three later read-only coordinator polls using this implementation produced
252.4, 331.9 and 331.9 W. Complete polls took approximately 122, 13 and 24 ms;
the first included connection setup. No physical write was made. The running
HA integration was not replaced or restarted as part of this PR.

The confirmed software defects are:

1. Status, settings, controls and possibly secondary meters separated the
   inverter and primary-meter reads. Their latency could exhaust the skew budget.
2. Completion timestamps described host response completion, not device sampling
   time, and did not bound the duration of a slow/retried request.
3. `max(candidate_load, 0)` concealed negative balances as valid zeroes.
4. The one-poll glitch fallback could reuse an arbitrarily old cached value:
   intervening rejected polls reset the counter but did not expire the cache.
5. Signed SunSpec power/scale-factor not-implemented values were scaled without
   checking their sentinel. For example, scale factor -32768 underflows to zero.

Device-internal refresh timing, rapid PV changes, meter forwarding latency,
unaccounted AC generation and meter installation errors remain possible reasons
for inconsistent device values. This investigation does not establish any one
of them as the physical root cause. Short host skew does not establish short
device measurement skew. The register interfaces used here expose no acquisition
timestamp that can settle that question. Do not add battery DC power or increase
the allowed skew to make a negative balance appear valid.

## Register and physical interpretation

Addresses below are zero-based Modbus protocol addresses; numbered registers in
manuals are one greater. Existing layouts are retained:

| Source | Model | W address | W_SF address | Interpretation |
| --- | --- | --- | --- | --- |
| Inverter | 101/102/103 | 40083 | 40084 | signed total AC output, W × 10^W_SF |
| Meter | 201/202/203 | 40087 | 40091 | signed real power sum, W × 10^W_SF |

Both W and W_SF are decoded as signed 16-bit values. `_calculate_load_power`
rejects W=-32768 and scale factors outside SunSpec's -10..10 range, including
its -32768 sentinel, before scaling. The existing ±50-kW validation and
2-decimal precision remain. This intentionally also makes the corresponding
AC/meter power entity unknown when those registers are invalid. Other register
families and battery write scaling are unchanged.

For primary meter location 0 (grid), positive meter power means import and
negative means export. The balance is:

`load = signed inverter total AC output + signed grid import`

Total AC output is the power crossing the inverter's AC connection; it is not
PV DC input. A DC-coupled battery contributes through that same inverter, so
adding battery discharge or subtracting battery charge again double-counts it.
Examples in `test_power_flows` cover PV charging, battery discharge and negative
AC output during charging from the grid. The previous >=1000-W charge exception
had no general physical basis for accepting other negative results and is removed.
Negative grid balances now receive the same conservative treatment in every
battery mode and model. Model-specific telemetry anomalies remain unknown rather
than being corrected with an unverified formula.

For location 1 (load) and 256–511 (subload), consumption is `-meter_power` and
requires only a fresh valid meter response. Signed negative net consumption is
preserved because generation on that load path is explicitly possible. A subload
reading represents the measured branch, not necessarily the whole house; this
preserves the existing integration behavior. Location 3 (external generator),
missing locations and unsupported locations are not household-load sources.
They stay unknown. No secondary meter is silently substituted for a missing
primary, and this change does not sum arbitrary external generators.

The physical scope of the grid formula assumes the inverter accounts for all
site generation. A second AC generator/storage system can invalidate that
assumption even when the residual is positive. No software timing check can
detect every such topology. Confirm wiring and all generation sources before
interpreting the result as total household consumption.

References:

- [Fronius GEN24 Modbus manual: models, meter locations and sign conventions](https://manuals.fronius.com/html/4204102649/en-US.html)
- [SunSpec model 103: inverter point layout](https://github.com/sunspec/models/blob/master/json/model_103.json)
- [SunSpec model 203: meter point layout](https://github.com/sunspec/models/blob/master/json/model_203.json)
- [Fronius official interface and register-map downloads](https://www.fronius.com/en/help-center/solar-energy/products/monitoring-control/solutions/open-interfaces/modbus-tcp)

## New data flow and validation

1. Reset successful sample metadata and load at the start of each coordinator cycle.
2. Read inverter core registers; retain existing core-failure/availability handling.
3. Read the configured primary meter first, immediately after the inverter.
4. Validate and calculate load immediately, before secondary meters and optional
   status/settings/controls/MPPT/storage/Web API work can delay the pair.
5. Continue all existing optional reads, independently of load validity.

Each request start is recorded before awaiting the transport, so connection,
transaction-lock waits and retries count. A completion timestamp is recorded
only after successful decoding. Each read invalidates its old completion
metadata first, including retries within the same cycle. Values must be finite
real numbers, not booleans, strings, NaN or infinity. Request intervals must be
ordered, completed and at most 500 ms old from their start when load is evaluated.
Together these checks bound the entire adjacent acquisition window to 500 ms,
not just the difference between completion times. The original numerical limit
is retained rather than widened. Direct load meters do not need inverter timing.

For a grid meter, every negative result, including -0.01 W, stays unknown.
Exactly zero remains valid when obtained from valid readings. No heuristic
storage exception or prior-load fallback remains. A new successful pair recovers
immediately. Rejection warnings include the reason and relevant values and are
limited to one per hub per five minutes; intervening details and successful grid
balances are logged at debug level. There is no promise that this removes every
`unknown`: rejecting untrustworthy values is intentional.

With a successful coordinator poll, `load=None` maps to HA `unknown`, while
other entities remain usable. A failed core poll outside the existing battery
write recovery window raises `UpdateFailed`, making coordinator entities
`unavailable`. That recovery-window behavior remains unchanged. Comments in the
old fallback called a missing value unavailable, although it was actually unknown.

## Modbus reads, writes and Predbat

The observed older write traces included function 0x90 exception code 3
(illegal data value returned by the inverter) and a separate request cancelled
outside pymodbus. The former is not evidence of a read/write collision. The
latter is consistent with cancellation/connection interruption; the trace alone
does not prove its cause or relate it to the later negative load balances.
Pymodbus 3.11.2 serializes transactions with an asyncio lock, but that lock does
not make separate inverter and meter values an atomic device snapshot.

This change does not add a lock around long coordinator polls or alter mode,
rate, reserve, Web API or transport write methods. Reads and writes may still
interleave; a wait during the measurement pair causes the duration guard to
reject load rather than block writes for a whole polling cycle. No new requests
or retries are added. There are no physical battery write/readback tests.
53 write/control/recovery methods were compared by Python AST with fork `main`
and were identical. Mocked write tests verify signed charge/discharge-rate
register payloads without contacting hardware. Newer upstream serialization and
mode resynchronization changes are separate from this patch and should not be
removed when installing it on a newer system.

## Tests and limitations

Run with Python 3.14 (matching the investigated HA runtime):

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-test.txt
python -m pytest -q tests
python -m compileall -q custom_components/fronius_modbus tests
git diff --check
```

`tests/test_load.py` covers PV self-consumption, high export, import, charging,
discharging, exact zero, the reported 30-W and -171-W examples, small/large skew,
long responses with close completion times, stale/missing/invalid samples,
SunSpec sentinels and negative scale factors, invalid locations, signed direct
load/subload readings, recovery, warning throttling, primary-first ordering,
secondary failure isolation, primary timeouts, core-failure recovery windows,
and simulated signed Predbat rate writes. The actual production coordinator,
hub and register decoder are loaded. Only HA framework boundaries and test
transport responses are substituted; real pymodbus data types are used.

At implementation verification: **79 tests passed** on the fork base and **79 tests passed** after a clean
three-way application of the production diff to upstream 0.3.3 (`81a631b`).
The newer upstream write/control/recovery methods were also unchanged in that
compatibility checkout. Direct context-only patch application needed three-way
merging because the constructor context changed upstream; no manual conflict
resolution or upstream reset was needed. Syntax compilation and
`git diff --check` passed. The base repository provided HACS/hassfest validation
workflows but no pre-existing Python test suite. A new GitHub Actions workflow
runs these tests and compilation. This lightweight suite does not run a complete
HA instance, config-entry lifecycle, HACS or hassfest locally. The short direct
read-only probes are not a long-term deployment test. Hardware validation of
battery charging/discharging transitions and Verto/Primo variants remains open.
The installed component and active Predbat control have not been changed.

### GitHub Actions results for PR #2

The new [unit-test and syntax job](https://github.com/anzimber/fronius_modbus/actions/runs/39765862737)
passed. The inherited validation workflows also ran and exposed unrelated failures:

- [hassfest](https://github.com/anzimber/fronius_modbus/actions/runs/39765862844):
  existing uppercase translation keys such as `A` and `Conn` in `translations/en.json`
  violate the current translation-key schema; an existing `config.title` warning
  was also reported. Translation files are byte-identical to the PR base.
- [HACS](https://github.com/anzimber/fronius_modbus/actions/runs/39765862807):
  the fork has no valid repository topics; 1 of 10 checks failed. This is repository
  metadata outside the code diff. The manifest, HACS config and inherited workflow
  files are also unchanged. A missing local brand icon produced a warning and
  fallback, not the failing check.

These failures are recorded rather than suppressed. Fixing translation keys can
require coordinated entity/migration work; repository topics require a separate
metadata change. Neither is part of the load-calculation fix.

## Installation, verification and rollback

1. Back up the **entire currently installed** `custom_components/fronius_modbus`
   directory and record its version/commit before making changes.
2. Review the PR diff. On the PR's 0.3.1 base, install the resulting full component
   folder using the normal custom-component process. **Do not copy this older
   branch wholesale over an installation already using newer upstream fixes.**
   For such installations, apply only this PR's `hub.py` and
   `froniusmodbusclient.py` diff to a checkout of that exact installed version,
   resolve conflicts, and repeat the tests. Retain newer battery-control fixes.
3. Run `ha core check` on the actual target installation, then restart HA (or
   reload the integration if supported) so that the new Python code is loaded.
4. Verify unchanged entity IDs, PV/grid/AC data and normal Predbat scheduling.
   No forced physical battery write is required for verification. Observe naturally
   occurring charging/discharging and PV changes over multiple polling periods.
5. When load is unknown, inspect the rate-limited diagnostic reason. Enable debug
   logging for `custom_components.fronius_modbus.hub` temporarily if raw balance
   details are needed. Compare with the inverter's own reported power flow while
   allowing for the different device/API refresh rates. A matching-looking single
   sample is not proof of long-term synchronization.
6. To roll back, restore the complete backed-up component version, run
   `ha core check`, restart HA, and verify entities and control operation again.
   No config-entry migration or entity recreation is introduced by this change.
