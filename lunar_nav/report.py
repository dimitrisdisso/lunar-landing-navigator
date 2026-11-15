"""Text report.  Every value is tagged with where it came from."""
import numpy as np

from . import config as C

LOLA = "[LOLA]"            # measured/derived directly from NASA LOLA grids
HORIZ = "[Horizons]"       # NASA JPL ephemeris
MODEL = "[MODEL]"          # published physical model driven by NASA data
REF = "[REFERENCE]"        # published NASA/mission measurement, not site-specific
NA = "[NOT AVAILABLE]"


def _f(v, fmt="{:.1f}", unit=""):
    return "n/a" if v is None or (isinstance(v, float) and not np.isfinite(v)) else fmt.format(v) + unit


def _ns(lat):
    return f"{abs(lat):.4f}\u00b0{'S' if lat < 0 else 'N'}"


def _ew(lon):
    lon = (lon + 180) % 360 - 180
    return f"{abs(lon):.4f}\u00b0{'E' if lon >= 0 else 'W'}"


def header(cfg, eph, provider):
    line = "=" * 78
    print(line)
    print(f" LUNAR LANDING NAVIGATOR  -  target date {cfg.date} 00:00 UTC, {cfg.days} day stay")
    print(f" terrain : {provider.name}")
    print(f" geometry: {eph.source}")
    print(line)


def summary(results):
    print("\n RANKING (hard NASA constraints + heuristic score)")
    print(f" {'#':<3}{'Latitude':>12}{'Longitude':>13}{'Score':>8}  {'Constraints':<12} Note")
    for i, r in enumerate(results, 1):
        ok = all(p for _, p in r["constraints"])
        print(f" {i:<3}{_ns(r['site']['lat']):>12}{_ew(r['site']['lon']):>13}{r['score']:>8.1f}  "
              f"{'ALL MET' if ok else 'FAILED':<12} "
              f"{'' if ok else ', '.join(n.split(' (')[0] for n, p in r['constraints'] if not p)}")


