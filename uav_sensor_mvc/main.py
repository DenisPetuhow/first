# -*- coding: utf-8 -*-
"""
Точка входа приложения «Размещение датчиков обнаружения БПЛА» (MVC).

Режимы запуска:
  * gui      — интерактивное приложение (по умолчанию);
  * selftest — автономный прогон ядра без графики (печать показателей);
  * render   — headless-рендер демонстрации в файлы (PNG по режимам + GIF).

Примеры:
    python main.py
    python main.py --mode selftest --T 400 --seed 1
    python main.py --ab 100 --Lmax 150 --N 6 --R 12 --k 3 --angle 10
    python main.py --traj serpentine --profile complex
    python main.py --geo --A 55.75 37.62 --B 56.20 38.40    # широта долгота

По умолчанию seed случаен — каждый прогон даёт другой результат. Передайте
--seed для воспроизводимости.
"""
import argparse

from config import Params, MODES, MODE_LABELS, MOTION_LABELS, MOTION_PROFILES


def build_params(args) -> Params:
    p = Params()
    if args.ab is not None:      p.ab_distance = args.ab
    if args.Lmax is not None:    p.L_max = args.Lmax
    if args.N is not None:       p.N = args.N
    if args.R is not None:       p.R = args.R
    if args.k is not None:       p.k = args.k
    if args.Lseg is not None:    p.L_seg = args.Lseg
    if args.angle is not None:   p.angle_step_deg = args.angle
    if args.T is not None:       p.T = args.T
    if args.seed is not None:    p.seed = args.seed
    p.mode = args.opt
    p.traj_model = args.traj
    p.motion_profile = args.profile
    if args.geo and args.A and args.B:
        import numpy as np
        from model.geometry import geo_to_local_km
        A_km, B_km, _, _ = geo_to_local_km(tuple(args.A), tuple(args.B))
        p.geo = True
        p.A_geo = tuple(args.A); p.B_geo = tuple(args.B)
        p.ab_distance = float(np.linalg.norm(B_km - A_km))
    p.validate()
    return p


# ----------------------------------------------------------------------
def run_gui(params: Params, speed: int):
    from model import SimulationModel
    from view import SimulationView
    from controller import SimulationController

    model = SimulationModel(params)
    view = SimulationView(params.A, params.B, model.corridor_outline(),
                          model.corridor_bbox(), params, speed=speed)
    SimulationController(model, view).run()


# ----------------------------------------------------------------------
def run_selftest(params: Params):
    from model import SimulationModel

    model = SimulationModel(params)
    print(f"Геометрия: |AB|={params.ab_distance:g} км, L_max={params.L_max:g} км")
    print(f"Большая полуось a=L_max/2={params.L_max/2:g}, малая b="
          f"{model.geom['b']:.1f} км")
    print(f"Предельный угол отклонения дуги: ±{model.theta_max:.1f}°")
    print(f"Кандидатных позиций в коридоре: {len(model.candidates)}")
    print(f"Профиль разброса: {MOTION_LABELS[params.motion_profile]}, "
          f"seed={params.seed}\n")

    print(f"Динамика инкрементального SAA (режим «{MODE_LABELS[params.mode]}»):")
    marks = {1, 2, 3, 10, 50, min(params.T, 200), params.T}
    model.reset()
    for t in range(1, params.T + 1):
        traj, feat = model.sample_trajectory()
        model.add_and_replace(traj, feat)
        if t in marks:
            m = model.evaluate()
            print(f"  t={t:4d} | датчиков={m['n_sensors']}/{params.N} "
                  f"| покрытие={m['avg_coverage_percent']:5.1f}% "
                  f"| пересечений={m['avg_crossings']:.2f} "
                  f"| вне зоны={m['avg_uncovered_distance']:5.1f} "
                  f"| доля≥k={m['share_meeting_k']:5.1f}%")

    print("\nИтоговое расположение датчиков:")
    for i, s in enumerate(model.sensors, 1):
        print(f"  Д{i}: ({s[0]:6.1f}, {s[1]:6.1f}) км")

    print("\nСравнение режимов на единой выборке:")
    for key, mt in model.compare_modes().items():
        print(f"  {MODE_LABELS[key]:<16}: датчиков={mt['n_sensors']} "
              f"покрытие={mt['avg_coverage_percent']:5.1f}% "
              f"пересеч.={mt['avg_crossings']:.2f} "
              f"вне зоны={mt['avg_uncovered_distance']:5.1f} "
              f"доля≥k={mt['share_meeting_k']:5.1f}%")


