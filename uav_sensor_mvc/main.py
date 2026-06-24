# -*- coding: utf-8 -*-
"""
Точка входа приложения «Размещение датчиков обнаружения БПЛА» (MVC).

Режимы запуска:
  * gui      — интерактивное приложение (по умолчанию): два режима просмотра
               (итеративный с анимацией и пакетный), три режима оптимизации,
               регулировка скорости, выбор модели движения;
  * selftest — автономный прогон ядра без графики: печать показателей и
               сравнение трёх режимов (для среды без дисплея);
  * render   — headless-рендер в файлы: batch_<mode>.png и iterative.gif
               (backend Agg, дисплей не требуется).

Примеры:
    python main.py                       # запуск интерактивного окна
    python main.py --mode selftest       # прогон ядра, печать показателей
    python main.py --mode render --T 400 # рендер картинок и анимации в ./out
    python main.py --opt crossing --N 6 --R 14 --k 3
"""
import argparse

from config import Params, MODES, MODE_LABELS


def build_params(args) -> Params:
    p = Params()
    if args.A is not None:     p.A = tuple(args.A)
    if args.B is not None:     p.B = tuple(args.B)
    if args.Lmax is not None:  p.L_max = args.Lmax
    if args.N is not None:     p.N = args.N
    if args.R is not None:     p.R = args.R
    if args.k is not None:     p.k = args.k
    if args.Lseg is not None:  p.L_seg = args.Lseg
    if args.T is not None:     p.T = args.T
    if args.seed is not None:  p.seed = args.seed
    p.mode = args.opt
    p.traj_model = args.traj
    p.validate()
    return p


# ----------------------------------------------------------------------
# Режим GUI
# ----------------------------------------------------------------------
def run_gui(params: Params, speed: int):
    from model import SimulationModel
    from view import SimulationView
    from controller import SimulationController

    model = SimulationModel(params)
    view = SimulationView(model.geom, params.A, params.B,
                          mode=params.mode, traj_model=params.traj_model,
                          speed=speed)
    controller = SimulationController(model, view)
    controller.run()


# ----------------------------------------------------------------------
# Режим selftest (без графики)
# ----------------------------------------------------------------------
def run_selftest(params: Params):
    from model import SimulationModel

    model = SimulationModel(params)
    print(f"Эллипс: a={model.geom['a']:.1f} b={model.geom['b']:.1f} "
          f"c={model.geom['c']:.1f}")
    print(f"Предельная стрела прогиба s_max={model.s_max:.2f}")
    print(f"Кандидатных позиций: {len(model.candidates)}\n")

    # инкрементальный SAA: стабилизация показателей по мере роста выборки
    print("Динамика инкрементального SAA (режим "
          f"«{MODE_LABELS[params.mode]}»):")
    marks = {1, 2, 3, 10, 50, min(params.T, 200), params.T}
    model.reset()
    for t in range(1, params.T + 1):
        traj, feat = model.sample_trajectory()
        model.add_and_replace(traj, feat)
        if t in marks:
            m = model.evaluate()
            print(f"  t={t:4d} | покрытие={m['avg_coverage_percent']:5.1f}% "
                  f"| пересечений={m['avg_crossings']:.2f} "
                  f"| вне зоны={m['avg_uncovered_distance']:5.1f} "
                  f"| доля≥k={m['share_meeting_k']:5.1f}%")

    print("\nИтоговое расположение датчиков:")
    for i, s in enumerate(model.sensors, 1):
        print(f"  Д{i}: ({s[0]:6.1f}, {s[1]:6.1f})")

    print("\nСравнение режимов на единой выборке:")
    for key, mt in model.compare_modes().items():
        print(f"  {MODE_LABELS[key]:<16}: покрытие={mt['avg_coverage_percent']:5.1f}% "
              f"пересеч.={mt['avg_crossings']:.2f} "
              f"вне зоны={mt['avg_uncovered_distance']:5.1f} "
              f"доля≥k={mt['share_meeting_k']:5.1f}%")


