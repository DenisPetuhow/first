<!-- КАРТА КОДА · сгенерировано tools/переносимое/codemap.py — руками не править -->

# Карта кода — указатель

> **Что это.** Оглавление ИСХОДНИКОВ: где какая функция лежит, с какой строки и
> что делает. Читается вместо того, чтобы открывать модуль целиком.

> **Как пользоваться.** Найти модуль в таблице → открыть его часть → взять номер
> строки → `Read` с `offset`. Сигнатуру брать отсюда, а не по памяти.

> **Сгенерировано** `tools/переносимое/codemap.py`, руками не править.
> Пересобрать: `python tools/переносимое/codemap.py`;
> проверить свежесть: `--check`; что устарело в документации: `--stale`.

## Характеристики

| | |
|---|---|
| файлов кода | 59 |
| строк кода | 25959 ≈ 337 тыс. токенов |
| строк карты | 1035 ≈ 9 тыс. токенов |
| частей | 1 (порог деления — 1500 строк) |

## Модули: где искать

| Модуль | Строк | ≈токенов | Классов | Функций | Где в карте |
|---|---|---|---|---|---|
| `model/threat_grid.py` | 4057 | 52741 | 2 | 162 | [КАРТА_1.md](КАРТА_1.md) стр. **10** |
| `view_qt/threat_view.py` | 4047 | 52611 | 4 | 170 | [КАРТА_1.md](КАРТА_1.md) стр. **201** |
| `controller_qt/threat_controller.py` | 1507 | 19591 | 3 | 77 | [КАРТА_1.md](КАРТА_1.md) стр. **398** |
| `model/threat_routes.py` | 1217 | 15821 | 0 | 26 | [КАРТА_1.md](КАРТА_1.md) стр. **509** |
| `tools/переносимое/make_figures.py` | 1090 | 14170 | 0 | 26 | [КАРТА_1.md](КАРТА_1.md) стр. **543** |
| `tools/gui_check.py` | 1075 | 13975 | 0 | 2 | [КАРТА_1.md](КАРТА_1.md) стр. **575** |
| `view_qt/input_window.py` | 960 | 12480 | 2 | 41 | [КАРТА_1.md](КАРТА_1.md) стр. **584** |
| `config.py` | 903 | 11739 | 1 | 3 | [КАРТА_1.md](КАРТА_1.md) стр. **641** |
| `view_qt/map_image.py` | 757 | 9841 | 1 | 24 | [КАРТА_1.md](КАРТА_1.md) стр. **662** |
| `model/trajectories.py` | 708 | 9204 | 0 | 39 | [КАРТА_1.md](КАРТА_1.md) стр. **693** |
| `model/map_fit.py` | 541 | 7033 | 0 | 16 | [КАРТА_1.md](КАРТА_1.md) стр. **738** |
| `view_qt/view_qt.py` | 514 | 6682 | 2 | 45 | [КАРТА_1.md](КАРТА_1.md) стр. **760** |
| `view_qt/geomap.py` | 481 | 6253 | 0 | 20 | [КАРТА_1.md](КАРТА_1.md) стр. **815** |
| `tools/переносимое/codemap.py` | 451 | 5863 | 0 | 15 | [КАРТА_1.md](КАРТА_1.md) стр. **841** |
| `view/view.py` | 414 | 5382 | 1 | 33 | [КАРТА_1.md](КАРТА_1.md) стр. **864** |
| `model/area_start.py` | 396 | 5148 | 1 | 28 | [КАРТА_1.md](КАРТА_1.md) стр. **911** |
| `tools/reference_cases.py` | 390 | 5070 | 0 | 6 | [КАРТА_1.md](КАРТА_1.md) стр. **953** |
| `tools/flow_check.py` | 357 | 4641 | 0 | 3 | [КАРТА_1.md](КАРТА_1.md) стр. **967** |
| `main_qt.py` | 354 | 4602 | 0 | 5 | [КАРТА_1.md](КАРТА_1.md) стр. **976** |
| `model/optimization.py` | 337 | 4381 | 1 | 11 | [КАРТА_1.md](КАРТА_1.md) стр. **987** |
| `view_qt/area_view.py` | 306 | 3978 | 1 | 18 | [КАРТА_1.md](КАРТА_1.md) стр. **1007** |

## Мелкие модули — читаются целиком

Короче 300 строк: открыть файл дешевле, чем расписывать по функциям.

