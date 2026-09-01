# -*- coding: utf-8 -*-
"""Диагностика GUI-окружения: QApplication без экрана (offscreen).

Переменная окружения ставится ВНУТРИ Python, до импорта QtWidgets, — тогда
не важно, какая оболочка (cmd/PowerShell/bash). Запуск: python -X utf8 check_env.py
"""
import os
import sys

# ОБХОД: кириллица в C:\Users\Денис ломает автоопределение пути к Qt-плагинам
# (Qt конструировал путь в неверной кодировке -> «Could not find the Qt platform
# plugin ... in ""»). Задаём путь готовой строкой от Windows (%APPDATA%) —
# строго ДО импорта QtWidgets.
_PLUGINS = os.path.join(
    os.environ["APPDATA"], "Python", "Python314",
    "site-packages", "PyQt5", "Qt5", "plugins",
)
os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = os.path.join(_PLUGINS, "platforms")
os.environ["QT_PLUGIN_PATH"] = _PLUGINS

# платформа — первым аргументом: offscreen | minimal | windows (по умолчанию offscreen)
_plat = sys.argv[1] if len(sys.argv) > 1 else "offscreen"
if _plat != "windows":
    os.environ["QT_QPA_PLATFORM"] = _plat        # строго ДО импорта QtWidgets
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
print("платформа:", _plat, flush=True)

from PyQt5.QtWidgets import QApplication
print("QtWidgets импортирован", flush=True)
app = QApplication(sys.argv[:1])
print("qt ok:", app.platformName(), flush=True)

# смоук-тест вкладки 3: создать view, отдать ему РЕАЛЬНЫЕ слои и прогнать отрисовку
import numpy as np
from view_qt.threat_view import ThreatMapView
from model.threat_grid import bbox_anchor_lonlat, bbox_lonlat_to_km, load_layers
from config import THREAT_BBOX_LONLAT, Params

lon0, lat0 = bbox_anchor_lonlat()
bbox_km = bbox_lonlat_to_km(THREAT_BBOX_LONLAT, lon0, lat0)
v = ThreatMapView(bbox_km, lon0, lat0, Params())
print("ThreatMapView создан; лимиты отрисовки:", v.MAX_LAYER_PTS, v.MAX_LAYER_POLYS,
      flush=True)

layers, src = load_layers(lon0, lat0, None, THREAT_BBOX_LONLAT)
print("источник слоёв:", src, flush=True)
for name in ("road_major", "road_local", "built_up", "river"):
    xs, ys = ThreatMapView._polys_to_xy(layers[name])
    n_lines = int(np.isnan(xs).sum())
    n_pts = int(np.isfinite(xs).sum())
    print(f"  {name:<11} линий {n_lines:>6}, точек {n_pts:>6}", flush=True)

# полный путь отрисовки слоёв, как в приложении
v.render_layers(layers, [], {"show_layers": True, "show_legend": True})
print("render_layers: OK", flush=True)
print("смоук-тест: OK")
