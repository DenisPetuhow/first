# -*- coding: utf-8 -*-
"""VIEW (Qt) · ПРИВЯЗКА КАРТЫ-КАРТИНКИ К КООРДИНАТАМ (план 8, задача 8.5).

ЗАЧЕМ ОТДЕЛЬНЫЙ МОДУЛЬ. Раньше картинка задавалась четырьмя числами — координатами
краёв, и клалась на сцену через `ImageItem.setRect`. Этого хватает, только если карта
нарисована ровно по параллелям и меридианам и не деформирована. Проверка на карте
заказчика (Плесецк, 17 944 × 13 220, 05.09.2026) показала, что это не так:

    модель привязки                медиана ошибки     максимум
    четыре числа, введённые руками     3 660 м          5 647 м
    прямоугольник, ПОДОБРАННЫЙ            479 м          2 591 м
    аффинная, 6 параметров                224 м            439 м

Ошибка мерялась по 34 населённым пунктам, у которых координаты известны из OSM.
Видно главное: даже идеально подобранный прямоугольник даёт до 2.6 км по краям, потому
что у картинки ПЕРЕКОС ОСЕЙ — 2.4° между направлением «на восток» и «на север»
(масштабы тоже разные: 8.84 м/пиксель по X против 8.42 по Y). Так бывает после склейки
листов и экспорта из чертёжной программы. Прямоугольником такое не описывается в
принципе — нужен поворот и перекос, то есть аффинное преобразование.

ЧТО ЗДЕСЬ ЛЕЖИТ. Только математика привязки, без Qt и без чтения картинок:
  * `MapAnchor` — преобразование «пиксель картинки → километры сцены» и обратно;
  * решение по опорным точкам методом наименьших квадратов (4 или 6 параметров);
  * невязка по каждой точке — чтобы человек видел, чего стоит его привязка.

ПОЧЕМУ КИЛОМЕТРЫ, А НЕ ГРАДУСЫ. Вся вкладка живёт в локальном км-фрейме
(`model/geo_frame.py`), и картинка обязана лечь в те же километры, что векторные слои,
рельеф и тайлы. Опорные точки при этом хранятся в ГРАДУСАХ: они не зависят от участка
и переживают смену района, а километры считаются при каждом применении.

⚠️ ПРОЕКЦИЯ КАРТИНКИ РОЛИ НЕ ИГРАЕТ — проверено. Те же 34 точки подгонялись как
равнопромежуточная (224 м), Plate Carrée (224 м), Web Mercator (186 м) и
Гаусса-Крюгера СК-42 с тремя осевыми меридианами (210…215 м). Разброс между
проекциями меньше собственной погрешности опорных точек, поэтому отдельного выбора
проекции нет: аффинное преобразование вбирает в себя и поворот, и разницу масштабов.
На районе шире ~300 км это перестанет быть верным (см. план 8 §0.4).

Связанные документы: [план 8, задача 8.5](../теория/планы/8_ПЛАН_КАРТА_И_ИНТЕРФЕЙС.md),
подбор по слоям — `tools/map_fit.py`.
"""
import math

import numpy as np

# СКОЛЬКО ТОЧЕК НУЖНО КАЖДОЙ МОДЕЛИ.
#   сдвиг            — 1 точка: карта просто съехала, масштаб верный;
#   прямоугольник    — 2 точки: сдвиг и два масштаба, оси строго по сторонам света;
#   аффинная         — 3 точки: добавляются поворот и перекос.
# Больше точек — не лишние: они уходят в метод наименьших квадратов и дают невязку,
# по которой видно, что привязка удалась.
MODEL_SHIFT = "сдвиг"
MODEL_SIMILAR = "подобие"
MODEL_RECT = "прямоугольник"
MODEL_AFFINE = "аффинная"
MIN_POINTS = {MODEL_SHIFT: 1, MODEL_SIMILAR: 2, MODEL_RECT: 2, MODEL_AFFINE: 3}


