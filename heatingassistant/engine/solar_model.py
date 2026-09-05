"""
Solar heat gain model for the Heating Assistant integration.

Calculates the solar irradiance on a tilted, oriented surface using a
simplified isotropic-sky diffuse model (Liu & Jordan) and a clear-sky
direct-normal irradiance estimate.  The result is the instantaneous solar
heat gain [W] for each window of a room.

References
----------
* Duffie & Beckman, "Solar Engineering of Thermal Processes", 4th ed.
* ASHRAE Fundamentals Handbook, Chapter 14
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .const import SOLAR_GAIN_SMOOTHING_TAU_S
from .thermal_model import Window


# ---------------------------------------------------------------------------
# Solar position
# ---------------------------------------------------------------------------

def _day_of_year(dt: datetime) -> int:
    """Return the day-of-year (1–365/366) for a given datetime."""
    return dt.timetuple().tm_yday


def _equation_of_time(n: int) -> float:
    """
    Equation of time in minutes (Spencer 1971).

    Parameters
    ----------
    n : int  day-of-year
    """
    B = math.radians(360.0 / 365.0 * (n - 81))
    return 9.87 * math.sin(2 * B) - 7.53 * math.cos(B) - 1.5 * math.sin(B)


def _solar_declination(n: int) -> float:
    """
    Solar declination angle in radians (Cooper equation).

    Parameters
    ----------
    n : int  day-of-year
    """
    return math.radians(23.45 * math.sin(math.radians(360.0 / 365.0 * (n - 81))))


def solar_angles(
    dt: datetime,
    latitude: float,
    longitude: float,
) -> tuple[float, float]:
    """
    Compute the solar altitude and azimuth angles.

    Parameters
    ----------
    dt : datetime
        Local solar time (aware or naive – naive is treated as UTC).
    latitude : float
        Geographic latitude in degrees (positive = North).
    longitude : float
        Geographic longitude in degrees (positive = East).

    Returns
    -------
    altitude : float
        Solar altitude above horizon in radians (negative when below horizon).
    azimuth : float
        Solar azimuth in degrees clockwise from North
        (0=N, 90=E, 180=S, 270=W).
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    # Convert to UTC solar time
    n = _day_of_year(dt)
    eot = _equation_of_time(n)
    # Apparent solar time
    solar_time = (
        dt.hour + dt.minute / 60.0 + dt.second / 3600.0
        + eot / 60.0
        + (longitude - 0.0) / 15.0  # 0.0 = reference meridian (UTC)
    )
    hour_angle = math.radians(15.0 * (solar_time - 12.0))  # noon = 0

    dec = _solar_declination(n)
    lat = math.radians(latitude)

    # Altitude
    sin_alt = (
        math.sin(lat) * math.sin(dec)
        + math.cos(lat) * math.cos(dec) * math.cos(hour_angle)
    )
    sin_alt = max(-1.0, min(1.0, sin_alt))
    altitude = math.asin(sin_alt)

    # Azimuth (clockwise from South, converted to clockwise from North)
    cos_az_south = (
        (math.sin(dec) * math.cos(lat) - math.cos(dec) * math.sin(lat) * math.cos(hour_angle))
        / math.cos(altitude)
        if math.cos(altitude) > 1e-6
        else 0.0
    )
    cos_az_south = max(-1.0, min(1.0, cos_az_south))
    az_south = math.degrees(math.acos(cos_az_south))

    # morning: east of south (hour_angle < 0) → azimuth from South is negative
    if hour_angle < 0:
        az_south = -az_south

    # Convert from clockwise-from-South to clockwise-from-North
    azimuth = (az_south + 180.0) % 360.0
    return altitude, azimuth


# ---------------------------------------------------------------------------
# Extra-terrestrial reference (pure geometry/almanac — no atmosphere)
# ---------------------------------------------------------------------------

# Solar constant [W/m²].
G_SC = 1361.0


