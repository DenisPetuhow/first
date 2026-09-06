# -*- coding: utf-8 -*-
"""MODEL · АВТОПОДБОР ПРИВЯЗКИ КАРТЫ-КАРТИНКИ по слоям OSM (план 8, задача 8.5).

ЗАЧЕМ. Координаты краёв карты человек вводит на глаз, и ошибается на километры: замер на
карте заказчика (Плесецк, 05.09.2026) — медиана 3 660 м, максимум 5 647 м. Прочитать
рамку из файла тоже нельзя: `.png` не хранит привязки. Но у нас есть то, чего нет у
картинки, — ТОЧНЫЕ координаты объектов из OSM. По ним привязку можно найти самим.

ИДЕЯ. На топокарте населённый пункт нарисован чёрными кварталами, и в слое `built_up`
он же есть с точными координатами. Берём пятно застройки как ШАБЛОН, накладываем на
картинку и ищем сдвиг, при котором под шаблоном больше всего чёрного. Совпало у многих
пунктов сразу — значит найдено соответствие «пиксель картинки ↔ координата», а по
набору таких пар решается преобразование.

ПОЧЕМУ ИМЕННО ЗАСТРОЙКА, А НЕ РЕКИ. Первым пробовали совмещение по гидрографии
(фазовая корреляция синей маски с растеризованными реками): острота пика вышла 5.9 при
нужных 10+, и подбор увёл рамку на 30 км. Причина — болотная штриховка на топокарте
тоже синяя, и её на порядок больше, чем рек. Застройка же локальна: у каждого пункта
свой отпечаток, и ложное совпадение отсеивается соседями (RANSAC ниже).

ЧТО ПОЛУЧАЕТСЯ. На той же карте: 34 согласных пункта, медиана невязки 224 м, максимум
439 м — против 3 660 м у привязки на глаз. Подробности и таблица сравнения моделей —
в `view_qt/map_anchor.py`.

⚠️ ЭТО ПОДСКАЗКА, А НЕ ИСТИНА. Подбор опирается на данные OSM и на то, что застройка
за годы не переехала; на карте без городов (тайга, море) ему не за что зацепиться.
Поэтому он возвращает НЕВЯЗКУ вместе с ответом, а последнее слово — за человеком:
опорные точки всегда можно поставить руками.

⚠️ КАЧЕСТВО ЗАВИСИТ ОТ ТОГО, СКОЛЬКО ПУНКТОВ ВИДНО, и разница между картами доходит до
пяти раз. Замер 05.09.2026 на двух картах одного района:

    карта     размер          чёрного   найдено   согласных   невязка   охват
    Пл_пол    17 944×13 220    4.55 %     178        89        127 м    ~90 %
    Пл_об     12 042× 9 686    1.96 %      26        16        107 м    89×97 %

У второй карты пунктов вчетверо меньше — она обзорная, мельче нарисована, и чёрного на
ней вдвое меньше. Невязка ТАМ, ГДЕ ТОЧКИ ЕСТЬ, осталась хорошей, но 16 точек — это
впритык: одна ложная пара весит в шестнадцать раз больше, чем при 89, а за краем облака
привязка экстраполирует. Поэтому наружу отдаётся `cover_x`/`cover_y` (охват картинки
точками) и `cv_m` (проверка на отложенной точке) — по ним видно, когда подбору верить
нельзя и надо доставить опорные точки руками.

Модуль без Qt и без чтения файлов: на вход даётся готовая маска картинки и слои в
километрах — так его можно проверить без экрана (`tools/map_fit.py`).
"""
import numpy as np

# ── ПАРАМЕТРЫ ПОДБОРА. Числа не с потолка: подобраны на карте Плесецка (17 944 ×
# 13 220 точек, 8.8 м на пиксель) и проверены по невязке — см. шапку.