class MapAnchor(object):
    """Привязка картинки: пиксели → километры сцены.

    Матрица `A` (2×3) переводит пиксель ОРИГИНАЛА картинки в километры:

        kx = A[0,0]·xpx + A[0,1]·ypx + A[0,2]
        ky = A[1,0]·xpx + A[1,1]·ypx + A[1,2]

    ⚠️ Ось Y пикселей смотрит ВНИЗ (строка 0 — север), ось Y километров — ВВЕРХ.
    Поэтому у правильной привязки `A[1,1]` отрицателен; это не ошибка знака.
    """

    def __init__(self, w, h, bbox=None, points=None, model=None):
        self.w = int(w)                     # размер картинки в пикселях оригинала
        self.h = int(h)
        self.bbox = tuple(bbox) if bbox else None   # (lon_min, lat_min, lon_max, lat_max)
        # ОПОРНЫЕ ТОЧКИ — в градусах: (xpx, ypx, lon, lat). Хранятся в пикселях
        # ОРИГИНАЛА, а не в долях: доля молча меняет смысл, если картинку пересохранят
        # другого размера, а пиксель хотя бы честно выйдет за край.
        self.points = [tuple(float(v) for v in p) for p in (points or [])]
        self.model = model
        self._cache = None                  # (lon0, lat0) -> матрица

    # ------------------------------------------------------------------
    def choose_model(self):
        """Какая модель применима при нынешнем числе точек."""
        if self.model:
            return self.model
        n = len(self.points)
        if n >= MIN_POINTS[MODEL_AFFINE]:
            return MODEL_AFFINE
        if n >= MIN_POINTS[MODEL_RECT]:
            return MODEL_RECT
        if n >= MIN_POINTS[MODEL_SHIFT]:
            return MODEL_SHIFT
        return None

    def matrix(self, lon0, lat0, to_km):
        """Матрица 2×3 «пиксель → км». `to_km(lon, lat) -> (x, y)` — из geo_frame.

        Если опорных точек нет — берётся прямоугольная привязка по `bbox` (прежнее
        поведение, ничего не ломается)."""
        key = (round(float(lon0), 9), round(float(lat0), 9))
        if self._cache and self._cache[0] == key:
            return self._cache[1]
        model = self.choose_model()
        if model is None or not self.points:
            A = self._from_bbox(lon0, lat0, to_km)
        else:
            A = self._from_points(lon0, lat0, to_km, model)
        self._cache = (key, A)
        return A

    def _from_bbox(self, lon0, lat0, to_km):
        """Прямоугольная привязка по четырём числам — как было до опорных точек."""
        if not self.bbox:
            raise ValueError("у привязки нет ни опорных точек, ни рамки")
        lo, la, ho, ha = self.bbox
        kx0, ky0 = to_km(lo, la, lon0, lat0)
        kx1, ky1 = to_km(ho, ha, lon0, lat0)
        kx0, kx1 = sorted((float(kx0), float(kx1)))
        ky0, ky1 = sorted((float(ky0), float(ky1)))
        # пиксель (0,0) — левый ВЕРХНИЙ угол, ему отвечает (kx0, ky1)
        return np.array([[(kx1 - kx0) / self.w, 0.0, kx0],
                         [0.0, -(ky1 - ky0) / self.h, ky1]], float)

    def _from_points(self, lon0, lat0, to_km, model):
        """Решение по опорным точкам методом наименьших квадратов."""
        P = np.asarray(self.points, float)
        xp, yp = P[:, 0], P[:, 1]
        kx, ky = to_km(P[:, 2], P[:, 3], lon0, lat0)
        kx = np.asarray(kx, float)
        ky = np.asarray(ky, float)
        if model == MODEL_AFFINE:
            M = np.column_stack([xp, yp, np.ones(len(P))])
            ax, *_ = np.linalg.lstsq(M, kx, rcond=None)
            ay, *_ = np.linalg.lstsq(M, ky, rcond=None)
            return np.array([ax, ay], float)
        if model == MODEL_RECT:
            # оси по сторонам света: kx зависит только от xpx, ky — только от ypx
            Mx = np.column_stack([xp, np.ones(len(P))])
            My = np.column_stack([yp, np.ones(len(P))])
            ax, *_ = np.linalg.lstsq(Mx, kx, rcond=None)
            ay, *_ = np.linalg.lstsq(My, ky, rcond=None)
            return np.array([[ax[0], 0.0, ax[1]],
                             [0.0, ay[0], ay[1]]], float)
        if model == MODEL_SIMILAR:
            # единый масштаб и поворот: форма карты сохраняется, растяжения нет
            n = len(P)
            Z, O = np.zeros(n), np.ones(n)
            M = np.vstack([np.column_stack([xp, -yp, O, Z]),
                           np.column_stack([yp, xp, Z, O])])
            sol, *_ = np.linalg.lstsq(M, np.concatenate([kx, ky]), rcond=None)
            a, b, dx, dy = sol
            return np.array([[a, -b, dx], [b, a, dy]], float)
        # СДВИГ: масштаб и поворот берём у нынешней рамки, двигаем на невязку одной точки
        A = self._from_bbox(lon0, lat0, to_km)
        gx = A[0, 0] * xp + A[0, 1] * yp + A[0, 2]
        gy = A[1, 0] * xp + A[1, 1] * yp + A[1, 2]
        A = A.copy()
        A[0, 2] += float(np.mean(kx - gx))
        A[1, 2] += float(np.mean(ky - gy))
        return A

    # ------------------------------------------------------------------
    def px_to_km(self, A, xpx, ypx):
        """Пиксель картинки → километры сцены."""
        return (A[0, 0] * xpx + A[0, 1] * ypx + A[0, 2],
                A[1, 0] * xpx + A[1, 1] * ypx + A[1, 2])

    @staticmethod
    def invert(A):
        """Обратная матрица: километры → пиксель картинки."""
        M = np.vstack([A, [0.0, 0.0, 1.0]])
        return np.linalg.inv(M)[:2, :]

    def km_to_px(self, A, kx, ky):
        """Километры сцены → пиксель картинки."""
        B = self.invert(A)
        return (B[0, 0] * kx + B[0, 1] * ky + B[0, 2],
                B[1, 0] * kx + B[1, 1] * ky + B[1, 2])

    def px_box_for_view(self, A, vx0, vx1, vy0, vy1):
        """Видимое км-окно → прямоугольник пикселей картинки (x0, y0, x1, y1).

        Берутся все четыре угла окна, а не два: при повороте картинки диагональ окна
        не совпадает с диагональю куска, и по двум углам кусок вышел бы обрезанным."""
        B = self.invert(A)
        xs, ys = [], []
        for kx, ky in ((vx0, vy0), (vx1, vy0), (vx0, vy1), (vx1, vy1)):
            xs.append(B[0, 0] * kx + B[0, 1] * ky + B[0, 2])
            ys.append(B[1, 0] * kx + B[1, 1] * ky + B[1, 2])
        return min(xs), min(ys), max(xs), max(ys)

    def corners_km(self, A):
        """Четыре угла картинки в километрах: СЗ, СВ, ЮВ, ЮЗ (по часовой)."""
        pts = ((0, 0), (self.w, 0), (self.w, self.h), (0, self.h))
        return [tuple(float(v) for v in self.px_to_km(A, x, y)) for x, y in pts]

    def bbox_km(self, A):
        """Габаритная рамка картинки в километрах (kx0, kx1, ky0, ky1)."""
        c = self.corners_km(A)
        xs = [p[0] for p in c]
        ys = [p[1] for p in c]
        return min(xs), max(xs), min(ys), max(ys)

    # ------------------------------------------------------------------
    def residuals_km(self, A, to_km, lon0, lat0):
        """Невязка по каждой опорной точке, км. Пусто, если точек нет."""
        out = []
        for xp, yp, lon, lat in self.points:
            gx, gy = self.px_to_km(A, xp, yp)
            tx, ty = to_km(lon, lat, lon0, lat0)
            out.append(float(math.hypot(gx - float(tx), gy - float(ty))))
        return out

    def describe(self, A):
        """Человеческое описание привязки: масштабы, поворот, перекос."""
        sx = math.hypot(A[0, 0], A[1, 0])          # км на пиксель вдоль оси X картинки
        sy = math.hypot(A[0, 1], A[1, 1])
        rot = math.degrees(math.atan2(A[1, 0], A[0, 0]))
        rot_y = math.degrees(math.atan2(-A[0, 1], -A[1, 1]))
        return dict(m_per_px_x=sx * 1000.0, m_per_px_y=sy * 1000.0,
                    rotation_deg=rot, skew_deg=rot - rot_y,
                    width_km=sx * self.w, height_km=sy * self.h)

    # ------------------------------------------------------------------
    def as_state(self):
        """Что сохранить в ui_state.json."""
        d = dict(w=self.w, h=self.h)
        if self.bbox:
            lo, la, ho, ha = self.bbox
            d.update(lon_min=lo, lat_min=la, lon_max=ho, lat_max=ha)
        if self.points:
            d["points"] = [list(p) for p in self.points]
        if self.model:
            d["model"] = self.model
        return d

    @classmethod
    def from_state(cls, d, w=None, h=None):
        """Восстановить привязку из сохранённого состояния (или из старых 4 чисел)."""
        if not d:
            return None
        bbox = None
        if all(d.get(k) is not None for k in ("lon_min", "lat_min", "lon_max", "lat_max")):
            bbox = (float(d["lon_min"]), float(d["lat_min"]),
                    float(d["lon_max"]), float(d["lat_max"]))
        return cls(int(d.get("w") or w or 1), int(d.get("h") or h or 1),
                   bbox=bbox, points=d.get("points") or [], model=d.get("model"))


