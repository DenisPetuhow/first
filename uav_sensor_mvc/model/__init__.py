# -*- coding: utf-8 -*-
"""
MODEL — вычислительное ядро модели размещения датчиков обнаружения БПЛА.

Не зависит от средств отображения (View) и управления (Controller),
допускает автономный прогон. Публичный API:

    SimulationModel  — главный класс модели (состояние + расчёт)
    ellipse_geometry — геометрия эллипса достижимости
    make_arc, make_serpentine — построение траекторий
    candidate_grid, greedy_placement — дискретизация и оптимизация
    n_detections, continuous_coverage, segment_coverage — метрики
"""
from .simulation import SimulationModel
from .geometry import ellipse_geometry, point_in_ellipse, points_in_ellipse
from .trajectories import (make_arc, make_serpentine, polyline_length,
                           max_sagitta, signed_max_lateral, SAMPLERS)
from .detection import (n_detections, continuous_coverage, segment_coverage,
                        utility, avg_utility)
from .optimization import candidate_grid, greedy_placement, CoverageCache

__all__ = [
    "SimulationModel", "ellipse_geometry", "point_in_ellipse", "points_in_ellipse",
    "make_arc", "make_serpentine", "polyline_length", "max_sagitta",
    "signed_max_lateral", "SAMPLERS", "n_detections", "continuous_coverage",
    "segment_coverage", "utility", "avg_utility", "candidate_grid",
    "greedy_placement", "CoverageCache",
]