# Радиус шаблона вокруг пункта. 2.5 км — это посёлок целиком с окраинами; больше брать
# нельзя: у соседних деревень пятна сливаются, и шаблон теряет своё лицо.
TEMPLATE_R_KM = 2.5
# Меньше этого числа точек в шаблоне — пятно слишком мелкое, совпадение будет случайным.
MIN_TEMPLATE_PTS = 15
# Окно поиска: сначала широкое (ошибка привязки на глаз доходила до 5.6 км), потом узкое.
SEARCH1_KM, SEARCH2_KM = 4.5, 1.5
# Доля чёрного под шаблоном, ниже которой совпадение не считается найденным.
MIN_DARK_FRAC = 0.30
# РЕЗКОСТЬ пика: на сколько сигм максимум выделяется среди прочих позиций окна. Без
# этого условия «находились» пункты посреди сплошного леса, где чёрного много везде.
MIN_SHARPNESS = 2.5
# Согласие в RANSAC: пара считается согласной, если после преобразования промахивается
# меньше чем на столько километров.
RANSAC_TOL_KM = 0.40
RANSAC_ITERS = 300


# ── ГРУБЫЙ ПОИСК КАРТЫ ПО ВСЕМУ РАЙОНУ. Нужен, когда координат нет ВООБЩЕ: подбор по
# пятнам застройки умеет вытянуть промах до 4.5 км, но не «где-то в области 291 × 250 км».
LOCATE_CELL_KM = 0.1        # шаг сетки поиска: 100 м — попасть надо лишь в пределы 4.5 км
LOCATE_BLUR_KM = 0.6        # сглаживание обеих масок: сравниваем ПЛОТНОСТИ, а не пиксели
LOCATE_SCALES = np.arange(4.0, 16.1, 0.5)   # пробные масштабы карты, метров на пиксель


def _box_blur(a, r):
    """Скользящее среднее квадратом 2r+1 через кумулятивные суммы (быстро, без scipy)."""
    if r < 1:
        return a
    c = np.cumsum(np.cumsum(np.pad(a, ((1, 0), (1, 0))), axis=0), axis=1)
    h, w = a.shape
    y0 = np.clip(np.arange(h) - r, 0, h)
    y1 = np.clip(np.arange(h) + r + 1, 0, h)
    x0 = np.clip(np.arange(w) - r, 0, w)
    x1 = np.clip(np.arange(w) + r + 1, 0, w)
    s = (c[np.ix_(y1, x1)] - c[np.ix_(y0, x1)] - c[np.ix_(y1, x0)] + c[np.ix_(y0, x0)])
    n = ((y1 - y0)[:, None] * (x1 - x0)[None, :]).astype(np.float32)
    return (s / np.maximum(n, 1)).astype(np.float32)


def _phase_corr(a, b):
    """Фазовая корреляция: (сдвиг строк, сдвиг столбцов, острота пика в сигмах).

    Берётся именно ФАЗОВАЯ, а не обычная: она нечувствительна к тому, что у одной
    картинки чёрного вдвое больше, чем у другой, — важно совпадение РИСУНКА пятен."""
    fa = np.fft.rfft2(a - a.mean())
    fb = np.fft.rfft2(b - b.mean())
    cross = fa * np.conj(fb)
    mag = np.abs(cross)
    mag[mag < 1e-12] = 1e-12
    r = np.fft.irfft2(cross / mag, s=a.shape)
    idx = int(np.argmax(r))
    dy, dx = divmod(idx, a.shape[1])
    if dy > a.shape[0] // 2:
        dy -= a.shape[0]
    if dx > a.shape[1] // 2:
        dx -= a.shape[1]
    return dy, dx, float(r.max() / (np.std(r) + 1e-12))


# ⚠️ КАРТА НЕ МОЖЕТ БЫТЬ КРОШЕЧНОЙ. Без этого порога поиск выбирал самый мелкий масштаб
# из перебора: маленькая картинка «совпадает» с любым куском района, пик у неё острый, и
# ответ выходит уверенным и неверным. Замер 05.09.2026 на карте `ПЛ_3_ЗВ`: без порога
# найдено 4.0 м/пиксель (карта 39 км, 13 % участка) и рамка ушла на 200 км в сторону; с
# порогом — 15.4 м/пиксель, что подтвердилось подписями на самой карте.
LOCATE_MIN_FRAC = 0.25


