# -*- coding: utf-8 -*-
"""
Конфигурация модели размещения датчиков обнаружения БПЛА.

Содержит:
  * Params       — все входные параметры модели (раздел 1 требований);
  * MODES        — три режима оптимизации через веса (alpha1, alpha2);
  * MODE_LABELS  — русские подписи режимов для интерфейса;
  * TRAJ_LABELS  — подписи моделей движения (этап 1 / этап 2).

Модуль не зависит ни от вычислений, ни от визуализации.
"""
from dataclasses import dataclass, field
from typing import Tuple


# ----------------------------------------------------------------------
# Три режима оптимизации: веса (alpha1 — кратность, alpha2 — равномерность)
# phi(S, gamma) = alpha1 * min(n, k) / k + alpha2 * m / L_seg
# ----------------------------------------------------------------------
MODES = {
    "distribution": (0.25, 0.75),   # акцент на равномерное распределение
    "crossing":     (0.75, 0.25),   # акцент на кратность пересечений
    "balanced":     (0.50, 0.50),   # сбалансированный
}

MODE_LABELS = {
    "distribution": "распределение",
    "crossing":     "пересечение",
    "balanced":     "сбалансированный",
}

# Модели движения: этап 1 — пучок дуг, этап 2 — петляющее движение
TRAJ_LABELS = {
    "arc":        "дуги (этап 1)",
    "serpentine": "петли (этап 2)",
}


@dataclass
class Params:
    """Входные параметры математической модели (раздел 1 требований)."""
    A: Tuple[float, float] = (0.0, 0.0)     # точка старта
    B: Tuple[float, float] = (100.0, 0.0)   # точка цели
    L_max: float = 130.0                     # запас хода (предельная длина траектории)
    N: int = 6                               # число датчиков
    R: float = 14.0                          # радиус обнаружения датчика
    k: int = 3                               # требуемая кратность обнаружения
    L_seg: int = 10                          # число сегментов разбиения маршрута
    mode: str = "balanced"                   # режим оптимизации (ключ из MODES)
    traj_model: str = "arc"                  # модель движения ("arc" | "serpentine")
    T: int = 400                             # число итераций накопления выборки
    grid_step: float = 8.0                   # шаг сетки кандидатных позиций
    sigma_frac: float = 0.35                 # ширина пучка (доля предельной стрелы)
    n_points: int = 120                      # число точек дискретизации маршрута
    seed: int = 1                            # зерно ГСЧ (seed=1 воспроизводит
                                             # эталонную таблицу требований)

    def validate(self) -> None:
        """Проверка корректности входных данных."""
        import numpy as np
        ab = float(np.linalg.norm(np.subtract(self.B, self.A)))
        if self.L_max <= ab:
            raise ValueError(
                f"Запас хода L_max={self.L_max} не больше |AB|={ab:.1f}: "
                f"траектория невозможна."
            )
        if self.mode not in MODES:
            raise ValueError(f"Неизвестный режим: {self.mode}. Допустимо: {list(MODES)}")
        if self.traj_model not in TRAJ_LABELS:
            raise ValueError(f"Неизвестная модель движения: {self.traj_model}.")
        if not (1 <= self.L_seg <= 31):
            raise ValueError("L_seg должно быть в диапазоне 1..31 (битовая маска uint32).")
        if self.N < 1 or self.k < 1 or self.R <= 0:
            raise ValueError("N, k должны быть >= 1, R > 0.")