def scene_transform(A, x0, y0, step_x, step_y, rows, flip_y=True):
    """Коэффициенты QTransform для куска картинки, показанного `ImageItem`.

    Кусок вырезан из оригинала с началом (`x0`, `y0`) и шагами `step_x`, `step_y`; в
    массиве `rows` строк. `flip_y=True` — массив перевёрнут (строка 0 внизу), как его
    отдают `load_image_rgba` и `read_detail_window`.

    ⚠️ ДВА ШАГА, А НЕ ОДИН. У куска детализации шаги по осям равны (прореживание
    целое), а у картинки, ужатой `resize`, — нет: 17 944 → 4 000 даёт 4.486 по X, а
    13 220 → 2 947 даёт 4.486 по Y лишь при точном совпадении округлений. Разница в
    сотые доли пикселя на 18 тысячах — это десятки метров на местности, поэтому шаги
    считаются по осям отдельно.

    Возвращает (m11, m12, m21, m22, dx, dy) для `QTransform(m11, m12, m21, m22, dx, dy)`:
    Qt считает x' = m11·x + m21·y + dx, y' = m12·x + m22·y + dy.
    """
    sy = -1.0 if flip_y else 1.0
    # какой строке ОРИГИНАЛА отвечает строка 0 массива
    y_first = y0 + (rows - 1) * step_y if flip_y else y0
    m11 = A[0, 0] * step_x
    m12 = A[1, 0] * step_x
    m21 = A[0, 1] * step_y * sy
    m22 = A[1, 1] * step_y * sy
    dx = A[0, 0] * x0 + A[0, 1] * y_first + A[0, 2]
    dy = A[1, 0] * x0 + A[1, 1] * y_first + A[1, 2]
    return m11, m12, m21, m22, dx, dy