def extraterrestrial_normal_irradiance(n: int) -> float:
    """
    Extra-terrestrial Direct Normal Irradiance [W/m²] for day-of-year ``n``.

    This is the solar constant corrected only for the Earth–Sun distance — it
    contains **no** atmospheric assumption.  It is the geometric reference used
    by the irradiance decomposition (:func:`ghi_to_dni_dhi`) to form the
    clearness index, so the forecast-driven path never needs the clear-sky
    *transmittance* model.
    """
    return G_SC * (1.0 + 0.033 * math.cos(math.radians(360.0 * n / 365.0)))


# ---------------------------------------------------------------------------
# Clear-sky irradiance (the fallback intensity source)
# ---------------------------------------------------------------------------

def clear_sky_dni(altitude: float, n: int) -> float:
    """
    Estimate clear-sky Direct Normal Irradiance [W/m²]
    using the ASHRAE simple clear-sky model.

    Parameters
    ----------
    altitude : float  solar altitude in radians
    n : int           day-of-year
    """
    if altitude <= 0.0:
        return 0.0

    # Extra-terrestrial irradiance (corrected for Earth-Sun distance)
    G_on = extraterrestrial_normal_irradiance(n)

    # Optical depth correction (Meinel & Meinel approximation)
    sin_alt = math.sin(altitude)
    if sin_alt <= 0.0:
        return 0.0
    # Air-mass number (Kasten & Young 1989)
    am = 1.0 / (sin_alt + 0.50572 * math.degrees(altitude + 0.07628) ** -1.6364)
    # Atmospheric transmittance
    tau_b = 0.56 * (math.exp(-0.65 * am) + math.exp(-0.095 * am))
    return G_on * tau_b


def erbs_diffuse_fraction(kt: float) -> float:
    """
    Diffuse fraction of global horizontal irradiance from the hourly **Erbs**
    correlation (Erbs, Klein & Duffie 1982).

    ``kt`` is the clearness index ``GHI / GHI_extraterrestrial`` (in [0, 1]).
    Returns the fraction of GHI that is diffuse — near 1.0 under overcast skies
    (low ``kt``) and ~0.16 under very clear skies (high ``kt``).  This is what
    lets the forecast set the beam/diffuse split from data, rather than the
    fixed clear-sky split: it depends only on the measured clearness, not on any
    atmospheric transmittance model.
    """
    kt = max(0.0, min(1.0, kt))
    if kt <= 0.22:
        return 1.0 - 0.09 * kt
    if kt <= 0.80:
        return (
            0.9511
            - 0.1604 * kt
            + 4.388 * kt ** 2
            - 16.638 * kt ** 3
            + 12.336 * kt ** 4
        )
    return 0.165


def ghi_to_dni_dhi(ghi: float, altitude: float, n: int) -> tuple[float, float]:
    """
    Decompose Global Horizontal Irradiance into (DNI, DHI) using only geometry.

    Given a measured/forecast GHI [W/m²], the solar altitude, and the day of
    year, returns the Direct Normal and Diffuse Horizontal components via the
    Erbs diffuse-fraction correlation.  The only "model" inputs are the sun
    position and the extra-terrestrial reference — there is **no** clear-sky
    transmittance assumption, so this is the geometry-only bridge from a solar
    forecast to per-surface irradiance.

    Returns ``(dni, dhi)`` in W/m²; ``(0, 0)`` at night.
    """
    if altitude <= 0.0 or ghi <= 0.0:
        return 0.0, 0.0
    sin_alt = math.sin(altitude)
    if sin_alt <= 1e-4:
        # Sun on the horizon: treat the whole (tiny) GHI as diffuse to avoid a
        # divide-by-near-zero beam-normal blow-up.
        return 0.0, ghi
    ghi_extra = extraterrestrial_normal_irradiance(n) * sin_alt
    kt = ghi / ghi_extra if ghi_extra > 0.0 else 0.0
    df = erbs_diffuse_fraction(kt)
    dhi = df * ghi
    dni = (ghi - dhi) / sin_alt  # beam-horizontal → beam-normal
    dni = max(0.0, min(dni, extraterrestrial_normal_irradiance(n)))
    return dni, dhi


