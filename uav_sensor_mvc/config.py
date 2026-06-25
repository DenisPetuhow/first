# -*- coding: utf-8 -*-
"""
Конфигурация модели размещения датчиков обнаружения БПЛА.

Содержит входные параметры (Params), режимы оптимизации (MODES), профили
разброса маршрутов (MOTION_PROFILES), подписи и цветовую тему интерфейса.

Геометрия задаётся двумя числами: расстоянием |AB| и запасом хода L_max.
Точки A, B размещаются на оси X (A в начале координат). Карта (оси) подгоняется
под КОРИДОР вероятных маршрутов, а не под весь эллипс достижимости.
"""
from dataclasses import dataclass
from typing import Optional, Tuple


# Три режима оптимизации: веса (alpha1 — кратность, alpha2 — равномерность)
MODES = {
    "distribution": (0.25, 0.75),
    "crossing":     (0.75, 0.25),
    "balanced":     (0.50, 0.50),
}
MODE_LABELS = {
    "distribution": "распределение",
    "crossing":     "пересечение",
    "balanced":     "сбалансированный",
}

TRAJ_LABELS = {
    "arc":        "дуги (этап 1)",
    "serpentine": "петли (этап 2)",
    "maneuver":   "манёвр (ломаная)",
}

# Модели движения для вкладки 2 (старт из зоны -> цель B)
AREA_TRAJ_LABELS = {
    "area_arc":  "дуги из старта",
    "maneuver":  "ломаная (манёвр)",
}

# Профили разброса маршрутов (как часто выбираются длинные/короткие пути)
MOTION_PROFILES = ("normal", "mixed", "complex")
MOTION_LABELS = {
    "normal":  "обычный",     # короткий путь — самый частый (усечённое нормальное)
    "mixed":   "смешанный",   # равномерно по всему диапазону углов
    "complex": "сложный",     # тяготеет к предельным (сильно выгнутым) дугам
}

# Вес вторичного критерия распределения (включается при насыщении основного)
SPREAD_EPS = 1.0

THEME = {
    "bg":      "#0f1623", "panel":  "#172033", "axes":    "#0c1320",
    "accent":  "#36c5f0", "accent2": "#7c5cff", "ok":      "#3ddc97",
    "warn":    "#ff5d6c", "text":   "#e6edf3", "muted":   "#8aa0b6",
    "grid":    "#22304a", "ellipse": "#5b6b85",
}


@dataclass
class Params:
    """Входные параметры модели (все редактируются в интерфейсе)."""
    ab_distance: float = 100.0     # расстояние |AB|, км
    L_max: float = 150.0           # запас хода, км
    N: int = 6                     # число датчиков (все задействуются)
    R: float = 12.0                # радиус обнаружения, км
    k: int = 3                     # требуемая кратность обнаружения
    L_seg: int = 10                # число сегментов разбиения маршрута
    angle_step_deg: float = 10.0   # шаг угла веера вероятных дуг
    corridor_depth: float = 60.0   # глубина зоны старта (вдоль A->B), вкладка 2
    corridor_width: float = 120.0  # ширина зоны старта (поперёк A->B), вкладка 2
    mode: str = "balanced"         # режим оптимизации
    traj_model: str = "arc"        # модель движения
    motion_profile: str = "mixed"  # профиль разброса маршрутов
    T: int = 400                   # число итераций накопления
    grid_step: float = 7.0         # шаг сетки кандидатных позиций, км
    sigma_frac: float = 0.45       # ширина пучка (доля предельного угла)
    n_points: int = 140            # точек дискретизации маршрута
    seed: Optional[int] = None     # зерно ГСЧ; None -> случайно при каждом прогоне

    # геопривязка (опционально, из CLI)
    geo: bool = False
    A_geo: Tuple[float, float] = (0.0, 0.0)
    B_geo: Tuple[float, float] = (0.0, 0.0)

    @property
    def A(self) -> Tuple[float, float]:
        return (0.0, 0.0)

    @property
    def B(self) -> Tuple[float, float]:
        return (float(self.ab_distance), 0.0)

    def validate(self) -> None:
        if self.L_max <= self.ab_distance:
            raise ValueError(
                f"Запас хода L_max={self.L_max} должен быть больше |AB|="
                f"{self.ab_distance}.")
        if self.mode not in MODES:
            raise ValueError(f"Неизвестный режим: {self.mode}")
        if self.traj_model not in TRAJ_LABELS:
            raise ValueError(f"Неизвестная модель движения: {self.traj_model}")
        if self.motion_profile not in MOTION_PROFILES:
            raise ValueError(f"Неизвестный профиль: {self.motion_profile}")
        if not (1 <= self.L_seg <= 31):
            raise ValueError("L_seg должно быть 1..31.")
        if self.N < 1 or self.k < 1 or self.R <= 0:
            raise ValueError("N, k >= 1, R > 0.")
        if self.angle_step_deg < 1.0:
            raise ValueError("Шаг угла >= 1°.")
