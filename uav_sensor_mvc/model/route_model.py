# -*- coding: utf-8 -*-
"""
MODEL · Общий базовый класс для моделей вкладок 1 и 2.

`SimulationModel` (маршрут A→B) и `AreaStartModel` (старт из зоны → цель) устроены
одинаково: копят выборку случайных маршрутов и по ней жадно расставляют датчики. Раньше
десяток методов был СКОПИРОВАН в обе модели один-в-один. Здесь эти общие методы собраны
в один базовый класс — правило DRY: правишь в одном месте, работает для обеих вкладок.

Базовый класс НЕ создаёт состояние (нет своего __init__) — он лишь пользуется атрибутами,
которые заводит конкретная модель в своём __init__/reset:
    p          — параметры (config.Params)
    cache      — CoverageCache (структура покрытия по накопленным маршрутам)
    candidates — допустимые позиции датчиков (M, 2)
    sensors, trajectories, features, iteration — накопленное состояние
и методами, которые модель определяет по-своему (различаются между вкладками):
    reset(), sample_trajectory(), _anchor_idx(), evaluate().
"""
from config import MODES
from . import detection


class RouteModelBase:
    """Общее поведение моделей «накопление маршрутов → жадная расстановка датчиков».
    Подклассы задают геометрию сцены и способ порождения маршрута."""

    # ---- настройка режима ----
    def set_mode(self, mode):
        """Сменить режим оптимизации (веса критериев покрытия), см. config.MODES."""
        self.p.mode = mode

    def set_profile(self, profile):
        """Сменить профиль разброса маршрутов (амплитуда отклонений от прямой)."""
        self.p.motion_profile = profile

    @property
    def weights(self):
        """Веса критериев текущего режима (кортеж) — из config.MODES по имени режима."""
        return MODES[self.p.mode]

    # ---- расстановка датчиков по накопленной выборке ----
    def recompute_placement(self):
        """Пересчитать позиции датчиков жадным субмодулярным алгоритмом по текущей
        выборке маршрутов. Первый датчик — «якорный» у цели B (self._anchor_idx());
        остальные — по максимуму прироста покрытия, не ближе 1.35·R к якорю."""
        ai = self._anchor_idx()
        self.sensors = self.cache.greedy(self.p.N, self.weights,
                                         anchors=[ai] if ai is not None else None,
                                         anchor_sep=1.35 * self.p.R)
        return self.sensors

    def add_and_replace(self, traj, feature):
        """Добавить один маршрут в выборку и сразу пересчитать расстановку (инкрементальный
        SAA — Sample Average Approximation: чем больше выборка, тем устойчивее решение)."""
        self.trajectories.append(traj)
        self.features.append(feature)
        self.cache.add_trajectory(traj)
        self.iteration += 1
        return self.recompute_placement()

    # ---- пошаговый и пакетный прогон ----
    def step(self):
        """Одна итерация: породить маршрут и обновить расстановку. Для анимации."""
        traj, feature = self.sample_trajectory()
        sensors = self.add_and_replace(traj, feature)
        return traj, sensors

    def run_batch(self, T=None, mode=None):
        """Сразу T итераций без анимации: набрать выборку и один раз расставить датчики.
        Возвращает (позиции датчиков, показатели). T по умолчанию — из параметров."""
        if mode is not None:
            self.p.mode = mode
        T = T or self.p.T
        self.reset()
        for _ in range(T):
            traj, feature = self.sample_trajectory()
            self.trajectories.append(traj)
            self.features.append(feature)
            self.cache.add_trajectory(traj)
        self.iteration = T
        self.recompute_placement()
        return self.sensors, self.evaluate()

    # ---- сравнение режимов и онлайн-показатели ----
    def compare_modes(self):
        """Расставить датчики КАЖДЫМ режимом по одной и той же выборке и вернуть их
        показатели {режим: метрики} — для таблицы сравнения. Текущий режим восстанавливается."""
        saved = self.p.mode
        out = {}
        ai, asep = self._anchor_idx(), 1.35 * self.p.R
        anchors = [ai] if ai is not None else None
        for m in MODES:
            S = self.cache.greedy(self.p.N, MODES[m], anchors=anchors, anchor_sep=asep)
            out[m] = self.evaluate(sensors=S)
        self.p.mode = saved
        self.recompute_placement()
        return out

    def live_metrics(self, partial_traj):
        """Показатели для НЕДОЛЕТЕВШЕГО (частичного) маршрута в анимации: доля покрытия
        пройденного отрезка (%) и число обнаружений текущей расстановкой."""
        if len(partial_traj) < 2:
            return 0.0, 0
        seen = detection.continuous_coverage(partial_traj, self.sensors, self.p.R) * 100.0
        nd = detection.n_detections(partial_traj, self.sensors, self.p.R)
        return seen, nd