def transpose_to_surface(
    dni: float,
    dhi: float,
    altitude: float,
    azimuth_deg: float,
    surface_tilt: float,
    surface_azimuth: float,
    albedo: float = 0.2,
) -> float:
    """
    Plane-of-array irradiance [W/m²] on a surface from (DNI, DHI).

    The shared **window-coupling** primitive — three components, identical
    for both intensity sources (clear-sky fallback and forecast), so the
    geometry lives in exactly one place:

    * **beam** — cosine of the incidence angle, reduced by an
      incidence-angle modifier (:func:`incidence_angle_modifier`) so that
      grazing-incidence transmission losses are not over-counted;
    * **sky diffuse** — isotropic sky-view factor ``(1 + cos β)/2``;
    * **ground-reflected** — isotropic ground term
      ``ρ · GHI · (1 − cos β)/2`` where ``GHI = DNI·sin(h) + DHI``.  For a
      vertical window this is ``ρ · GHI / 2`` — with snow on the ground
      (``ρ ≈ 0.7``) a dominant winter component at high latitudes.

    ``albedo`` is the ground reflectance ρ ∈ [0, 1] (grass ≈ 0.2,
    fresh snow ≈ 0.8).
    """
    if altitude <= 0.0:
        return 0.0
    theta = angle_of_incidence(altitude, azimuth_deg, surface_tilt, surface_azimuth)
    iam = incidence_angle_modifier(theta)
    direct = max(0.0, dni * math.cos(theta) * iam)
    tilt_r = math.radians(surface_tilt)
    diffuse = dhi * (1.0 + math.cos(tilt_r)) / 2.0
    ghi = max(0.0, dni * math.sin(altitude) + dhi)
    ground = max(0.0, min(1.0, albedo)) * ghi * (1.0 - math.cos(tilt_r)) / 2.0
    return direct + diffuse + ground


def _intensity_dni_dhi(
    altitude: float,
    n: int,
    cloud_cover: float | None,
    ghi: float | None,
) -> tuple[float, float]:
    """Resolve the (DNI, DHI) intensity for a timestep from the active source.

    Precedence: a forecast ``ghi`` (decomposed via Erbs — geometry only) takes
    priority; otherwise the clear-sky model attenuated by the optional
    Kasten–Czeplak cloud factor.

    Every source supplies only a **GHI magnitude**; the beam/diffuse split
    is always done by the same Erbs correlation (:func:`ghi_to_dni_dhi`):

    * forecast ``ghi`` — used verbatim;
    * cloudy fallback — clear-sky GHI × Kasten–Czeplak factor;
    * clear fallback — clear-sky GHI.

    One decomposition everywhere means the paths agree at their seams
    (``cloud_cover = 0`` equals "no cloud data" exactly), the total
    horizontal irradiance follows the Kasten–Czeplak factor exactly, and
    the beam share collapses with increasing cloud (low clearness index →
    mostly diffuse) — the physically correct behaviour for directional
    gains through oriented windows.
    """
    if ghi is not None:
        return ghi_to_dni_dhi(ghi, altitude, n)
    if altitude <= 0.0:
        return 0.0, 0.0
    dni_cs = clear_sky_dni(altitude, n)
    ghi_eff = max(0.0, dni_cs * math.sin(altitude) + clear_sky_dhi(altitude, dni_cs))
    if cloud_cover is not None:
        ghi_eff *= cloud_attenuation_factor(cloud_cover)
    return ghi_to_dni_dhi(ghi_eff, altitude, n)


def clear_sky_dhi(altitude: float, dni: float) -> float:
    """
    Estimate clear-sky Diffuse Horizontal Irradiance [W/m²].

    Parameters
    ----------
    altitude : float  solar altitude in radians
    dni : float       direct normal irradiance [W/m²]
    """
    if altitude <= 0.0:
        return 0.0
    # Simple isotropic model: DHI ≈ 0.1 * GHI
    ghi = dni * math.sin(altitude)
    return 0.1 * ghi