| Модуль | Строк | Что внутри |
|---|---|---|
| `controller/controller.py` | 285 | `SimulationController`, `__init__`, `_make_timer`, `_toggles`, `_layers`, `on_apply`, `on_mode`, `on_traj`, `on_profile`, `on_law`, `on_speed`, `on_toggle`, … (27 всего) |
| `view_qt/map_anchor.py` | 275 | `MapAnchor`, `__init__`, `choose_model`, `matrix`, `_from_bbox`, `_from_points`, `px_to_km`, `invert`, `km_to_px`, `px_box_for_view`, `corners_km`, `bbox_km`, … (17 всего) |
| `tools/переносимое/docmap.py` | 272 | `_find_root`, `norm`, `walk_md`, `build_toc`, `apply_toc`, `verify_toc`, `check_links`, `check_structure`, `main` |
| `view_qt/basemap_mixin.py` | 249 | `_BasemapSignals`, `_BasemapTask`, `__init__`, `run`, `BasemapMixin`, `_init_basemap`, `_set_view_limits`, `_refresh_basemap`, `_on_basemap_ready`, `_fade_basemap`, `_set_scheme_visible`, `_draw_scheme`, … (15 всего) |
| `tools/переносимое/check_names.py` | 247 | `_find_root`, `collect_code_names`, `is_noise`, `docs`, `main` |
| `tools/reference_run.py` | 240 | `_fmt`, `main` |
| `tools/переносимое/check_claims.py` | 229 | `_find_root`, `docs`, `paragraphs`, `selftest`, `main` |
| `view_qt/map_vector.py` | 220 | `is_vector`, `svg_size`, `inspect_svg`, `describe_content`, `make_item`, `place_item`, `render_for_fit`, `measure_draw` |
| `model/simulation.py` | 217 | `SimulationModel`, `__init__`, `reset`, `_compute_view_bbox`, `set_traj_model`, `corridor_bbox`, `corridor_outline`, `view_bbox`, `sample_trajectory`, `_anchor_idx`, `frequent_paths`, `fan_paths`, … (15 всего) |
| `tools/переносимое/check_dead_code.py` | 206 | `find_root`, `py_files`, `collect_defs`, `collect_uses`, `unused_functions`, `unused_config`, `main` |
| `tools/area_bounds.py` | 179 | `_varint`, `_zigzag`, `_fields`, `pbf_bbox`, `raster_bounds`, `main` |
| `model/sensor_track.py` | 172 | `hungarian`, `_hungarian_square`, `match_by_type`, `total_movement` |
| `tools/download_tiles.py` | 166 | `_area_bbox`, `_area_tile_dir`, `_pad_km`, `main` |
| `model/flight_log.py` | 163 | `routes_dir`, `default_file_name`, `_route_length_km`, `save_flights`, `load_flights`, `check_compatible` |
| `view_qt/route_viewer.py` | 156 | `RouteViewerDialog`, `__init__`, `set_data`, `_current_source`, `_refresh_list`, `_show_route`, `_show_on_map`, `_reset_preview` |
| `controller_qt/area_controller.py` | 152 | `AreaStartController`, `__init__`, `_sync_geometry`, `_redraw_idle_or_last`, `_update_metrics_idle`, `on_create`, `on_route_ready`, `on_map_layer`, `on_map_offline`, `on_movement`, `on_zone`, `on_apply`, … (17 всего) |
| `tools/metrics_check.py` | 148 | `check`, `main` |
| `tools/map_fit.py` | 148 | `main` |
| `model/sensors.py` | 147 | `SensorSpec`, `active`, `label`, `ManualSensor`, `_num`, `sensor_types`, `active_small`, `min_small_radius`, `format_types_header` |
| `tools/inspect_tile_cache.py` | 142 | `_list_dirs`, `_list_files`, `print_tree`, `probe_sample_files`, `guess_xyz_or_tms`, `main` |
| `tools/переносимое/check_numbers.py` | 135 | `_find_root`, `config_values`, `docs`, `same`, `main` |
| `model/geo_frame.py` | 129 | `km_per_deg_lon`, `lonlat_to_km`, `km_to_lonlat`, `bbox_lonlat_to_km`, `bbox_size_km`, `_dms`, `format_dms`, `format_deg`, `format_rad`, `format_point` |
| `tools/tile_coverage.py` | 115 | `count_layer`, `main` |
| `model/route_model.py` | 106 | `RouteModelBase`, `set_mode`, `set_profile`, `weights`, `recompute_placement`, `add_and_replace`, `step`, `run_batch`, `compare_modes`, `live_metrics` |
| `tools/build_threat_grid.py` | 98 | `main` |
| `model/detection.py` | 80 | `_min_dist_point_to_sensors`, `n_detections`, `continuous_coverage`, `segment_coverage`, `utility`, `avg_utility` |
| `model/geometry.py` | 76 | `ellipse_geometry`, `point_in_ellipse`, `points_in_ellipse`, `auto_axes_limits`, `geo_to_local_km` |
| `view_qt/ui_common.py` | 73 | `apply_dark_theme`, `make_side_panel` |
| `view_qt/ui_state.py` | 63 | `_path`, `load_all`, `get`, `save` |
| `view_qt/analysis_window.py` | 40 | `AnalysisDialog`, `__init__`, `set_html` |
| `controller_qt/controller_qt.py` | 34 | `_QtTimer`, `__init__`, `add_callback`, `start`, `stop`, `QtSimulationController`, `_make_timer` |
| `main.py` | 29 | `main` |

**Пустые модули** (задают пакет, содержимого нет): `check_env.py`, `controller/__init__.py`, `controller_qt/__init__.py`, `model/__init__.py`, `view/__init__.py`, `view_qt/__init__.py`.