# ----------------------------------------------------------------------
def run_render(params: Params, out_dir: str, speed: int, n_iter: int):
    import os
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.animation import FuncAnimation, PillowWriter

    from config import THEME
    from model import SimulationModel, n_detections, continuous_coverage
    from view import draw_static, draw_sensors, draw_probable, draw_density

    os.makedirs(out_dir, exist_ok=True)
    model = SimulationModel(params)
    outline = model.corridor_outline()
    bbox = model.corridor_bbox()

    print("Пакетный рендер по режимам:")
    for mode in MODES:
        sensors, metrics = model.run_batch(mode=mode)
        paths = model.probable_paths(top=10)
        density = model.density_field()
        fig, ax = plt.subplots(figsize=(11.5, 6.4), facecolor=THEME["bg"])
        draw_static(ax, params.A, params.B, outline, bbox)
        draw_density(ax, *density)
        draw_probable(ax, paths)
        draw_sensors(ax, sensors, params.R)
        ax.set_title(f"Пакетный режим | {MODE_LABELS[mode]} | "
                     f"датчиков {metrics['n_sensors']} | "
                     f"покрытие {metrics['avg_coverage_percent']:.0f}% | "
                     f"пересеч. {metrics['avg_crossings']:.1f} | "
                     f"вне зоны {metrics['avg_uncovered_distance']:.0f} км",
                     fontsize=10, color=THEME["text"])
        ax.legend(loc="upper right", fontsize=7, framealpha=0.25,
                  labelcolor=THEME["text"], facecolor=THEME["panel"])
        path = os.path.join(out_dir, f"batch_{mode}.png")
        plt.tight_layout(); plt.savefig(path, dpi=115, facecolor=THEME["bg"])
        plt.close()
        print(f"  {MODE_LABELS[mode]:<16}: датчиков {metrics['n_sensors']} | "
              f"покрытие {metrics['avg_coverage_percent']:.0f}%  -> {path}")

    print("Рендер итеративной анимации…")
    model.reset()
    frames = []
    for it in range(1, n_iter + 1):
        traj, sensors = model.step()
        snap = sensors.copy()
        density = model.density_field()
        step = speed * (1 + it // 6)
        for j in list(range(0, len(traj), step)) + [len(traj) - 1]:
            frames.append((traj, snap, density, j, it))

    fig, ax = plt.subplots(figsize=(9.6, 5.4), facecolor=THEME["bg"])

    def update(fi):
        ax.clear()
        traj, sens, density, j, it = frames[fi]
        draw_static(ax, params.A, params.B, outline, bbox)
        draw_density(ax, *density)
        ax.plot(traj[:, 0], traj[:, 1], color=THEME["accent"], lw=1.4, alpha=0.45)
        ax.plot(traj[:j + 1, 0], traj[:j + 1, 1], color=THEME["accent"], lw=2.6)
        ax.plot(traj[j, 0], traj[j, 1], "o", color=THEME["warn"], ms=10,
                mec="white", mew=1.0)
        draw_sensors(ax, sens, params.R)
        seen = continuous_coverage(traj[:j + 1], sens, params.R) * 100 if j > 0 else 0
        nd = n_detections(traj[:j + 1], sens, params.R)
        ax.set_title(f"Итерация {it}/{n_iter} | датчиков {len(sens)}/{params.N} | "
                     f"под наблюдением {seen:.0f}% | пересечений {nd}",
                     fontsize=10, color=THEME["text"])
        return []

    anim = FuncAnimation(fig, update, frames=len(frames), interval=55, blit=False)
    gif = os.path.join(out_dir, "iterative.gif")
    anim.save(gif, writer=PillowWriter(fps=18),
              savefig_kwargs={"facecolor": THEME["bg"]}); plt.close()
    print(f"  кадров: {len(frames)} -> {gif}")


# ----------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Размещение датчиков обнаружения БПЛА (MVC).")
    ap.add_argument("--mode", choices=["gui", "selftest", "render"], default="gui")
    ap.add_argument("--opt", choices=list(MODES), default="balanced")
    ap.add_argument("--traj", choices=["arc", "serpentine"], default="arc")
    ap.add_argument("--profile", choices=list(MOTION_PROFILES), default="mixed",
                    help="профиль разброса маршрутов")
    ap.add_argument("--ab", type=float, help="расстояние |AB|, км")
    ap.add_argument("--Lmax", type=float, help="запас хода, км")
    ap.add_argument("--N", type=int); ap.add_argument("--R", type=float)
    ap.add_argument("--k", type=int); ap.add_argument("--Lseg", type=int)
    ap.add_argument("--angle", type=float, help="шаг угла веера дуг, градусы")
    ap.add_argument("--T", type=int); ap.add_argument("--seed", type=int)
    ap.add_argument("--speed", type=int, default=4)
    ap.add_argument("--iters", type=int, default=12)
    ap.add_argument("--out", default="out")
    ap.add_argument("--geo", action="store_true", help="A/B заданы в широте/долготе")
    ap.add_argument("--A", type=float, nargs=2, metavar=("LAT", "LON"))
    ap.add_argument("--B", type=float, nargs=2, metavar=("LAT", "LON"))
    args = ap.parse_args()

    params = build_params(args)
    if args.mode == "selftest":
        run_selftest(params)
    elif args.mode == "render":
        run_render(params, args.out, args.speed, args.iters)
    else:
        run_gui(params, args.speed)


if __name__ == "__main__":
    main()