def clear_sky_plane_poa(
    dt: datetime,
    latitude: float,
    longitude: float,
    surface_tilt: float,
    surface_azimuth: float,
) -> float:
    """
    Clear-sky plane-of-array (POA) irradiance on a tilted surface [W/m²].

    Composes the same direct-beam + isotropic-diffuse model used by
    :func:`window_solar_gain` (minus the SHGC and area factors), returning
    the clear-sky incident irradiance for a surface of arbitrary tilt and
    orientation.  A convenience helper for diagnostics and tests; the live
    paths use :func:`transpose_to_surface` with the active intensity source.

    Parameters
    ----------
    dt : datetime          current datetime (UTC or aware).
    latitude : float       site latitude [degrees].
    longitude : float      site longitude [degrees].
    surface_tilt : float   tilt from horizontal [degrees] (90 = vertical).
    surface_azimuth : float surface azimuth clockwise from North [degrees].

    Returns
    -------
    float : clear-sky POA irradiance [W/m²]; ``0.0`` at night.
    """
    altitude, azimuth_deg = solar_angles(dt, latitude, longitude)
    if altitude <= 0.0:
        return 0.0

    n = _day_of_year(dt)
    dni, dhi = _intensity_dni_dhi(altitude, n, None, None)
    return transpose_to_surface(
        dni, dhi, altitude, azimuth_deg, surface_tilt, surface_azimuth
    )


def horizontal_irradiance(
    dt: datetime,
    latitude: float,
    longitude: float,
    cloud_cover: float | None = None,
    ghi: float | None = None,
) -> float:
    """
    Global Horizontal Irradiance (GHI) at the site [W/m²].

    A building-level, location-only solar intensity: the sun's power landing on
    a flat horizontal surface, independent of any individual room's windows or
    orientation.  This is the "how strong is the sun right now" figure used for
    the overview KPI.

    Precedence mirrors the gains path: a forecast/measured ``ghi`` is returned
    verbatim; otherwise the clear-sky model — attenuated by the optional
    Kasten–Czeplak cloud factor — supplies a modeled GHI so the value is
    available even when no irradiance sensor is configured.

    Parameters
    ----------
    dt : datetime          current datetime (UTC or aware).
    latitude : float       site latitude [degrees].
    longitude : float      site longitude [degrees].
    cloud_cover : float, optional  fraction in [0, 1]; used only when ``ghi``
                                   is ``None`` to attenuate the clear-sky model.
    ghi : float, optional  measured/forecast GHI [W/m²]; returned directly.

    Returns
    -------
    float : GHI in W/m²; ``0.0`` at night.
    """
    if ghi is not None:
        return max(0.0, ghi)

    altitude, _azimuth = solar_angles(dt, latitude, longitude)
    if altitude <= 0.0:
        return 0.0

    n = _day_of_year(dt)
    dni, dhi = _intensity_dni_dhi(altitude, n, cloud_cover, None)
    # GHI = beam-horizontal (DNI·sin(altitude)) + diffuse-horizontal (DHI).
    return max(0.0, dni * math.sin(altitude) + dhi)


# ---------------------------------------------------------------------------
# Incidence angle on tilted surface
# ---------------------------------------------------------------------------

def angle_of_incidence(
    altitude: float,
    azimuth_deg: float,
    surface_tilt: float,
    surface_azimuth: float,
) -> float:
    """
    Angle of incidence of direct beam radiation on a tilted surface.

    Parameters
    ----------
    altitude : float       solar altitude [radians]
    azimuth_deg : float    solar azimuth clockwise from North [degrees]
    surface_tilt : float   surface tilt from horizontal [degrees]  (90 = vertical)
    surface_azimuth : float  surface azimuth clockwise from North [degrees]

    Returns
    -------
    float : angle of incidence in radians (π/2 = grazing, 0 = normal)
    """
    tilt_r = math.radians(surface_tilt)
    gamma = math.radians(azimuth_deg - surface_azimuth)  # relative azimuth
    cos_theta = (
        math.cos(altitude) * math.cos(gamma) * math.sin(tilt_r)
        + math.sin(altitude) * math.cos(tilt_r)
    )
    cos_theta = max(0.0, min(1.0, cos_theta))
    return math.acos(cos_theta)


# ---------------------------------------------------------------------------
# Solar heat gain through windows
# ---------------------------------------------------------------------------

# Simple solar heat gain coefficient (SHGC) – assume 0.6 for clear double glazing
DEFAULT_SHGC = 0.6