def locate(dark, step_px, built_km, area_km, scales=None, cell_km=LOCATE_CELL_KM,
           min_frac=LOCATE_MIN_FRAC):
    """Где на участке лежит карта, если координат нет вовсе.

    `dark` — маска чёрного уменьшенной картинки, `step_px` — во сколько пикселей
    оригинала обошёлся её пиксель, `built_km` — точки застройки участка в км,
    `area_km` — (ширина, высота) участка в км.

    Возвращает `(A, м_на_пиксель, острота)`: `A` — грубая привязка «пиксель → км» без
    поворота, годная как НАЧАЛЬНОЕ приближение для `fit`.

    ⚠️ ПОЧЕМУ ПО ЗАСТРОЙКЕ, А НЕ ПО РЕКАМ. Совмещение по гидрографии на этих картах
    провалилось: болотная штриховка тоже синяя, её на порядок больше, и острота пика
    вышла 5.9 при нужных 10+. Города же дают компактные плотные пятна, узнаваемые даже
    после сглаживания до 600 м."""
    W_km, H_km = float(area_km[0]), float(area_km[1])
    nx, ny = int(W_km / cell_km), int(H_km / cell_km)
    if nx < 8 or ny < 8:
        raise ValueError("участок слишком мал для поиска")
    # карта района: плотность застройки
    ref = np.zeros((ny, nx), np.float32)
    P = np.asarray(built_km, float)
    ix = np.clip((P[:, 0] / cell_km).astype(int), 0, nx - 1)
    iy = np.clip(((H_km - P[:, 1]) / cell_km).astype(int), 0, ny - 1)   # строка 0 — север
    np.add.at(ref, (iy, ix), 1.0)
    rb = max(1, int(LOCATE_BLUR_KM / cell_km))
    ref = _box_blur(ref, rb)

    best = None
    hp, wp = dark.shape
    for m_per_px in (LOCATE_SCALES if scales is None else np.asarray(scales, float)):
        # во сколько раз ужать картинку, чтобы её пиксель стал равен ячейке поиска
        k = (m_per_px * step_px / 1000.0) / cell_km
        h2, w2 = int(hp * k), int(wp * k)
        if h2 < 8 or w2 < 8 or h2 > ny or w2 > nx:
            continue                       # карта крупнее участка — этот масштаб не тот
        if max(w2 / float(nx), h2 / float(ny)) < min_frac:
            continue                       # слишком мелко: совпадёт с чем угодно
        yi = np.clip((np.arange(h2) / k).astype(int), 0, hp - 1)
        xi = np.clip((np.arange(w2) / k).astype(int), 0, wp - 1)
        small = _box_blur(dark[np.ix_(yi, xi)], rb)
        pad = np.zeros((ny, nx), np.float32)
        pad[:h2, :w2] = small
        dy, dx, sharp = _phase_corr(ref, pad)
        if best is None or sharp > best[2]:
            best = (dy, dx, sharp, m_per_px, k)
    if best is None:
        raise ValueError("ни один пробный масштаб не подошёл")
    dy, dx, sharp, m_per_px, k = best
    # верхний левый угол карты в километрах участка
    kx0 = dx * cell_km
    ky1 = H_km - dy * cell_km
    s = m_per_px * step_px / 1000.0        # км на пиксель ОРИГИНАЛА картинки
    A = np.array([[s, 0.0, kx0], [0.0, -s, ky1]], float)
    return A, float(m_per_px), float(sharp)