# ----------------------------------------------------------------------
# Режим render (headless: Agg)
# ----------------------------------------------------------------------
def run_render(params: Params, out_dir: str, speed: int, n_iter: int):
    import os
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.cm as cm
    from matplotlib.animation import FuncAnimation, PillowWriter

    from model import SimulationModel, n_detections, continuous_coverage
    from view import draw_static, draw_sensors

    os.makedirs(out_dir, exist_ok=True)
    model = SimulationModel(params)

    # --- пакетные картинки по трём режимам ---
    print("Пакетный рендер по режимам:")
    for mode in MODES:
        sensors, metrics = model.run_batch(mode=mode)
        flights = model.top_flights(top=10)
        fig, ax = plt.subplots(figsize=(11, 6))
        draw_static(ax, model.geom, params.A, params.B)
        for rank, (traj, w) in enumerate(flights):
            ax.plot(traj[:, 0], traj[:, 1], color=cm.plasma(0.15 + 0.7 * w),
                    lw=1.0 + 3.0 * w, alpha=0.85,
                    label=f"пролёт {rank + 1} (вес {w:.2f})" if rank < 5 else None)
        draw_sensors(ax, sensors, params.R)
        ax.set_title(f"Пакетный режим | {MODE_LABELS[mode]} | "
                     f"покрытие {metrics['avg_coverage_percent']:.0f}%, "
                     f"ср. пересечений {metrics['avg_crossings']:.1f}, "
                     f"вне зоны {metrics['avg_uncovered_distance']:.1f}", fontsize=10)
        ax.legend(loc="upper right", fontsize=7)
        path = os.path.join(out_dir, f"batch_{mode}.png")
        plt.tight_layout(); plt.savefig(path, dpi=110); plt.close()
        print(f"  {MODE_LABELS[mode]:<16}: покрытие {metrics['avg_coverage_percent']:.0f}% "
              f"| ≥k {metrics['share_meeting_k']:.0f}%  -> {path}")

    # --- итеративная анимация ---
    print("Рендер итеративной анимации…")
    model.reset()
    frames = []
    for it in range(1, n_iter + 1):
        traj, sensors = model.step()
        snap = sensors.copy()
        step = speed * (1 + it // 6)
        for j in range(0, len(traj), step):
            frames.append((traj, snap, j, it))
        frames.append((traj, snap, len(traj) - 1, it))

    fig, ax = plt.subplots(figsize=(11, 6))

    def update(fi):
        ax.clear()
        draw_static(ax, model.geom, params.A, params.B)
        traj, sens, j, it = frames[fi]
        ax.plot(traj[:, 0], traj[:, 1], color="#39c", lw=1.0, alpha=0.5)
        ax.plot(traj[:j + 1, 0], traj[:j + 1, 1], color="#06c", lw=2.2)
        ax.plot(traj[j, 0], traj[j, 1], "o", color="crimson", ms=9)
        draw_sensors(ax, sens, params.R)
        seen = continuous_coverage(traj[:j + 1], sens, params.R) * 100 if j > 0 else 0
        nd = n_detections(traj[:j + 1], sens, params.R)
        ax.set_title(f"Итерация {it}/{n_iter} | датчиков {len(sens)} | "
                     f"под наблюдением {seen:.0f}% | пересечений {nd}", fontsize=10)
        return []

    anim = FuncAnimation(fig, update, frames=len(frames), interval=60, blit=False)
    gif = os.path.join(out_dir, "iterative.gif")
    anim.save(gif, writer=PillowWriter(fps=20)); plt.close()
    print(f"  кадров: {len(frames)} -> {gif}")


# ----------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Размещение датчиков обнаружения БПЛА (MVC).")
    ap.add_argument("--mode", choices=["gui", "selftest", "render"],
                    default="gui", help="режим запуска (по умолчанию gui)")
    ap.add_argument("--opt", choices=list(MODES), default="balanced",
                    help="режим оптимизации")
    ap.add_argument("--traj", choices=["arc", "serpentine"], default="arc",
                    help="модель движения")
    ap.add_argument("--A", type=float, nargs=2, metavar=("X", "Y"))
    ap.add_argument("--B", type=float, nargs=2, metavar=("X", "Y"))
    ap.add_argument("--Lmax", type=float)
    ap.add_argument("--N", type=int)
    ap.add_argument("--R", type=float)
    ap.add_argument("--k", type=int)
    ap.add_argument("--Lseg", type=int)
    ap.add_argument("--T", type=int)
    ap.add_argument("--seed", type=int)
    ap.add_argument("--speed", type=int, default=4, help="точек траектории за кадр")
    ap.add_argument("--iters", type=int, default=12,
                    help="итераций для анимации в режиме render")
    ap.add_argument("--out", default="out", help="каталог вывода для режима render")
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
