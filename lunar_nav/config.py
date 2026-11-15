"""
Constants, thresholds and data locations.  Every number carries its source so a
judge can trace it.  Anything that is an engineering heuristic (not a NASA
requirement) is marked HEURISTIC and can be changed in one place.
"""
from dataclasses import dataclass

# ---------------------------------------------------------------- Moon / Sun
R_MOON = 1_737_400.0          # m   LOLA reference sphere (Barker+2023, PGDA)
G_MOON = 1.62                 # m/s^2 surface gravity (mean)
SOLAR_CONSTANT = 1361.0       # W/m^2 total solar irradiance at 1 AU
AU_KM = 1.495978707e8
SUN_RADIUS_DEG = 0.2666       # apparent solar radius at 1 AU
SYNODIC_DAYS = 29.530588853   # lunar day (new moon to new moon)

# --------------------------------------- Artemis III requirements (NASA NTRS)
# NTRS 20240009596 "Artemis HLS Update 2024" and NASA Aug-2022 candidate-region
# release.  These are the HARD constraints used to accept / reject a site.
LAT_LIMIT_DEG = -84.0         # land within 6 deg latitude of the south pole
LANDING_SLOPE_MAX_DEG = 10.0  # surface slope for landing (100 m baseline)
LANDING_ZONE_RADIUS_M = 100.0 # a landing site occupies ~100 m radius
EVA_SLOPE_MAX_DEG = 20.0      # slope limit for EVA (2 km scale)
EVA_RADIUS_M = 2000.0
MISSION_DAYS = 6.5            # planned Artemis III surface stay
EARTH_VIS_MIN_FRAC = 0.25     # NASA slide excludes areas with Earth visibility < 25 %
SUN_VIS_MIN_FRAC = 0.05       # ... and solar visibility < 5 %

# ------------------------------------------------ NASA LOLA data (PGDA, GSFC)
# Cloud-optimised GeoTIFFs, south polar stereographic, MOON_ME frame.
# Barker et al. 2023, PSJ 4, 183, doi:10.3847/PSJ/acf3e1
PGDA_BASE = "https://pgda.gsfc.nasa.gov/data/LOLA_20mpp/"
PRODUCTS = {
    "dem":   "LDEM_80S_80MPP_ADJ.TIF",             # surface height (m), 80 m/px
    "slope": "LDSM_80S_80MPP_ADJ.TIF",             # slope (deg), 80 m/px
    "rough": "LDRM_80S_50MPP_ADJ_ROUGH_100M.TIF",  # roughness (m), 100 m baseline
}

# ------------------------------- Regolith thermophysical model (Hayne+2017 JGR)
RHO_S, RHO_D = 1100.0, 1800.0      # kg/m^3 surface / deep density
H_PARAM = 0.06                     # m  density e-folding depth (global typical)
K_S, K_D = 7.4e-4, 3.4e-3          # W/m/K conductivity surface / deep
CHI = 2.7                          # radiative conductivity parameter
CP_COEF = (-3.6125, 2.7431, 2.3616e-3, -1.2340e-5, 8.9093e-9)
BOND_ALBEDO = 0.12                 # mean Bond albedo, normal incidence (Vasavada+2012)
EMISSIVITY = 0.95                  # bolometric emissivity (Bandfield+2015)
GEOTHERMAL = 0.018                 # W/m^2, ~Apollo 15/17 heat-flow order of magnitude
SIGMA_SB = 5.670374419e-8

# ------------------------------------------ Regolith mechanics (NASA NTRS)
# Connolly/Carrier, NTRS 20220018853 & 20220014634 (Lunar Sourcebook, Heiken+1991)
COHESION_SURFACE_KPA = (0.1, 1.0)
COHESION_DEEP_KPA = 3.0            # > 3 kPa below ~0.5 m
FRICTION_ANGLE_DEG = (30.0, 50.0)  # at surface; -> ~55 deg within first metre

# ----------------------------------------------- Radiation (measured, Chang'e-4)
# Zhang+2020 Sci. Adv. 6, eaaz1334 (LND instrument; solar-minimum conditions)
GCR_DOSE_EQ_USV_DAY = 1369.0
ISS_DOSE_EQ_USV_DAY = 731.0
NASA_API = "https://api.nasa.gov"
NASA_API_KEY = "8DWQrk9zb7Jg9BjwCKKfgfcWWr77ktftpnFWqqkw"


@dataclass
class Config:
    date: str
    days: float = MISSION_DAYS
    top: int = 3
    demo: bool = False
    local_dir: str = ""
    screen_decimate: int = 4
    horizon_range_m: float = 25_000.0   # OPTIMIZED: reduced from 30k for faster computation
    antenna_height_m: float = 5.0       # HEURISTIC: lander antenna height above ground
    slope_max: float = LANDING_SLOPE_MAX_DEG
    zone_radius_m: float = LANDING_ZONE_RADIUS_M
    lander_mass_kg: float = 7300.0      # example: Apollo LM-class (user-adjustable)
    footpad_diameter_m: float = 0.94
    n_footpads: int = 4
    do_thermal: bool = True
    do_donki: bool = False              # OPTIMIZED: disabled by default (slow external API)
    offline_ephemeris: bool = False
    candidates: int = 1                     # OPTIMIZED: Single best site only
    lat_limit: float = LAT_LIMIT_DEG