def locate_candidates(dark, step_px, built_km, area_km, scales=None,
                      cell_km=LOCATE_CELL_KM, min_frac=LOCATE_MIN_FRAC):
    """Все правдоподобные положения карты — по одному на пробный масштаб.

    ⚠️ ПОЧЕМУ СПИСОК, А НЕ ОТВЕТ. Острота пика корреляции — ПЛОХОЙ судья. Замер
    05.09.2026 на карте `ПЛ_3_ЗВ`: самый острый пик (22.7) отвечал масштабу 19.7 м на
    пиксель и рамке, промахнувшейся на 200 км, тогда как верным оказался масштаб
    15.4 — у него пик был слабее. Причина: на топокарте чёрным нарисованы не только
    посёлки, но и тысячи подписей урочищ (5.45 % площади), и «плотность чёрного» плохо
    отвечает плотности застройки в OSM.

    Поэтому корреляция только ПРЕДЛАГАЕТ кандидатов, а выбирает между ними
    `locate_and_fit` — по числу пунктов, которые реально нашлись на своих местах."""
    W_km, H_km = float(area_km[0]), float(area_km[1])
    nx, ny = int(W_km / cell_km), int(H_km / cell_km)
    ref = np.zeros((ny, nx), np.float32)
    P = np.asarray(built_km, float)
    ix = np.clip((P[:, 0] / cell_km).astype(int), 0, nx - 1)
    iy = np.clip(((H_km - P[:, 1]) / cell_km).astype(int), 0, ny - 1)
    np.add.at(ref, (iy, ix), 1.0)
    rb = max(1, int(LOCATE_BLUR_KM / cell_km))
    ref = _box_blur(ref, rb)
    hp, wp = dark.shape
    out = []
    for m_per_px in (LOCATE_SCALES if scales is None else np.asarray(scales, float)):
        k = (m_per_px * step_px / 1000.0) / cell_km
        h2, w2 = int(hp * k), int(wp * k)
        if h2 < 8 or w2 < 8 or h2 > ny or w2 > nx:
            continue
        if max(w2 / float(nx), h2 / float(ny)) < min_frac:
            continue
        yi = np.clip((np.arange(h2) / k).astype(int), 0, hp - 1)
        xi = np.clip((np.arange(w2) / k).astype(int), 0, wp - 1)
        small = _box_blur(dark[np.ix_(yi, xi)], rb)
        pad = np.zeros((ny, nx), np.float32)
        pad[:h2, :w2] = small
        dy, dx, sharp = _phase_corr(ref, pad)
        s = m_per_px * step_px / 1000.0
        A = np.array([[s, 0.0, dx * cell_km], [0.0, -s, H_km - dy * cell_km]], float)
        out.append((A, float(m_per_px), float(sharp)))
    out.sort(key=lambda t: -t[2])
    return out


def locate_and_fit(dark, step_px, places_km, built_km, area_km, try_n=8):
    """Найти карту на участке и сразу подобрать привязку. -> отчёт как у `fit`.

    Кандидаты берутся у `locate_candidates`, а РЕШАЕТ число согласных пунктов: ложное
    положение даёт единицы совпадений, верное — десятки. Это тот же критерий, которым
    RANSAC отсеивает промахи внутри одного подбора, только на уровень выше."""
    cands = locate_candidates(dark, step_px, built_km, area_km)
    if not cands:
        raise ValueError("ни один пробный масштаб не подошёл под размер участка")
    w_px = dark.shape[1] * step_px
    h_px = dark.shape[0] * step_px
    best = None
    for A0, m_per_px, sharp in cands[:max(1, try_n)]:
        try:
            rep = fit(dark, step_px, A0, places_km, built_km, passes=1)
        except ValueError:
            continue
        # ⚠️ КАРТА НЕ БОЛЬШЕ УЧАСТКА. Без этой проверки побеждали кандидаты с грубым
        # масштабом: там шаблон застройки мельчает вчетверо и цепляется за любое тёмное
        # пятно, так что «согласных» набирается даже больше, чем у верного положения.
        # Замер 05.09.2026 на `ПЛ_3_ЗВ`: 68 согласных у карты 351 × 333 км, тогда как
        # весь участок 302 × 250 — то есть карта якобы шире данных, по которым найдена.
        A = rep["A"]
        w_km = float(np.hypot(A[0, 0], A[1, 0])) * w_px
        h_km = float(np.hypot(A[0, 1], A[1, 1])) * h_px
        if w_km > 1.05 * area_km[0] or h_km > 1.05 * area_km[1]:
            continue
        rep["m_per_px_guess"] = m_per_px
        rep["sharpness"] = sharp
        if best is None or rep["n_used"] > best["n_used"]:
            best = rep
    if best is None:
        raise ValueError(
            "карту не удалось найти на участке: ни одно положение не дало совпадений. "
            "Введите координаты краёв хотя бы приблизительно — с ними подбор справится.")
    # уточняем от лучшего положения полным подбором
    rep = fit(dark, step_px, best["A"], places_km, built_km)
    rep["m_per_px_guess"] = best.get("m_per_px_guess")
    rep["sharpness"] = best.get("sharpness")
    return rep