def site_report(rank, r, cfg, eph):
    s, k, t, e = r["site"], r["sky"], r["thermal"], r["env"]
    line = "-" * 78
    print("\n" + line)
    print(f" #{rank}  TOUCHDOWN  {_ns(s['lat'])}, {_ew(s['lon'])}     score {r['score']:.1f}/100")
    print(f"     map x,y = ({s['x']:.0f}, {s['y']:.0f}) m  (south polar stereographic, MOON_ME)")
    print(line)
    for name, ok in r["constraints"]:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print("  score parts: " + " | ".join(f"{n} {v:.1f}" for n, v in r["parts"].items()))

    print("\n 1. TERRAIN TOPOGRAPHY")
    print(f"    elevation {_f(s['elevation_m'], '{:+.0f}', ' m')} vs 1737.4 km sphere   {LOLA}")
    print(f"    shape: {s['shape_class']} (TPI {s['tpi_m']:+.0f} m over 500 m); relief within 2 km {s['relief_2km_m']:.0f} m   {LOLA}")
    print(f"    slope in {cfg.zone_radius_m:.0f} m zone: mean {s['slope_mean']:.1f} / 95th pct {s['slope_p95']:.1f} / max {s['slope_max']:.1f} deg   {LOLA} (LDSM)")
    print(f"    slopes >20 deg (no-EVA) within 2 km: {s['eva_frac_over']*100:.0f}% of area; >30 deg within 1 km: {s['steep_frac_1km']*100:.0f}%   {LOLA}")
    print(f"    nearest >30 deg slope (cliff/wall): {_f(s['d_steep_m'], '{:.0f}', ' m')}   {LOLA}")

    print(" 2. SURFACE ROUGHNESS")
    print(f"    RMS height roughness (100 m baseline): mean {_f(s['rough_m'], '{:.2f}', ' m')}, max {_f(s['rough_max_m'], '{:.2f}', ' m')}   {LOLA} (LDRM)")
    print(f"    scales below ~{s['pix_g']:.0f} m (rocks, small texture)   {NA} from LOLA - use LROC NAC imagery")

    print(" 3. CRATER & BOULDER HAZARDS")
    print(f"    resolved craters (depressions >4 m deep, >~{3*s['pix_g']:.0f} m wide): {s['n_craters_1km']} within 1 km; "
          f"nearest {_f(s['d_crater_m'], '{:.0f}', ' m')}; {s['crater_frac_zone']*100:.0f}% of landing zone   {LOLA}")
    print(f"    boulders / small craters   {NA} from LOLA; typical exposed-rock cover <1% (Hayne+2017)   {REF}")

    print(" 4. LANDING-ZONE SUITABILITY")
    print(f"    largest obstacle-free circle: radius {s['r_inscribed_m']:.0f} m (touchdown diameter ~{2*s['r_inscribed_m']:.0f} m)   {LOLA}")
    print(f"    connected usable area (slope <= {cfg.slope_max:.0f} deg, no craters): {s['safe_area_km2']:.2f} km^2; "
          f"touchdown point is {s['center_offset_m']:.0f} m from the screening centre   {LOLA}")

    print(" 5. SOLAR ILLUMINATION")
    print(f"    Sun elevation during stay: {k['sun_el_min']:+.1f} to {k['sun_el_max']:+.1f} deg   {HORIZ}+{LOLA}")
    print(f"    sunlit {k['sunlit_frac']*100:.0f}% of stay; longest continuous sunlight {k['longest_sunlit_h']:.0f} h"
          f"{'  (whole stay)' if k['continuous_whole_mission'] else ''}; sunlit {k['sunlit_frac_month']*100:.0f}% of the lunar day")
    print(f"    incidence angle on real surface while lit: mean {_f(k['mean_incidence'], '{:.0f}')} / max {_f(k['max_incidence'], '{:.0f}')} deg")
    print(f"    Sun distance {k['sun_au']:.4f} AU -> solar flux {k['sun_flux']:.0f} W/m^2   {HORIZ}")
    for kind, tm in k["events"]:
        print(f"      {kind:8s} {tm:%Y-%m-%d %H:%M} UTC")

    print(" 6. TERRAIN SHADOWING")
    hz = s["horizon"]
    az = s["az"]
    print(f"    horizon elevation (antenna {cfg.antenna_height_m:.0f} m up): min {hz.min():+.1f}, mean {hz.mean():+.1f}, max {hz.max():+.1f} deg "
          f"(highest at azimuth {az[hz.argmax()]:.0f} deg)   {LOLA}")
    print(f"    hours with Sun above flat horizon but blocked by terrain: {k['shadow_by_terrain_h']:.0f} h   {LOLA}+{HORIZ}")

    print(" 7. THERMAL ENVIRONMENT")
    if t:
        print(f"    surface T during stay: max {t['t_max_window']:.0f} K / min {t['t_min_window']:.0f} K / mean {t['t_mean_window']:.0f} K "
              f"({t['t_max_window']-273.15:.0f} to {t['t_min_window']-273.15:.0f} C)   {MODEL}")
        print(f"    largest 24 h swing {t['swing24']:.0f} K; lit/dark transitions in stay: {t['transitions']}; "
              f"full lunar day range {t['t_min_month']:.0f}-{t['t_max_month']:.0f} K   {MODEL}")
    else:
        print("    skipped (--no-thermal)")

    print(" 8. EARTH VISIBILITY & COMMUNICATIONS")
    print(f"    Earth elevation {k['earth_el_min']:+.1f} to {k['earth_el_max']:+.1f} deg (flat); above LOLA horizon {k['earth_vis_frac']*100:.0f}% of stay; "
          f"longest outage {k['longest_outage_h']:.0f} h   {HORIZ}+{LOLA}")
    print(f"    Sun AND Earth both available {k['both_frac']*100:.0f}% of stay")

    print(" 9. SURFACE & SOIL PROPERTIES  (no site-specific data exists; reference values)")
    so = e["soil"]
    print(f"    bulk density {so['density_surface']:.2f} g/cm3 at surface, {so['density_10cm']:.2f} at 10 cm, "
          f"{so['density_100cm']:.2f} at 1 m, ->{so['density_deep']:.2f} deep   {REF} (Lunar Sourcebook)")
    print(f"    cohesion {so['cohesion_surface_kpa'][0]}-{so['cohesion_surface_kpa'][1]} kPa at surface, >{so['cohesion_deep_kpa']:.0f} kPa below 0.5 m; "
          f"friction angle {so['friction_deg'][0]:.0f}-{so['friction_deg'][1]:.0f} deg   {REF}")
    print(f"    regolith thickness typically {so['thickness_m'][0]:.0f}-{so['thickness_m'][1]:.0f} m (highlands)   {REF}")
    print(f"    bearing capacity {so['bearing_kpa'][0]:.0f}-{so['bearing_kpa'][1]:.0f} kPa vs applied {so['applied_kpa']:.1f} kPa "
          f"(safety factor {so['safety_factor'][0]:.1f}-{so['safety_factor'][1]:.1f}) for "
          f"{cfg.lander_mass_kg:.0f} kg on {cfg.n_footpads} x {cfg.footpad_diameter_m} m pads   {MODEL}")
    print("    polar caution: ice-cemented regolith near cold traps may be stiffer; unmeasured here")

    print("10. GEOLOGICAL STABILITY")
    print(f"    slope-failure potential: >30 deg ground (lower bound of Apollo friction angle) {s['steep_frac_1km']*100:.0f}% within 1 km, "
          f"nearest {_f(s['d_steep_m'], '{:.0f}', ' m')}   {LOLA}")
    print(f"    fractures / voids / lava tubes / moonquake faults   {NA} - needs GRAIL gravity, Mini-RF radar, LROC tectonic maps")

    print("11. RADIATION & SPACE ENVIRONMENT")
    rd = e["rad"]
    print(f"    galactic cosmic rays: ~{rd['gcr_usv_day']:.0f} uSv/day dose equivalent (~{rd['gcr_usv_hour']:.0f} uSv/h) => "
          f"~{rd['mission_msv']:.1f} mSv over the stay (ISS: {rd['iss_usv_day']:.0f} uSv/day)   {REF} (Chang'e-4 LND, solar minimum)")
    sep = rd["sep"]
    if sep:
        if sep["future"]:
            print(f"    NASA DONKI (historical baseline, SEPs cannot be forecast): "
                  f"{sep['n']} solar energetic particle events in the year ending {sep['end']:%Y-%m-%d} "
                  f"(latest recorded: {sep['latest'] or 'none'})   [DONKI]")
        else:
            print(f"    NASA DONKI: {sep['n']} solar energetic particle events in the 365 days "
                  f"up to {sep['end']:%Y-%m-%d} (latest: {sep['latest'] or 'none'})   [DONKI]")
    else:
        print(f"    solar particle events   {NA} (DONKI not reached); unshielded surface is exposed")
    print("    micrometeoroids (Gruen 1985, unshielded flat plate, ~20 km/s mean)   [MODEL]; impacts per m^2:")
    for lab, d1, dm in e["mmod"]:
        print(f"      mass >= {lab:<22} {d1:9.2e} /day   {dm:9.2e} over the stay")
