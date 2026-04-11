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
from typing import List

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
# Clear-sky irradiance
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
    G_sc = 1361.0  # W/m² solar constant
    G_on = G_sc * (1.0 + 0.033 * math.cos(math.radians(360.0 * n / 365.0)))

    # Optical depth correction (Meinel & Meinel approximation)
    sin_alt = math.sin(altitude)
    if sin_alt <= 0.0:
        return 0.0
    # Air-mass number (Kasten & Young 1989)
    am = 1.0 / (sin_alt + 0.50572 * math.degrees(altitude + 0.07628) ** -1.6364)
    # Atmospheric transmittance
    tau_b = 0.56 * (math.exp(-0.65 * am) + math.exp(-0.095 * am))
    return G_on * tau_b


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


def window_solar_gain(
    window: Window,
    dt: datetime,
    latitude: float,
    longitude: float,
    shgc: float = DEFAULT_SHGC,
) -> float:
    """
    Compute the solar heat gain through a single window [W].

    Parameters
    ----------
    window : Window
        Window geometry (area, orientation, tilt).
    dt : datetime
        Current datetime (UTC or aware).
    latitude : float
        Site latitude [degrees].
    longitude : float
        Site longitude [degrees].
    shgc : float
        Solar heat gain coefficient (0–1).

    Returns
    -------
    float : solar heat gain in Watts.
    """
    altitude, azimuth_deg = solar_angles(dt, latitude, longitude)
    if altitude <= 0.0:
        return 0.0  # night-time

    n = _day_of_year(dt)
    dni = clear_sky_dni(altitude, n)
    dhi = clear_sky_dhi(altitude, dni)

    # Direct beam component on the tilted window
    theta = angle_of_incidence(altitude, azimuth_deg, window.tilt, window.orientation)
    direct = max(0.0, dni * math.cos(theta))

    # Isotropic sky diffuse on tilted surface
    tilt_r = math.radians(window.tilt)
    diffuse = dhi * (1.0 + math.cos(tilt_r)) / 2.0

    irradiance = direct + diffuse  # W/m²
    return shgc * window.area * irradiance


def room_solar_gains(
    windows: List[Window],
    dt: datetime,
    latitude: float,
    longitude: float,
    shgc: float = DEFAULT_SHGC,
) -> float:
    """
    Total solar heat gain for a room [W] as the sum over all its windows.
    """
    return sum(
        window_solar_gain(w, dt, latitude, longitude, shgc) for w in windows
    )