def layers_for_fit(npz_path):
    """Из кэша слоёв участка — точки пунктов и точки застройки, в километрах.

    Отдельной функцией, потому что нужна двоим: окну «Своя карта» и утилите
    `tools/map_fit.py`. Бросает `ValueError` с понятным текстом, если подбирать не по
    чему, — на карте без застройки метод не работает, и человеку надо это сказать."""
    z = np.load(npz_path, allow_pickle=True)
    places = [np.asarray(z[k], float) for k in z.files
              if k.split("__")[0] == "place_pts"]
    built = [np.asarray(z[k], float) for k in z.files
             if k.split("__")[0] == "built_up"]
    built = [b for b in built if b.ndim == 2 and b.shape[1] >= 2]
    if not places or not built:
        raise ValueError(
            "в слоях участка нет населённых пунктов или застройки — подобрать привязку "
            "по ним нельзя. Поставьте опорные точки вручную.")
    return np.vstack(places)[:, :2], np.vstack(built)[:, :2]


def dark_mask(rgb, threshold=110):
    """Маска «чёрного» на топокарте: кварталы, строения, подписи, ж/д."""
    a = np.asarray(rgb)
    return ((a[:, :, 0] < threshold) & (a[:, :, 1] < threshold)
            & (a[:, :, 2] < threshold)).astype(np.float32)


def _km_to_px(A_inv, kx, ky):
    return (A_inv[0, 0] * kx + A_inv[0, 1] * ky + A_inv[0, 2],
            A_inv[1, 0] * kx + A_inv[1, 1] * ky + A_inv[1, 2])


def _search_one(dark, step, A_inv, cx, cy, tpl_x, tpl_y, rad_px, stride):
    """Лучший сдвиг шаблона в окне ±rad_px. -> (dx, dy, доля чёрного, резкость)."""
    h, w = dark.shape
    ix, iy = int(round(cx)), int(round(cy))
    scores, offs = [], []
    for ddy in range(-rad_px, rad_px + 1, stride):
        yy = tpl_y + iy + ddy
        for ddx in range(-rad_px, rad_px + 1, stride):
            xx = tpl_x + ix + ddx
            ok = (xx >= 0) & (xx < w) & (yy >= 0) & (yy < h)
            if ok.sum() < 10:
                continue
            scores.append(float(dark[yy[ok], xx[ok]].mean()))
            offs.append((ddx, ddy))
    if not scores:
        return None
    s = np.asarray(scores)
    i = int(np.argmax(s))
    sharp = float((s[i] - s.mean()) / (s.std() + 1e-9))
    return offs[i][0], offs[i][1], float(s[i]), sharp