#: ASHRAE incidence-angle-modifier coefficient for double glazing.  The SHGC
#: is quoted at normal incidence; transmittance falls off at grazing angles.
IAM_B0 = 0.1


def incidence_angle_modifier(theta: float, b0: float = IAM_B0) -> float:
    """
    ASHRAE incidence-angle modifier  ``IAM = 1 − b₀ (1/cos θ − 1)``.

    Reduces the normal-incidence SHGC at oblique beam incidence (θ in
    radians, 0 = normal).  Clamped to [0, 1]; returns 0 at θ ≥ 90° where the
    expression diverges.  ``b₀ ≈ 0.1`` is a reasonable value for clear double
    glazing.
    """
    cos_theta = math.cos(theta)
    if cos_theta <= 1e-6:
        return 0.0
    return max(0.0, min(1.0, 1.0 - b0 * (1.0 / cos_theta - 1.0)))


def cloud_attenuation_factor(cloud_cover: float) -> float:
    """
    Empirical Kasten–Czeplak (1980) cloud attenuation of global irradiance.

        GHI_cloudy / GHI_clear = 1 − 0.75 · c^3.4

    where ``c ∈ [0, 1]`` is the cloud-cover fraction.  Returns 1.0 for
    clear sky, ≈0.93 at 50 % cover (the cubic power barely bites until
    skies are nearly overcast), and 0.25 fully overcast.

    Note: strictly, beam (DNI) and diffuse (DHI) attenuate at very different
    rates under cloud — DNI collapses much faster than DHI.  We apply the
    same GHI-equivalent factor to both components here as a first-order
    approximation; the SHGC and ``DEFAULT_SHGC`` constant absorb the residual.
    """
    c = max(0.0, min(1.0, cloud_cover))
    return max(0.0, 1.0 - 0.75 * (c ** 3.4))


def window_solar_gain(
    window: Window,
    dt: datetime,
    latitude: float,
    longitude: float,
    shgc: float = DEFAULT_SHGC,
    cloud_cover: float | None = None,
    ghi: float | None = None,
    albedo: float = 0.2,
) -> float:
    """
    Compute the solar heat gain through a single window [W].

    The geometry (sun position, incidence angle, sky-view factor) is fixed; the
    **intensity** is swappable:

    * ``ghi`` — forecast Global Horizontal Irradiance [W/m²].  When provided it
      is decomposed into beam/diffuse via the Erbs correlation
      (:func:`ghi_to_dni_dhi`, geometry only) and **takes precedence**.  This is
      the path used when a solar-forecast sensor is configured: the magnitude
      comes purely from the forecast, the model contributes only geometry.
    * ``cloud_cover`` — fraction in [0, 1].  Used only when ``ghi`` is ``None``;
      attenuates the clear-sky model by the Kasten–Czeplak factor.  This is the
      analytical fallback.
    * neither — clear sky.

    Parameters
    ----------
    window : Window        window geometry (area, orientation, tilt).
    dt : datetime          current datetime (UTC or aware).
    latitude, longitude : float   site location [degrees].
    shgc : float           solar heat gain coefficient (0–1).

    Returns
    -------
    float : solar heat gain in Watts.
    """
    altitude, azimuth_deg = solar_angles(dt, latitude, longitude)
    if altitude <= 0.0:
        return 0.0  # night-time

    n = _day_of_year(dt)
    dni, dhi = _intensity_dni_dhi(altitude, n, cloud_cover, ghi)
    poa = transpose_to_surface(
        dni, dhi, altitude, azimuth_deg, window.tilt, window.orientation,
        albedo=albedo,
    )
    return shgc * window.area * poa


def room_solar_gains(
    windows: List[Window],
    dt: datetime,
    latitude: float,
    longitude: float,
    shgc: float = DEFAULT_SHGC,
    cloud_cover: float | None = None,
    ghi: float | None = None,
    albedo: float = 0.2,
) -> float:
    """
    Total solar heat gain for a room [W] as the sum over all its windows.

    When forecast ``ghi`` [W/m²] is provided it drives the intensity (decomposed
    geometrically) and takes precedence over ``cloud_cover``; otherwise the
    clear-sky model attenuated by the optional cloud factor is used.
    """
    return sum(
        window_solar_gain(
            w, dt, latitude, longitude, shgc, cloud_cover, ghi, albedo,
        )
        for w in windows
    )


