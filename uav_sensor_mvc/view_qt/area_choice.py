# -*- coding: utf-8 -*-
"""
VIEW (Qt) · ВЫБОР ОБЛАСТИ И ПАПКИ ТАЙЛОВ — кнопки окна «Исходные данные».

ДВА ВЫБОРА, И ОНИ НЕЗАВИСИМЫ (заказчик 13.09.2026, план 10 §10.5):
  * ОБЛАСТЬ — файл `область.txt` (координаты области и района, векторные слои, рельеф).
    После выбора программа ПЕРЕЗАПУСКАЕТСЯ: область читается при импорте `config` и
    разошлась бы по ~30 местам модели и вида. Перезапуск не трогает ни расчёт, ни эталон;
  * ПАПКА ТАЙЛОВ — откуда показывается подложка и куда докачиваются тайлы. Меняется
    СРАЗУ, без перезапуска: `geomap` читает корень при каждом обращении, достаточно
    забыть уже загруженные картинки.

Оба выбора запоминаются в `geo_cache/ui_state.json` (правило 04.09.2026: последний
выбор подставляется снова) и при следующем запуске выставляются `main_qt.py` ДО
`import config` — см. `area_file.apply_saved_choice`. Разбор — теория/код/ОБЛАСТЬ_ИЗ_ФАЙЛА.md.
"""
import os
import sys

from pyqtgraph.Qt import QtCore, QtWidgets

import area_file as af
from . import geomap as gm
from . import ui_state


def _remember(**values):
    # Дописать в раздел памяти «область», не затирая соседний ключ.
    # Вход: пары ключ=значение (папка=…, тайлы=…). Отдаёт: ничего.
    sec = dict(ui_state.get(af.UI_SECTION))          # что уже сохранено в разделе
    sec.update(values)
    ui_state.save(af.UI_SECTION, sec)


def current_area_folder():
    # Папка открытой области — с неё начинается диалог выбора.
    # Вход: ничего. Отдаёт: полный путь, строка.
    from model.threat_grid import area_cache_dir
    return area_cache_dir()


def _size_text(box):
    # Размер рамки для подписи. Вход: (lon_min, lat_min, lon_max, lat_max), градусы.
    # Отдаёт: строка «302 × 250 км» (ширина по южному краю, как на осях).
    from model.geo_frame import bbox_size_km
    w, h = bbox_size_km(box)                         # ширина и высота, км
    return "%.0f × %.0f км" % (w, h)


def restart_program():
    # Запустить программу заново и закрыть текущую. Вход: ничего.
    # Отдаёт: True — новый экземпляр запущен; False — не удалось.
    frozen = getattr(sys, "frozen", False)           # True в собранном .exe
    # в .exe `sys.executable` — сама программа, имя скрипта ей не передаётся
    args = sys.argv[1:] if frozen else [os.path.abspath(sys.argv[0])] + sys.argv[1:]
    ok = QtCore.QProcess.startDetached(sys.executable, args, os.getcwd())   # новый процесс поднялся
    if ok:                                           # новый экземпляр запущен
        QtWidgets.QApplication.quit()                # — этот закрываем
    return ok


