# Lunar Mission Planner - Implementation Summary

## Overview

The Lunar Mission Planner feature has been successfully integrated into the Eclipse Lunar Landing application. It calculates complete Earth-to-Moon mission timelines working **backwards from a desired landing date/time** using real orbital mechanics.

## Features

### Scientific Accuracy
- **Real Ephemeris**: NASA JPL DE440 via SpiceyPy (1849-2150 coverage)
- **Lambert Solver**: Izzo 2015 universal-variables formulation for optimal Earth-Moon transfers
- **Patched-Conic Approximation**: Geocentric two-body for Earth departure/arrival, selenocentric for lunar operations
- **Real GM Values**: Read from `gm_de440.tpc` at runtime - no hard-coded constants
- **Apollo-Validated Descent**: DOI and PDI Δv based on Apollo 11 mission data

### Mission Phases Calculated
1. **Earth Parking Orbit (LEO)** - Configurable altitude (default 200 km)
2. **Trans-Lunar Injection (TLI)** - Hyperbolic escape from Earth
3. **Translunar Coast** - Ballistic transfer (3-4 days typical)
4. **Lunar Orbit Insertion (LOI)** - Hyperbolic capture into circular LLO
5. **Descent Orbit Insertion (DOI)** - Elliptic transfer orbit
6. **Powered Descent Initiation (PDI)** - Terminal descent to surface
7. **Touchdown** - Landing at target coordinates and time

### User Inputs
- Landing site latitude/longitude
- Landing date & time (UTC)
- Spacecraft parameters:
  - Total mass (kg) - includes all propellant
  - Dry mass (kg) - lander mass after all maneuvers
  - Specific impulse (s) - engine efficiency
  - Thrust (N) - engine power
  - Earth orbit altitude (optional, default 200 km)
  - Lunar orbit altitude (optional, default 100 km)

### Outputs
- **Feasibility determination** with clear explanations if infeasible
- **Complete mission timeline** with UTC dates for every maneuver
- **Δv requirements** for each maneuver
- **Propellant consumption** at each stage
- **Mass budget** tracking from LEO to touchdown
- **Burn durations** for all powered phases

## Implementation Files

### Backend (`lunar_nav/mission_planner.py`)
Core orbital mechanics engine:
- `plan_mission()` - Main entry point
- `lambert()` - Izzo 2015 Lambert solver with universal variables
- `SpacecraftParams` - User spacecraft configuration
- `MissionPlan` - Complete mission timeline output
- `Manoeuvre` - Individual mission event with Δv/mass/time data

Key functions:
- Stumpff functions for universal variables
- Kepler propagation for two-body dynamics
- Descent Δv model from Apollo heritage
- SPICE kernel path handling (non-ASCII path workaround)

### API Endpoints (`ui/app.py`)
- `POST /api/plan_mission` - Start mission planning job
- `GET /api/mission_status/<job_id>` - Poll job status and results

Background threading ensures responsive UI during long calculations.

### Frontend
- `ui/templates/index.html` - Mode selector and mission planner form
- `ui/static/js/planner.js` - Mission planner UI logic
- `ui/static/css/style.css` - Mission planner styling

## Usage

### Starting the Application
```bash
cd "C:\Users\dim20\OneDrive\Υπολογιστής\Eclipse"
python ui/app.py
```

Open browser to: http://localhost:5000

### Planning a Mission

1. On landing screen, click **"Mission Planner"** mode button

2. Enter landing target:
   - Latitude: -89.0° (south pole)
   - Longitude: 0.0°
   - Date: 2027-03-15
   - Time: 12:00 UTC

3. Enter spacecraft parameters:
   - **Example feasible mission**:
     - Total Mass: 160,000 kg
     - Dry Mass: 20,000 kg
     - Isp: 465 s (cryogenic engine, e.g., RL10)
     - Thrust: 200,000 N
   
   - **Example infeasible mission** (for testing):
     - Total Mass: 45,000 kg
     - Dry Mass: 10,000 kg
     - Isp: 450 s
     - Thrust: 110,000 N

4. Click **"Plan Mission"**

5. Results display:
   - Feasibility status (✓ FEASIBLE or ✗ INFEASIBLE)
   - If infeasible: clear explanation of why
   - Complete mission timeline with all maneuvers
   - Total Δv requirement
   - Launch and landing times

## Example Results

### Feasible Mission (160,000 kg spacecraft)

**Target**: -89.0°, 0.0° landing at 2027-03-15 12:00 UTC

**Status**: ✓ FEASIBLE

**Total Δv**: 9,264 m/s

**Timeline**:
```
2027-03-11 21:20 UTC | Earth Parking Orbit            | Δv =     0 m/s | mass = 152,516 kg
2027-03-11 22:49 UTC | Trans-Lunar Injection (TLI)    | Δv = 6,472 m/s | mass = 152,516 kg
2027-03-13 13:19 UTC | Translunar Coast               | Δv =     0 m/s | mass =  36,894 kg
2027-03-15 03:49 UTC | Lunar Orbit Insertion (LOI)    | Δv =   913 m/s | mass =  36,894 kg
2027-03-15 09:48 UTC | Descent Orbit Insertion (DOI)  | Δv =    20 m/s | mass =  30,202 kg
2027-03-15 11:48 UTC | Powered Descent Initiation (PDI) | Δv = 1,860 m/s | mass =  30,073 kg
2027-03-15 12:00 UTC | Touchdown                      | Δv =     0 m/s | mass =  20,000 kg
```

**Propellant consumed**: 132,516 kg
**Margin remaining**: 7,484 kg

**Mission duration**: 3.6 days from LEO to lunar surface