def room_solar_gains_from_exposure(
    aperture: float,
    facing: float,
    dt: datetime,
    latitude: float,
    longitude: float,
    cloud_cover: float | None = None,
    ghi: float | None = None,
    tilt: float = 90.0,
    albedo: float = 0.2,
) -> float:
    """
    Solar heat gain [W] for a room described by a single effective aperture.

    The lightweight, no-geometry alternative to enumerating individual
    windows: a room is summarised by one ``aperture`` [m²·SHGC effective]
    facing one direction.  Uses the same swappable intensity (forecast ``ghi``
    decomposed via Erbs, else clear-sky + cloud) and the same transposition as
    :func:`room_solar_gains`.  Returns ``0.0`` when ``aperture <= 0`` (the
    "none" exposure preset).

    Parameters
    ----------
    aperture : float       effective aperture [m²·SHGC]; the magnitude that
                           maps incident POA irradiance to room heat gain.
    facing : float         dominant sun-facing azimuth clockwise from North
                           [degrees] (0=N, 90=E, 180=S, 270=W).
    tilt : float           surface tilt [degrees]; vertical (90) by default.
    """
    if aperture <= 0.0:
        return 0.0
    altitude, azimuth_deg = solar_angles(dt, latitude, longitude)
    if altitude <= 0.0:
        return 0.0
    n = _day_of_year(dt)
    dni, dhi = _intensity_dni_dhi(altitude, n, cloud_cover, ghi)
    poa = transpose_to_surface(
        dni, dhi, altitude, azimuth_deg, tilt, facing, albedo=albedo,
    )
    return aperture * poa


def coerce_solar_gain_smoothing_tau_s(value: Any) -> float:
    """Return a finite τ ≥ 0 seconds; invalid input uses the 1800 s default."""
    if value is None or value == "":
        return float(SOLAR_GAIN_SMOOTHING_TAU_S)
    try:
        tau = float(value)
    except (TypeError, ValueError):
        return float(SOLAR_GAIN_SMOOTHING_TAU_S)
    if not math.isfinite(tau):
        return float(SOLAR_GAIN_SMOOTHING_TAU_S)
    return max(0.0, tau)


def smooth_solar_gain_step(
    prev: Optional[float],
    obs: float,
    dt: float,
    tau: float,
) -> float:
    """One first-order EMA step for a solar-gain watt sample.

    ``α = 1 − exp(−dt / τ)``.  The first valid observation seeds the filter
    (no startup transient).  Watts are clamped to ``≥ 0``.  ``τ ≤ 0`` is
    identity (returns the observation).
    """
    obs = max(0.0, float(obs))
    if prev is None or tau <= 0.0:
        return obs
    prev = max(0.0, float(prev))
    alpha = 1.0 - math.exp(-dt / tau) if dt > 0.0 else 1.0
    return alpha * obs + (1.0 - alpha) * prev


def smooth_solar_gain_schedule(
    schedules: Sequence[Mapping[str, float]],
    prev: Optional[Mapping[str, float]],
    dt: float,
    tau: float,
    rooms: Iterable[str],
) -> Tuple[List[Dict[str, float]], Dict[str, float]]:
    """Walk a solar schedule with a causal per-room EMA.

    Returns ``(filtered_schedule, k0_state)``.  ``k0_state`` is the filtered
    k = 0 map so the next live cycle can continue from NOW, not from the
    horizon tail.
    """
    names = [str(name) for name in rooms]
    state: Dict[str, float] = {
        name: float(prev[name])
        for name in names
        if prev is not None and name in prev
    }
    out: List[Dict[str, float]] = []
    for step in schedules:
        filtered: Dict[str, float] = {}
        for name in names:
            inst = float(step.get(name, 0.0) or 0.0)
            filtered[name] = smooth_solar_gain_step(
                state.get(name), inst, dt, tau,
            )
            state[name] = filtered[name]
        out.append(filtered)
    persist = dict(out[0]) if out else {}
    return out, persist
