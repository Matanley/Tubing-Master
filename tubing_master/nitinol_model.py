"""1D superelastic Nitinol (NiTi SMA) stress model for tube-drawing estimates.

Loading path (stress-induced martensite): austenite → σ_ms–σ_mf plateau → martensite hardening.
Unloading path (reverse transform): martensite elastic → σ_af–σ_as plateau → austenite.
Illustrative — calibrate to your lot, Af temperature, and heat treatment.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

StressPath = Literal["loading", "unloading"]


@dataclass(frozen=True)
class NitinolParams:
    """Illustrative superelastic NiTi (binary, cold-drawn tube)."""

    e_austenite_mpa: float = 69000.0
    e_martensite_mpa: float = 41000.0
    sigma_ms_mpa: float = 280.0  # loading: martensite start
    sigma_mf_mpa: float = 420.0  # loading: martensite finish
    sigma_as_mpa: float = 140.0  # unloading: austenite start
    sigma_af_mpa: float = 360.0  # unloading: austenite finish
    transformation_strain: float = 0.055  # recoverable transformation strain (~5.5%)
    martensite_eps0: float = 0.002
    martensite_C_mpa: float = 680.0
    martensite_n: float = 0.28
    uts_mpa: float = 950.0
    uts_strain_coef_mpa: float = 120.0
    af_temp_c: float = 10.0
    ms_temp_c: float = -30.0


@dataclass(frozen=True)
class NitinolPassMetrics:
    """Per-pass superelastic hysteresis summary after a drawing bite."""

    true_strain_peak: float
    loading_flow_stress_mpa: float
    unloading_stress_mpa: float
    hysteresis_mpa: float
    springback_strain: float
    permanent_strain: float
    residual_stress_mpa: float


def _eps_ms(p: NitinolParams) -> float:
    return p.sigma_ms_mpa / max(p.e_austenite_mpa, 1e-6)


def _eps_tr_end(p: NitinolParams) -> float:
    return _eps_ms(p) + max(p.transformation_strain, 1e-9)


def nitinol_loading_stress_mpa(true_strain: float, params: NitinolParams) -> float:
    """
    Loading branch σ(ε): austenite → transformation plateau → martensite hardening.
    """
    eps = max(0.0, float(true_strain))
    p = params
    eps_ms = _eps_ms(p)
    if eps <= eps_ms:
        return float(p.e_austenite_mpa * eps)

    eps_tr_end = _eps_tr_end(p)
    if eps <= eps_tr_end:
        frac = (eps - eps_ms) / max(p.transformation_strain, 1e-9)
        return float(p.sigma_ms_mpa + frac * (p.sigma_mf_mpa - p.sigma_ms_mpa))

    eps_mart = eps - eps_tr_end
    return float(
        p.sigma_mf_mpa
        + p.martensite_C_mpa * (p.martensite_eps0 + eps_mart) ** p.martensite_n
    )


def nitinol_unloading_stress_mpa(
    strain: float,
    params: NitinolParams,
    *,
    peak_strain: float,
) -> float:
    """
    Unloading branch σ(ε) after loading to ``peak_strain``.

    ``strain`` is the current (lower) strain during springback, 0 ≤ strain ≤ peak_strain.
    """
    eps = max(0.0, float(strain))
    peak = max(eps, float(peak_strain))
    p = params
    eps_ms = _eps_ms(p)
    eps_tr_end = _eps_tr_end(p)

    if peak <= eps_ms:
        return float(p.e_austenite_mpa * eps)

    if eps <= eps_ms:
        return float(p.e_austenite_mpa * eps)

    if peak <= eps_tr_end and eps <= eps_tr_end:
        frac = (eps - eps_ms) / max(p.transformation_strain, 1e-9)
        return float(p.sigma_af_mpa - frac * (p.sigma_af_mpa - p.sigma_as_mpa))

    if peak > eps_tr_end and eps > eps_tr_end:
        sigma_peak = nitinol_loading_stress_mpa(peak, p)
        sigma = sigma_peak - p.e_martensite_mpa * (peak - eps)
        return float(max(p.sigma_af_mpa, sigma))

    frac = (eps - eps_ms) / max(p.transformation_strain, 1e-9)
    return float(p.sigma_af_mpa - frac * (p.sigma_af_mpa - p.sigma_as_mpa))


def nitinol_stress_mpa(
    strain: float,
    params: NitinolParams,
    *,
    path: StressPath = "loading",
    peak_strain: float | None = None,
) -> float:
    """Path-aware 1D stress; unloading requires ``peak_strain``."""
    if path == "loading":
        return nitinol_loading_stress_mpa(strain, params)
    if peak_strain is None:
        raise ValueError("peak_strain is required when path='unloading'")
    return nitinol_unloading_stress_mpa(strain, params, peak_strain=peak_strain)


def nitinol_unloading_stress_at_peak_mpa(true_strain: float, params: NitinolParams) -> float:
    """Stress on the unloading leg when springback begins (reverse-transform onset)."""
    eps = max(0.0, float(true_strain))
    if eps <= _eps_tr_end(params):
        return nitinol_unloading_stress_mpa(eps, params, peak_strain=eps)
    return float(params.sigma_af_mpa)


def nitinol_springback_and_permanent_strain(
    true_strain: float, params: NitinolParams
) -> tuple[float, float]:
    """
    Return (springback_strain, permanent_strain) after a pass peak strain.

    Transformation strain is treated as recoverable; martensite plastic strain is not.
    """
    eps = max(0.0, float(true_strain))
    eps_ms = _eps_ms(params)
    eps_tr_end = _eps_tr_end(params)

    if eps <= eps_ms:
        return eps, 0.0

    eps_mart = max(0.0, eps - eps_tr_end)
    springback = min(eps, eps_tr_end)
    permanent = eps_mart
    return float(springback), float(permanent)


def nitinol_residual_stress_after_unload_mpa(
    true_strain: float, params: NitinolParams
) -> float:
    """Back-stress locked in after full unload when martensite plastic strain remains."""
    _, permanent = nitinol_springback_and_permanent_strain(true_strain, params)
    if permanent < 1e-6:
        return 0.0
    return float(
        params.martensite_C_mpa
        * (params.martensite_eps0 + permanent) ** params.martensite_n
        * 0.08
    )


def nitinol_pass_metrics(
    true_strain: float,
    params: NitinolParams,
    *,
    loading_flow_stress_mpa: float | None = None,
) -> NitinolPassMetrics:
    """Full loading/unloading summary for one drawing pass."""
    eps = max(0.0, float(true_strain))
    load = (
        float(loading_flow_stress_mpa)
        if loading_flow_stress_mpa is not None
        else nitinol_loading_stress_mpa(eps, params)
    )
    unload = nitinol_unloading_stress_at_peak_mpa(eps, params)
    springback, permanent = nitinol_springback_and_permanent_strain(eps, params)
    residual = nitinol_residual_stress_after_unload_mpa(eps, params)
    return NitinolPassMetrics(
        true_strain_peak=eps,
        loading_flow_stress_mpa=load,
        unloading_stress_mpa=unload,
        hysteresis_mpa=max(0.0, load - unload),
        springback_strain=springback,
        permanent_strain=permanent,
        residual_stress_mpa=residual,
    )


def nitinol_flow_stress_mpa(true_strain: float, params: NitinolParams) -> float:
    """Alias for loading flow stress (tube-drawing forward path)."""
    return nitinol_loading_stress_mpa(true_strain, params)


def nitinol_ultimate_strength_mpa(
    true_strain: float,
    params: NitinolParams,
    *,
    reduction_fraction: float = 0.0,
) -> float:
    """Fracture / capacity proxy for cold-drawn martensitic Nitinol."""
    del reduction_fraction
    eps = max(0.0, float(true_strain))
    return float(params.uts_mpa + params.uts_strain_coef_mpa * min(eps, 0.6) ** 0.5)


def nitinol_transformation_onset_mpa(params: NitinolParams) -> float:
    """Stress at which stress-induced martensite begins (for FEA yield checks)."""
    return float(params.sigma_ms_mpa)


def nitinol_superelastic_note(params: NitinolParams) -> str:
    return (
        f"Superelastic NiTi (Af≈{params.af_temp_c:.0f} °C): "
        f"load plateau {params.sigma_ms_mpa:.0f}–{params.sigma_mf_mpa:.0f} MPa, "
        f"unload plateau {params.sigma_as_mpa:.0f}–{params.sigma_af_mpa:.0f} MPa, "
        f"ε_tr≈{100.0 * params.transformation_strain:.1f}%."
    )
