"""Scoring a tracked race against ground truth (shared by the stress suite and the venue simulator).

Metrics, evaluated at regular instants:

* rank accuracy: every adjacent pair of *visible* marbles on the board is in true
  order (pairs within ``tie`` metres count as level);
* the same with the 100 ms display budget broadcast graphics allow;
* phantom overtakes: a displayed pass that never happened on the track within
  that budget (the precision metric: must be zero);
* identity errors: a tracked marble more than 15 cm from where it really is;
* official result: the final order, where pairs flagged for photo-finish review
  count as ties.
"""
from __future__ import annotations

import os

import numpy as np

_DEBUG = bool(os.environ.get("MARBLETRACK_DEBUG"))


class Scorer:
    def __init__(self, ids, truth_progress, truth_finish, s_finish, tie, latency_budget=0.1, on_eval=None):
        self.ids = list(ids)
        self.truth_progress = truth_progress  # t -> (K,) true progress
        self.truth_finish = np.asarray(truth_finish, float)
        self.s_finish = s_finish
        self.tie = tie
        self.latency_budget = latency_budget
        self.on_eval = on_eval
        self.ok = self.ok_all = self.ok_lat = self.total = 0
        self.sq_err, self.n_err, self.id_errors = 0.0, 0, 0
        self.phantom = self.shown = 0
        self.truth_hist = []
        self.prev_order = None
        self.issues = []  # (t, kind, text) for diagnosis

    def ahead(self, i, j, truth):
        """Is i genuinely ahead of j (or level within the tie tolerance)?"""
        fi, fj = self.truth_finish[i], self.truth_finish[j]
        if np.isfinite(fi) and np.isfinite(fj) and truth[i] >= self.s_finish and truth[j] >= self.s_finish:
            return fi <= fj
        return truth[i] >= truth[j] - self.tie

    def evaluate(self, engine, t):
        ids = self.ids
        board = engine.leaderboard(t)
        truth = self.truth_progress(t)
        self.truth_hist.append((t, truth))
        while self.truth_hist and self.truth_hist[0][0] < t - self.latency_budget - 1e-9:
            self.truth_hist.pop(0)
        order = [ids.index(e.marble_id) for e in board]
        status_now = {ids.index(e.marble_id): e.status for e in board}
        if self.prev_order is not None:
            pos0 = {m: k for k, m in enumerate(self.prev_order)}
            for k, x in enumerate(order):
                for y in order[k + 1:]:
                    if pos0[x] > pos0[y] and "unseen" not in (status_now[x], status_now[y]):
                        self.shown += 1
                        if not any(self.ahead(x, y, tr) for _, tr in self.truth_hist):
                            self.phantom += 1
                            ex = next(e for e in board if e.marble_id == ids[x])
                            ey = next(e for e in board if e.marble_id == ids[y])
                            msg = (f"{ids[x]}({status_now[x]}, est {ex.progress_m:.3f}, true {truth[x]:.3f}) passed "
                                   f"{ids[y]}({status_now[y]}, est {ey.progress_m:.3f}, true {truth[y]:.3f})")
                            self.issues.append((round(t, 3), "phantom", msg))
                            if _DEBUG:
                                print(f"[phantom] t={t:.3f} {msg}")
        self.prev_order = order
        ok = ok_all = ok_lat = True
        seen = [e for e in board if e.status in ("finished", "tracking")]
        for pairs, visible_only in ((zip(seen, seen[1:]), True), (zip(board, board[1:]), False)):
            for a, b in pairs:
                ia, ib = ids.index(a.marble_id), ids.index(b.marble_id)
                if a.status == "finished" and b.status == "finished":
                    good = self.truth_finish[ia] <= self.truth_finish[ib] or (a.photo_finish and b.photo_finish)
                elif b.status == "finished":
                    good = False
                elif "unseen" in (a.status, b.status):
                    good = True  # before the first frame reaches the fusion host
                else:
                    good = truth[ia] >= truth[ib] - self.tie
                if visible_only:
                    if not good:
                        self.issues.append((round(t, 3), "rank", f"{a.marble_id}({a.status}) ahead of {b.marble_id}({b.status}) "
                                                                 f"truth {truth[ia]:.3f} vs {truth[ib]:.3f}"))
                        if _DEBUG:
                            print(f"[rank] t={t:.3f} {self.issues[-1][2]}")
                    ok &= good
                    if not good and not (a.status == "finished" and b.status == "finished"):
                        good = any(self.ahead(ia, ib, tr) for _, tr in self.truth_hist)
                    ok_lat &= good
                else:
                    ok_all &= good
        self.ok += ok
        self.ok_all += ok_all
        self.ok_lat += ok_lat
        self.total += 1
        for e in board:
            if e.status == "tracking":
                err = e.progress_m - truth[ids.index(e.marble_id)]
                self.sq_err += err**2
                self.n_err += 1
                if abs(err) > 0.15:
                    self.id_errors += 1
                    self.issues.append((round(t, 3), "identity", f"{e.marble_id} est {e.progress_m:.3f} true "
                                                                 f"{truth[ids.index(e.marble_id)]:.3f}"))
        if self.on_eval is not None:
            self.on_eval(t, board, truth)
        return board

    def final(self, board) -> dict:
        ids, tf = self.ids, self.truth_finish
        truth_order = [ids[i] for i in np.argsort(tf)]
        official_ok = all(
            tf[ids.index(a.marble_id)] <= tf[ids.index(b.marble_id)] or (a.photo_finish and b.photo_finish)
            for a, b in zip(board, board[1:])
        ) and all(e.status == "finished" or not np.isfinite(tf[ids.index(e.marble_id)]) for e in board)
        est = {e.marble_id: e.finish_time for e in board}
        errs = np.array([abs(est[i] - tf[ids.index(i)]) * 1e3 if est[i] is not None else
                         (0.0 if not np.isfinite(tf[ids.index(i)]) else np.inf) for i in ids])
        return dict(
            rank_accuracy=self.ok / max(1, self.total),
            rank_accuracy_all=self.ok_all / max(1, self.total),
            rank_accuracy_latency=self.ok_lat / max(1, self.total),
            phantom_overtakes=self.phantom,
            overtakes_shown=self.shown,
            final_order_correct=official_ok,
            final_order_strict=[e.marble_id for e in board] == truth_order,
            photo_finishes=sum(e.photo_finish for e in board),
            finish_err_ms_mean=float(errs.mean()),
            finish_err_ms_max=float(errs.max()),
            pos_err_cm_rms=float(np.sqrt(self.sq_err / max(1, self.n_err)) * 100),
            id_errors=int(self.id_errors),
            truth_order=truth_order,
            final_order=[e.marble_id for e in board],
        )
