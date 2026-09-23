# -*- coding: utf-8 -*-
"""
MODEL · Объекты защиты: список целей и розыгрыш цели налёта (план 13, задачи 13.12, 13.14).

Цель перестала быть одной. Здесь — что хранится о каждой (место и приоритет) и как
маршрут выбирает свою: МЕШКОМ без повторов подряд, а не монеткой.

⚠️ ПРИОРИТЕТ ХРАНИТСЯ, НО В РОЗЫГРЫШ НЕ ИДЁТ (заказчик 21.09.2026: «пока не влияет никак»).
Работает он пока в одном месте — у больших датчиков: кольцо у ПРИОРИТЕТНОЙ цели, при
равных долях не ставится вовсе (ответ заказчика на В13.8).
Разбор — [ЦЕЛИ_И_РОЗЫГРЫШ](../теория/код/ЦЕЛИ_И_РОЗЫГРЫШ.md).
"""
import math
from dataclasses import dataclass

import numpy as np

WEIGHT_EPS = 1e-6      # допуск суммы приоритетов: 0.1 + 0.2 в двоичной записи не ровно 0.3


@dataclass
class Target:
    """Объект защиты: место в км от якоря области и приоритет — доля от 0 до 1."""

    x_km: float                   # восток от якоря, км
    y_km: float                   # север от якоря, км
    weight: float = 1.0           # приоритет, доля; сумма по всем целям не больше 1
    sectors: bool = True          # рисовать ли у цели компас секторов (только показ)


def equal_share(n):
    """Доля каждой из n целей при раздаче поровну: 1 · 0.5 · 0.33 · 0.25 · 0.2.
    ⚠️ Округление ВНИЗ до сотых: сумма не больше 1 и сходится с тем, что видно в таблице."""
    return math.floor(100.0 / n + 1e-9) / 100.0 if n > 0 else 0.0


class TargetSet:
    """Список объектов защиты. Места — км от якоря (в сеансе он не меняется, как у точек
    старта человека); при каждом добавлении приоритеты раздаются поровну заново."""

    def __init__(self, limit):
        self.items = []               # объекты защиты по порядку постановки, список Target
        self.limit = int(limit)       # сколько целей можно поставить, шт.

    def __len__(self):
        return len(self.items)

    def points_km(self):
        """Места целей списком [(x, y), …], км — их принимают зона пролёта и контекст."""
        return [(t.x_km, t.y_km) for t in self.items]

    def add(self, x_km, y_km):
        """Поставить цель. Вход: км. Отдаёт: True — принята, False — предел исчерпан."""
        if len(self.items) >= self.limit:          # мест больше нет
            return False                           # — у каждой цели свой контекст в памяти
        self.items.append(Target(float(x_km), float(y_km)))
        self._share_equally()
        return True

    def replace_all(self, x_km, y_km):
        """Одна цель вместо всех — прежняя «цель кликом» и `target_km = (x, y)`."""
        self.items = [Target(float(x_km), float(y_km), 1.0)]

    def move(self, i, x_km, y_km):
        """Перенести цель. Вход: номер с нуля, км. Отдаёт: True — такая цель есть."""
        if not 0 <= int(i) < len(self.items):      # номера нет
            return False
        self.items[int(i)].x_km, self.items[int(i)].y_km = float(x_km), float(y_km)
        return True

    def clear(self):
        """Убрать все цели — расчёт вернётся к демо-метке участка или центру круга."""
        self.items = []

    def weight_sum(self):
        """Сумма приоритетов, доля."""
        return float(sum(t.weight for t in self.items))

    def set_weight(self, i, w):
        """Приоритет цели руками. Вход: номер с нуля, доля. Отдаёт: '' — принято, иначе
        причина отказа. ⚠️ Правило заказчика 22.09.2026: сумма НЕ БОЛЬШЕ 1, а не ровно 1."""
        if not 0 <= int(i) < len(self.items):      # номера нет
            return "нет цели №%d" % (int(i) + 1)
        w = float(w)                               # новая доля
        if not 0.0 <= w <= 1.0:                    # не доля
            return "приоритет — доля от 0 до 1"
        total = self.weight_sum() - self.items[int(i)].weight + w   # сумма после правки
        if total > 1.0 + WEIGHT_EPS:               # перевалило бы за единицу
            return "сумма приоритетов стала бы %.2f — больше 1" % total
        self.items[int(i)].weight = w
        return ""

    def priority_index(self):
        """Номер ПРИОРИТЕТНОЙ цели — её доля больше, чем у каждой другой. Отдаёт: номер
        с нуля; None — целей нет или наибольшая доля не одна (тогда цель называет человек)."""
        if not self.items:                         # целей нет
            return None
        ws = [t.weight for t in self.items]        # доли по порядку
        top = max(ws)                              # наибольшая доля
        leaders = [i for i, w in enumerate(ws) if w > top - WEIGHT_EPS]   # у кого наибольшая
        return leaders[0] if len(leaders) == 1 else None

    def _share_equally(self):
        """Раздать доли поровну — при каждом добавлении цели (правило В13.10)."""
        w = equal_share(len(self.items))           # доля каждой, округлена вниз
        for t in self.items:
            t.weight = w


class TargetBag:
    """Мешок розыгрыша цели: номера тасуются колодой, выдаются по одному, кончились —
    колода новая. Подряд одна цель не выпадает, за круг каждая выпадает ровно раз."""

    def __init__(self, ids):
        self.ids = [int(i) for i in ids]   # какие цели разыгрываются (достижимые), номера
        self.deck = []                     # что осталось в колоде этого круга, номера
        self.last = None                   # цель последнего ПРИНЯТОГО маршрута

    def peek(self, rng):
        """Чья очередь. Вход: ГПСЧ выборки (отдельного потока нет — прогон воспроизводим).
        Отдаёт: номер цели; None — разыгрывать нечего. Одна цель — без обращения к rng."""
        if not self.ids:                           # ни одна цель не достижима
            return None
        if len(self.ids) == 1:                     # ⚠️ одна цель: rng не трогаем — поток
            return self.ids[0]                     # случайных чисел тот же, что до плана 13
        if not self.deck:                          # круг кончился — новая колода
            self.deck = [self.ids[int(k)] for k in rng.permutation(len(self.ids))]
            if self.deck[0] == self.last:          # стык кругов дал бы повтор подряд
                self.deck[0], self.deck[1] = self.deck[1], self.deck[0]   # — меняем первые две
        return self.deck[0]

    def commit(self):
        """Маршрут к цели из `peek` ПРИНЯТ — снять её с колоды. Не дошёл — не звать:
        попытка повторится к той же цели, иначе недостижимая чаще пропускала бы очередь."""
        if len(self.ids) == 1:                     # одна цель — колоды нет
            self.last = self.ids[0]
        elif self.deck:                            # колода начата
            self.last = self.deck.pop(0)


def route_target_index(route, points):
    """Чья цель у маршрута: ближайшая к его КОНЦУ — маршрут кончается в ячейке цели.
    Вход: маршрут (M, 2) км, места целей [(x, y)]. Отдаёт: номер с нуля (0 — цель одна)."""
    if len(points) <= 1:                           # цель одна — спрашивать нечего
        return 0
    pts = np.asarray(points, float)                # места целей, км
    end = np.asarray(route[-1], float)             # конец маршрута, км
    return int(np.argmin(np.hypot(pts[:, 0] - end[0], pts[:, 1] - end[1])))