def find_pairs(dark, step_px, A, places_km, built_km, search_km=SEARCH1_KM,
               stride=3, min_dark=MIN_DARK_FRAC, min_sharp=MIN_SHARPNESS):
    """Найти соответствия «пиксель картинки ↔ километры» по пятнам застройки.

    `dark` — маска чёрного на УМЕНЬШЕННОЙ картинке (шаг `step_px` от оригинала);
    `A` — текущая привязка (пиксель ОРИГИНАЛА → км), от неё пляшет поиск;
    `places_km` — точки пунктов (N, 2) в км; `built_km` — точки застройки (M, 2) в км.

    Возвращает список кортежей `(xpx, ypx, kx, ky, доля, резкость)`, где пиксели — в
    координатах ОРИГИНАЛА картинки."""
    h, w = dark.shape
    # привязка для уменьшенной картинки: те же километры, пиксели мельче в step раз
    As = A.copy()
    As[:, :2] = As[:, :2] * step_px
    A_inv = np.linalg.inv(np.vstack([As, [0.0, 0.0, 1.0]]))[:2, :]
    # масштаб: сколько пикселей уменьшенной картинки в километре
    px_per_km = 1.0 / max(1e-9, np.hypot(As[0, 0], As[1, 0]))
    rad = int(max(8, min(200, search_km * px_per_km)))
    out = []
    P = np.asarray(built_km, float)
    for kx, ky in np.asarray(places_km, float)[:, :2]:
        cx, cy = _km_to_px(A_inv, kx, ky)
        if not (20 <= cx <= w - 20 and 20 <= cy <= h - 20):
            continue
        m = ((np.abs(P[:, 0] - kx) < TEMPLATE_R_KM)
             & (np.abs(P[:, 1] - ky) < TEMPLATE_R_KM))
        if int(m.sum()) < MIN_TEMPLATE_PTS:
            continue
        tx, ty = _km_to_px(A_inv, P[m, 0], P[m, 1])
        tpl_x = np.round(tx - cx).astype(int)
        tpl_y = np.round(ty - cy).astype(int)
        got = _search_one(dark, step_px, A_inv, cx, cy, tpl_x, tpl_y, rad, stride)
        if got is None:
            continue
        ddx, ddy, frac, sharp = got
        if frac < min_dark or sharp < min_sharp:
            continue
        out.append((float((cx + ddx) * step_px), float((cy + ddy) * step_px),
                    float(kx), float(ky), frac, sharp))
    return out


def solve_affine(pairs, tol_km=RANSAC_TOL_KM, iters=RANSAC_ITERS, seed=7):
    """Аффинное «пиксель → км» по парам, устойчиво к промахам (RANSAC + МНК).

    Возвращает `(A, согласные, невязки_км)`. Промах одного пункта (шаблон сел на
    соседнюю деревню) не должен утащить всю привязку — за это отвечает RANSAC."""
    P = np.asarray([p[:4] for p in pairs], float)
    n = len(P)
    if n < 3:
        raise ValueError("для аффинной привязки нужно хотя бы три пары, есть %d" % n)
    M = np.column_stack([P[:, 0], P[:, 1], np.ones(n)])
    kx, ky = P[:, 2], P[:, 3]
    rng = np.random.default_rng(seed)
    best_inl = None
    for _ in range(iters):
        idx = rng.choice(n, 3, replace=False)
        try:
            ax = np.linalg.solve(M[idx], kx[idx])
            ay = np.linalg.solve(M[idx], ky[idx])
        except np.linalg.LinAlgError:
            continue
        e = np.hypot(M @ ax - kx, M @ ay - ky)
        inl = e < tol_km
        if best_inl is None or inl.sum() > best_inl.sum():
            best_inl = inl
    if best_inl is None or best_inl.sum() < 3:
        best_inl = np.ones(n, bool)
    ax, *_ = np.linalg.lstsq(M[best_inl], kx[best_inl], rcond=None)
    ay, *_ = np.linalg.lstsq(M[best_inl], ky[best_inl], rcond=None)
    A = np.array([ax, ay], float)
    e = np.hypot(M @ ax - kx, M @ ay - ky)
    return A, best_inl, e


# ── ИМЕНА МОДЕЛЕЙ. Совпадают с `view_qt/map_anchor.py`: подобранная модель
# сохраняется вместе с точками, и по этому имени привязка потом восстанавливается.
MODEL_SIMILAR = "подобие"
MODEL_RECT = "прямоугольник"
MODEL_AFFINE = "аффинная"