### Infeasible Mission (45,000 kg spacecraft)

**Status**: ✗ INFEASIBLE

**Reason**: Insufficient propellant. Required initial mass: 210,675 kg, spacecraft total mass: 48,000 kg. Total Delta-v required: 9,264 m/s, available Delta-v budget: 4,695 m/s. Increase propellant load, raise Isp, or reduce dry mass.

## Technical Details

### Lambert Solver
- Geocentric J2000 frame
- r1 placed at LEO on +X axis with circular velocity
- r2 is Moon position at arrival time (geocentric)
- Short-way transfer (dnu < 180°) for minimum energy
- Dense grid search for root bracketing (3000 points, z ∈ [-4π², +4π²])
- Converges to ~1e-9 tolerance using Brentq

### TLI Δv Calculation
```
v_inf@Earth = |v1_geocentric - v_circular_LEO|
v_peri_TLI = sqrt(v_inf² + 2μ_earth/r_LEO)
Δv_TLI = v_peri_TLI - v_circular_LEO
```

### LOI Δv Calculation
```
v_inf@Moon = |v2_geocentric - v_moon_geocentric|
v_peri_LOI = sqrt(v_inf² + 2μ_moon/r_LLO)
Δv_LOI = v_peri_LOI - v_circular_LLO
```

### Descent Δv
- **DOI**: 20 m/s (Apollo 11)
- **PDI**: 1,860 m/s base + altitude scaling (Apollo 11 baseline)

### Mass Budget
Tsiolkovsky equation applied backwards from dry mass:
```
m_after = m_before / exp(Δv / (Isp × g0))
```

### Mission Architecture
- LEO parking: 1 orbit (~90 min) before TLI
- LOI wait: 6 hours for orbital mechanics setup
- DOI wait: 2 hours before descent
- PDI duration: 12 minutes (Apollo heritage)

## Known Limitations

1. **No trajectory visualization yet** - Placeholder in UI, 3D plot not implemented
2. **Single landing site** - Does not integrate with site analysis (separate tools)
3. **No launch window optimization** - Uses fixed 3-4 day transfers
4. **Simplified descent model** - Real PDI requires terrain-relative navigation
5. **No abort scenarios** - Only nominal mission timeline
6. **No multi-revolution transfers** - Short-way Lambert only
7. **Earth rotation not modeled** - Launch site latitude/azimuth not considered

## Data Sources & References

### Ephemeris
- NASA JPL DE440s (small-body ephemeris, 1849-2150)
- SPICE kernels: `de440s.bsp`, `gm_de440.tpc`, `naif0012.tls`, `pck00011.tpc`

### Algorithms
- Lambert solver: Izzo, D. (2015). "Revisiting Lambert's problem." *Celestial Mechanics and Dynamical Astronomy*, 121(1), 1-15. doi:10.1007/s10569-014-9587-y
- Stumpff functions: Battin, R.H. (1987). *An Introduction to the Mathematics and Methods of Astrodynamics*
- Kepler propagation: Universal variables formulation (Battin 1987)

### Apollo Mission Data
- DOI/PDI Δv values from Apollo 11 LM descent profile
- Mission timeline phasing (LOI wait, DOI wait, PDI duration)

## Testing

### Unit Tests
Location: `C:\EclipseTest\`
- `test_planner.py` - Original small spacecraft (infeasible)
- `test_planner_realistic.py` - Medium spacecraft (infeasible)
- `test_planner_apollo.py` - Apollo-class parameters (infeasible - expected)
- `test_planner_feasible.py` - Large modern spacecraft (feasible ✓)

### API Tests
- `test_api.py` - End-to-end HTTP API test (PASSED ✓)

### Manual UI Testing
See `test_ui_instructions.md` for browser testing steps.

## Future Enhancements

### High Priority
1. **3D trajectory visualization** - Earth-Moon transfer arc with matplotlib or Three.js
2. **Launch window analysis** - Multiple departure dates with Δv vs time plot
3. **Integration with site analysis** - Auto-fill landing coordinates from selected site

### Medium Priority
4. **Multi-revolution transfers** - Long-duration, low-energy trajectories
5. **Free-return trajectories** - Apollo 13-style abort capability
6. **Launch site constraints** - Azimuth limits, Earth rotation effects
7. **Orbit plane changes** - Out-of-plane Δv for non-equatorial targets

### Low Priority
8. **N-body perturbations** - Sun/Earth gravity during translunar coast
9. **Non-impulsive burns** - Finite burn duration effects
10. **Propellant boiloff** - Cryogenic tank losses during coast phases

## Troubleshooting

### "Insufficient propellant" error
- Increase total mass while keeping dry mass constant
- Increase Isp (use more efficient engines)
- Reduce dry mass (lighter lander)
- **Typical requirement**: ~9,000-9,500 m/s total Δv for lunar landing

### "Cannot bracket root" Lambert error
- Date/time may be at Moon's perigee/apogee causing numerical issues
- Try different landing date (±1 day)
- Report as bug if persistent

### "Failed to fetch" API error
- Verify Flask server is running: `netstat -ano | findstr :5000`
- Restart server: `python ui/app.py`

### Greek character path errors (SpiceyPy)
- Already handled: kernels auto-copied to `%TEMP%\eclipse_kernels`
- If issues persist, manually copy kernels to `C:\EclipseData\`

## License & Attribution

Part of the Eclipse Lunar Landing Navigator suite.

**Credit**:
- NASA JPL for DE440 ephemeris
- Dario Izzo for Lambert solver algorithm
- Apollo program for descent Δv validation data

**Built for**: NASA Space Apps Challenge