def choose_area(parent):
    # Кнопка «Область…»: файл области → проверка → подтверждение → память → перезапуск.
    # Вход: окно-родитель диалогов. Отдаёт: True — перезапуск начат; False — нет.
    # ⚠️ ВЫБИРАЕТСЯ ФАЙЛ, А НЕ ПАПКА: окно «Выбор папки» Windows файлов не показывает, и
    # человек, не видя `область.txt`, выбирал папку уровнем выше (заказчик 13.09.2026)
    path, _ = QtWidgets.QFileDialog.getOpenFileName(
        parent, "Файл области «%s»" % af.AREA_FILE_NAME,
        os.path.dirname(current_area_folder()),
        "Файл области (%s);;Текстовые файлы (*.txt);;Все файлы (*)" % af.AREA_FILE_NAME)   # выбранный файл
    if not path:                                     # окно закрыли
        return False                                 # — ничего не меняем
    # стандартное имя — запоминаем папку; иное («область (2).txt») — сам файл
    folder = (os.path.dirname(path) if os.path.basename(path) == af.AREA_FILE_NAME
              else path)
    try:
        rec = af.read_area_folder(folder)            # запись области из файла
    except af.AreaFileError as e:                    # файла нет или он битый
        QtWidgets.QMessageBox.warning(
            parent, "Область не открыта",
            "%s\n\nФормат файла — план 10 §10.5.3 или образец geo_cache/arh/%s."
            % (e, af.AREA_FILE_NAME))
        return False
    work = rec.get("work")                           # район моделирования или None
    text = ("Область: %s\nРазмер: %s\nРайон моделирования: %s\n\n"
            "Программа перезапустится и откроется на этой области. "
            "Несохранённые результаты расчёта пропадут." % (
                rec["label"], _size_text(rec["bbox"]),
                _size_text(work) if work else "не задан — задать мышью после запуска"))
    ans = QtWidgets.QMessageBox.question(parent, "Сменить область", text,
                                         QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel)
    if ans != QtWidgets.QMessageBox.Ok:              # человек передумал
        return False                                 # — ничего не меняем
    folder = os.path.abspath(folder)
    _remember(папка=folder)
    # ⚠️ ПАПКА ТАЙЛОВ ПОДБИРАЕТСЯ ПОД ОБЛАСТЬ (заказчик 20.09.2026). Память «тайлы» жила
    # отдельно от выбора области: сменил область — корень остался от прежней, и подложка
    # искалась не там. Свой выбор человека не перебиваем, пока тайлы области в нём есть.
    tiles = af.tiles_root_for_area(rec.get("tile_area"), folder,
                                   os.environ.get(af.ENV_TILES, ""))
    if tiles != os.environ.get(af.ENV_TILES, ""):    # корень сменился под эту область
        _remember(тайлы=tiles)                       # — запоминаем и выставляем ниже
        if tiles:
            os.environ[af.ENV_TILES] = tiles
        else:                                        # подошёл проектный tile_cache/
            os.environ.pop(af.ENV_TILES, None)       # — переменная больше не нужна
    # ⚠️ переменную окружения — ДО перезапуска: новый процесс наследует окружение старого, а
    # `apply_saved_choice` заданную переменную не перебивает — открылась бы прежняя область
    os.environ[af.ENV_AREA] = folder
    if not restart_program():                        # перезапуск не удался
        QtWidgets.QMessageBox.information(
            parent, "Сменить область",
            "Выбор сохранён. Закройте программу и откройте снова — область сменится.")
    return True


def choose_tile_root(parent, view, reset=False):
    # Кнопки «Папка тайлов…» / «Тайлы по умолчанию»: сменить корень кэша СРАЗУ.
    # Вход: окно-родитель, карта (с неё снять тайлы), reset — вернуть tile_cache/ проекта.
    # Отдаёт: новый корень, строка; None — окно выбора закрыли.
    if reset:                                        # «по умолчанию»
        root = ""                                    # — корень проекта, переменную уберём
    else:                                            # выбрать свою папку
        root = QtWidgets.QFileDialog.getExistingDirectory(
            parent, "Папка тайлов (внутри — <область>/<слой>/z/x/y или <слой>/z/x/y)",
            gm.cache_root())                         # выбранный корень тайлов
        if not root:                                 # окно закрыли
            return None                              # — ничего не меняем
        # ⚠️ Человек видит папку с именем области и выбирает ЕЁ, а не корень: тогда имя
        # участка добавлялось второй раз (`tile_cache/arh` + `arh` → `tile_cache/arh/arh`)
        # и докачка уходила в пустоту. Берём родителя (заказчик 20.09.2026).
        import config as _cfg                        # читаем при вызове: область могла смениться
        root = af.normalize_tiles_root(os.path.abspath(root), _cfg.THREAT_TILE_AREA)
    if root:                                         # задан свой корень
        os.environ[af.ENV_TILES] = root              # — geomap.cache_root прочтёт его сразу
    else:                                            # умолчание
        os.environ.pop(af.ENV_TILES, None)           # — переменную убираем
    _remember(тайлы=root)
    # ⚠️ ключ памяти тайлов — (область, слой, z, x, y) БЕЗ корня: не сбросить — старые
    # картинки отдавались бы из памяти, хотя папка уже другая
    gm.clear_tile_memory()
    tiles = getattr(view, "_tiles", None)            # слой поштучных тайлов карты
    if tiles is not None:                            # новый показ тайлов включён
        tiles.clear()                                # — снять картинки прежнего корня
    view._refresh_basemap(force=True)
    return gm.cache_root()