def solve_model(P, model):
    """Матрица «пиксель → км» по парам `P` (N, 4) для одной из трёх моделей."""
    x, y, kx, ky = P[:, 0], P[:, 1], P[:, 2], P[:, 3]
    n = len(P)
    if model == MODEL_AFFINE:
        M = np.column_stack([x, y, np.ones(n)])
        ax, *_ = np.linalg.lstsq(M, kx, rcond=None)
        ay, *_ = np.linalg.lstsq(M, ky, rcond=None)
        return np.array([ax, ay], float)
    if model == MODEL_RECT:
        Mx = np.column_stack([x, np.ones(n)])
        My = np.column_stack([y, np.ones(n)])
        ax, *_ = np.linalg.lstsq(Mx, kx, rcond=None)
        ay, *_ = np.linalg.lstsq(My, ky, rcond=None)
        return np.array([[ax[0], 0.0, ax[1]], [0.0, ay[0], ay[1]]], float)
    # ПОДОБИЕ: единый масштаб и поворот, форма карты не искажается.
    #   kx = a·x − b·y + dx,  ky = b·x + a·y + dy
    Z, O = np.zeros(n), np.ones(n)
    M = np.vstack([np.column_stack([x, -y, O, Z]), np.column_stack([y, x, Z, O])])
    sol, *_ = np.linalg.lstsq(M, np.concatenate([kx, ky]), rcond=None)
    a, b, dx, dy = sol
    return np.array([[a, -b, dx], [b, a, dy]], float)


def cross_val_m(P, model):
    """Ошибка на ОТЛОЖЕННОЙ точке (leave-one-out), метры: медиана и максимум.

    ⚠️ ЗАЧЕМ ЭТО НУЖНО. Чем больше у модели параметров, тем лучше она ложится на свои
    же точки — и тем легче обманывает. Аффинная по трём точкам сядет ИДЕАЛЬНО и при
    этом может врать на километры везде между ними. Единственная честная проверка —
    убрать точку, решить без неё и посмотреть, куда модель эту точку положит.

    Замер на карте `Пл_об` (16 точек): подобие 11 733 м, прямоугольник 595 м, аффинная
    119 м — то есть растяжение 1.45 у той карты НАСТОЯЩЕЕ, а не выдумано моделью."""
    n = len(P)
    need = {MODEL_SIMILAR: 3, MODEL_RECT: 3, MODEL_AFFINE: 4}[model]
    if n <= need:
        return float("inf"), float("inf")
    errs = []
    for i in range(n):
        m = np.ones(n, bool)
        m[i] = False
        A = solve_model(P[m], model)
        gx = A[0, 0] * P[i, 0] + A[0, 1] * P[i, 1] + A[0, 2]
        gy = A[1, 0] * P[i, 0] + A[1, 1] * P[i, 1] + A[1, 2]
        errs.append(np.hypot(gx - P[i, 2], gy - P[i, 3]))
    return float(np.median(errs) * 1000.0), float(np.max(errs) * 1000.0)


# ⚠️ ПРЕДЕЛЫ ПРАВДОПОДОБИЯ. Карта — не резиновая: сколько-нибудь разумный экспорт может
# дать разные масштабы по осям и небольшой перекос, но не превратить квадрат в трапецию.
# Замер 05.09.2026: на карте `ПЛ_3_ЗВ`, где опорные точки собрались в пятне 43 × 31 %,
# аффинная модель «объяснила» их перекосом −5.2° и растянула карту до 397 × 331 км при
# настоящих 151 × 139. Числа при этом выглядели прекрасно: невязка 85 м. Модель, которая
# идеально ложится на свои точки и врёт втрое на остальной карте, — обычное переобучение,
# и ловится оно не невязкой, а здравым смыслом о том, какой бывает карта.
MAX_ANISOTROPY = 1.7        # во сколько раз масштабы по осям могут различаться
MAX_SKEW_DEG = 4.0          # насколько оси могут отойти от прямого угла
# Аффинная модель разрешена, только если точки покрывают хотя бы столько картинки по
# КАЖДОЙ оси: иначе шесть параметров держатся на пятне, а вся остальная карта —
# экстраполяция.
AFFINE_MIN_COVER = 0.35


def model_is_sane(A):
    """Похоже ли преобразование на настоящую карту (а не на переобучение)."""
    sx = float(np.hypot(A[0, 0], A[1, 0]))
    sy = float(np.hypot(A[0, 1], A[1, 1]))
    if sx <= 0 or sy <= 0:
        return False
    agr = max(sx / sy, sy / sx)
    rot = np.degrees(np.arctan2(A[1, 0], A[0, 0]))
    rot_y = np.degrees(np.arctan2(-A[0, 1], -A[1, 1]))
    skew = abs(((rot - rot_y) + 180.0) % 360.0 - 180.0)
    return agr <= MAX_ANISOTROPY and skew <= MAX_SKEW_DEG


