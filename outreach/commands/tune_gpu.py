"""`tune-gpu`: probe a few -ngl values and report VRAM usage.

Loads the drafting model once per value, samples nvidia-smi, and stops. Pick
the largest -ngl that stays inside your 4 GB budget without spilling hard.
"""

from __future__ import annotations

import time

from ..llm import server as srv


def _set_ngl(flags: list[str], value: int) -> list[str]:
    out = list(flags)
    if "-ngl" in out:
        out[out.index("-ngl") + 1] = str(value)
    else:
        out += ["-ngl", str(value)]
    return out


def run(ctx) -> int:
    cfg = ctx.cfg
    model_cfg = cfg.raw.models.draft
    values = [0, 10, 20, 30, 99]

    print(f"Probing -ngl values for {model_cfg.name.raw} on :{model_cfg.port.raw}")
    print(f"{'ngl':>4}  {'baseline':>9}  {'peak':>9}  {'delta':>9}  ready")
    print("-" * 48)

    results = []
    for ngl in values:
        flags = _set_ngl(list(model_cfg.flags.raw), ngl)
        baseline = srv.vram_used_mb()

        server = srv.load_one(cfg, "draft", logger=ctx.logger)
        server.flags = flags
        if not server.start(timeout_s=int(cfg.raw.models.startup_timeout_s.raw)):
            print(f"{ngl:>4}  start failed (OOM or bad flags)")
            server.stop()
            continue

        peak = baseline
        for _ in range(5):
            peak = max(peak, srv.vram_used_mb())
            time.sleep(0.4)
        delta = peak - max(baseline, 0) if baseline >= 0 and peak >= 0 else -1
        print(f"{ngl:>4}  {baseline:>9}  {peak:>9}  {delta:>9}  yes")
        results.append((ngl, baseline, peak, delta))

        server.stop()
        time.sleep(2)

    print("-" * 48)
    if results:
        best = max((r for r in results if 0 <= r[3] <= 3800), key=lambda r: r[0], default=None)
        if best:
            print(f"Recommended: -ngl {best[0]} (VRAM delta {best[3]} MiB)")
        else:
            print("No value fit under 3800 MiB; try lower -ngl or a smaller model.")
    if ctx.store is not None:
        ctx.store.log("tune-gpu", "INFO", f"results={results}")
    return 0
