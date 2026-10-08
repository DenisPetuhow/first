# -*- coding: utf-8 -*-
"""
MODEL — вычислительное ядро модели размещения датчиков обнаружения БПЛА.

Не зависит от средств отображения (View) и управления (Controller).
"""
from model.tabs12.simulation import SimulationModel
from model.tabs12.geometry import (ellipse_geometry, point_in_ellipse, points_in_ellipse,
                       auto_axes_limits, geo_to_local_km)
from model.tabs12.trajectories import (make_arc, make_arc_by_angle, make_serpentine,
                           polyline_length, max_deflection_angle,
                           arc_length_from_angle, arc_fan, signed_max_lateral,
                           SAMPLERS)
from model.tabs12.detection import (n_detections, continuous_coverage, segment_coverage,
                        utility, avg_utility)
from model.placement.optimization import candidate_grid, CoverageCache
from model.threat.threat_grid import ThreatGrid, ThreatModel, build_threat_grid, load_layers

__all__ = [
    "ThreatGrid", "ThreatModel", "build_threat_grid", "load_layers",
    "SimulationModel", "ellipse_geometry", "point_in_ellipse", "points_in_ellipse",
    "auto_axes_limits", "geo_to_local_km", "make_arc", "make_arc_by_angle",
    "make_serpentine", "polyline_length", "max_deflection_angle",
    "arc_length_from_angle", "arc_fan", "signed_max_lateral", "SAMPLERS",
    "n_detections", "continuous_coverage", "segment_coverage", "utility",
    "avg_utility", "candidate_grid", "CoverageCache",
]