def choose_model(P, cover=None):
    """Какая модель лучше описывает ЭТУ карту — по проверке на отложенной точке.

    Модель выбирается данными, а не заранее: у одной карты перекос настоящий, у другой
    его нет вовсе, и навязывать шесть параметров там, где хватает четырёх, значит
    ловить шум. При нехватке точек берётся самая простая из посильных.

    `cover` — доля картинки, накрытая точками, по осям: при узком пятне аффинная модель
    не допускается вовсе (см. `AFFINE_MIN_COVER`)."""
    allowed = [MODEL_SIMILAR, MODEL_RECT, MODEL_AFFINE]
    if cover is not None and min(cover) < AFFINE_MIN_COVER:
        allowed.remove(MODEL_AFFINE)
    best, best_err = None, None
    for model in allowed:
        med, _ = cross_val_m(P, model)
        if not np.isfinite(med):
            continue
        if not model_is_sane(solve_model(P, model)):
            continue                        # карта такой формы не бывает — не берём
        # ⚠️ ЗАПАС В ПОЛЬЗУ ПРОСТОЙ МОДЕЛИ: сложная выигрывает, только если заметно
        # лучше (на четверть), — иначе побеждает та, у которой меньше параметров.
        if best is None or med < 0.75 * best_err:
            best, best_err = model, med
    if best is None:                        # точек совсем мало — что посильно, то и берём
        best = MODEL_RECT if len(P) >= 2 else MODEL_SIMILAR
        best_err = float("nan")
    return best, best_err


def fit(dark, step_px, A0, places_km, built_km, passes=2):
    """Полный подбор: грубый проход, уточнение, выбор модели по данным.

    -> `dict` с `A` (пиксель → км), `points` (согласные пары), `model`, `median_m`,
    `max_m`, `cv_m` (проверка на отложенной точке), `n_used`, `n_found`.
    Бросает `ValueError`, если зацепиться не за что."""
    A = np.asarray(A0, float).copy()
    report = {}
    for i in range(max(1, passes)):
        wide = (i == 0)
        pairs = find_pairs(dark, step_px, A, places_km, built_km,
                           search_km=SEARCH1_KM if wide else SEARCH2_KM,
                           stride=3 if wide else 1)
        if len(pairs) < 3:
            raise ValueError(
                "нашлось %d совпадений — мало для подбора. Обычно это значит, что "
                "рамка задана слишком грубо (промах больше %.1f км) либо на карте "
                "почти нет населённых пунктов." % (len(pairs), SEARCH1_KM))
        A, inl, e = solve_affine(pairs)
        kept = [pairs[j] for j in range(len(pairs)) if inl[j]]
        P = np.asarray([p[:4] for p in kept], float)
        w_px = dark.shape[1] * step_px
        h_px = dark.shape[0] * step_px
        cover = (float(np.ptp(P[:, 0]) / max(1.0, w_px)),
                 float(np.ptp(P[:, 1]) / max(1.0, h_px)))
        model, cv = choose_model(P, cover)
        A = solve_model(P, model)
        e = np.hypot(A[0, 0] * P[:, 0] + A[0, 1] * P[:, 1] + A[0, 2] - P[:, 2],
                     A[1, 0] * P[:, 0] + A[1, 1] * P[:, 1] + A[1, 2] - P[:, 3])
        # ⚠️ ОХВАТ ОПОРНЫХ ТОЧЕК. Невязка меряется ТАМ, ГДЕ ТОЧКИ ЕСТЬ, и о пустой
        # половине карты ничего не говорит: за краем облака привязка ЭКСТРАПОЛИРУЕТ, и
        # там ошибка растёт. Поэтому охват возвращается наружу — человеку надо знать,
        # что половина тайги привязана «по вере», а не по совпадениям.
        report = dict(A=A, model=model, cv_m=cv, n_found=len(pairs), n_used=len(kept),
                      median_m=float(np.median(e) * 1000.0),
                      max_m=float(e.max() * 1000.0), points=kept,
                      cover_x=cover[0], cover_y=cover[1])
    return report
